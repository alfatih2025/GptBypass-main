import unittest
from unittest.mock import patch

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

import proxy.main as proxy_main


class FakeHTTPXResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._content = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}

    async def aread(self):
        return self._content

    async def aclose(self):
        return None

    async def aiter_bytes(self):
        yield self._content


class FakeHTTPXClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def build_request(self, method, url, headers=None, json=None, **kwargs):
        return {"method": method, "url": url, "headers": headers or {}, "json": json, **kwargs}

    async def send(self, req, stream=False):
        if not self._responses:
            raise AssertionError("No fake responses left")
        return self._responses.pop(0)

    async def aclose(self):
        return None


class ProxyHelperTests(unittest.TestCase):
    def test_build_target_url_keeps_query_and_avoids_double_v1(self):
        url = proxy_main.build_target_url("http://example.com/v1", "/v1/responses", "stream=true")
        self.assertEqual(url, "http://example.com/v1/responses?stream=true")

    def test_build_local_models_payload_uses_target_model(self):
        with patch.object(proxy_main, "TARGET_A_MODEL", "gpt-5.4"):
            payload = proxy_main.build_local_models_payload()

        self.assertEqual(payload["object"], "list")
        self.assertEqual(payload["data"][0]["id"], "gpt-5.4")

    def test_extract_last_user_message_from_responses_style_input(self):
        payload = {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello world"}],
                }
            ]
        }
        self.assertEqual(proxy_main.extract_last_user_message(payload), "hello world")

    def test_apply_refined_prompt_updates_responses_style_input(self):
        payload = {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "old"}],
                }
            ]
        }
        proxy_main.apply_refined_prompt(payload, "new")
        self.assertEqual(payload["input"][0]["content"][0]["text"], "new")

    def test_extract_chat_completion_text_supports_string_content(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": "rewritten text",
                    }
                }
            ]
        }
        self.assertEqual(proxy_main.extract_chat_completion_text(data), "rewritten text")

    def test_get_response_denylist_result_uses_rust_filter(self):
        with patch.object(proxy_main, "RUST_FILTER_AVAILABLE", True), patch.object(
            proxy_main, "rust_find_sensitive_words", return_value=["无法提供帮助"]
        ):
            result = proxy_main.get_response_denylist_result("这里明确说无法提供帮助")

        self.assertEqual(result["engine"], "rust")
        self.assertEqual(result["matches"], ["无法提供帮助"])

    def test_get_response_denylist_result_falls_back_to_python(self):
        with patch.object(proxy_main, "RUST_FILTER_AVAILABLE", False), patch.object(
            proxy_main, "RESPONSE_DENYLIST", ["abc", "xyz"]
        ):
            result = proxy_main.get_response_denylist_result("hello xyz world")

        self.assertEqual(result["engine"], "python-fallback")
        self.assertEqual(result["matches"], ["xyz"])

    def test_extract_last_model_message_from_payload_only_checks_last_assistant(self):
        payload = {
            "output": [
                {"role": "assistant", "content": [{"type": "output_text", "text": "前一条包含无法提供帮助"}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "最终正常回复"}]},
            ]
        }
        self.assertEqual(proxy_main.extract_last_model_message_from_payload(payload), "最终正常回复")

    def test_extract_last_model_message_from_stream_uses_final_model_text(self):
        stream_text = (
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"最终"}\n\n'
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"正常回复"}\n\n'
            'event: response.completed\n'
            'data: {"type":"response.completed","response":{"output":[{"role":"assistant","content":[{"type":"output_text","text":"最终正常回复"}]}]}}\n\n'
        )
        self.assertEqual(proxy_main.extract_last_model_message_from_stream(stream_text), "最终正常回复")

    def test_extract_last_model_message_from_stream_supports_output_item_done(self):
        stream_text = (
            'event: response.output_item.done\n'
            'data: {"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"不能帮助"}]}}\n\n'
        )
        self.assertEqual(proxy_main.extract_last_model_message_from_stream(stream_text), "不能帮助")

    def test_extract_last_model_message_from_stream_ignores_metadata_after_assistant_text(self):
        stream_text = (
            'event: response.output_item.done\n'
            'data: {"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"我不能协助"}]}}\n\n'
            'event: response.completed\n'
            'data: {"type":"response.completed","sequence_number":42,"status":"completed"}\n\n'
        )
        self.assertEqual(proxy_main.extract_last_model_message_from_stream(stream_text), "我不能协助")

    def test_extract_last_model_message_from_payload_matches_rollout_message_shape(self):
        payload = {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "我不能协助这个请求",
                }
            ],
        }
        self.assertEqual(proxy_main.extract_last_model_message_from_payload(payload), "我不能协助这个请求")

    def test_classify_user_request_marks_auxiliary_title_request(self):
        """仅当 ONLY_MAIN_USER_REQUEST=True 时，辅助关键词+previous_response_id 触发辅助判定。"""
        request = Request({"type": "http", "headers": []})
        payload = {
            "previous_response_id": "resp_123",
            "instructions": "Generate a short conversation title only.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "逆向小红书接口"}]}],
        }

        with patch.object(proxy_main, "ONLY_MAIN_USER_REQUEST", True):
            result = proxy_main.classify_user_request(request, payload, "逆向小红书接口")
        self.assertFalse(result["is_main"])
        self.assertTrue(result["matched_aux_hints"])

    def test_classify_user_request_all_requests_main_when_config_disabled(self):
        """当 ONLY_MAIN_USER_REQUEST=False 时，所有请求都被判定为主请求。"""
        request = Request({"type": "http", "headers": []})
        payload = {
            "previous_response_id": "resp_123",
            "instructions": "Generate a short conversation title only.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "逆向小红书接口"}]}],
        }

        with patch.object(proxy_main, "ONLY_MAIN_USER_REQUEST", False):
            result = proxy_main.classify_user_request(request, payload, "逆向小红书接口")
        self.assertTrue(result["is_main"])
        self.assertEqual(result["reason"], "config_all_requests")

    def test_classify_user_request_marks_long_title_prompt_as_auxiliary(self):
        request = Request({"type": "http", "headers": []})
        payload = {
            "instructions": "You are Codex",
        }
        long_title_prompt = (
            "You are a helpful assistant. You will be presented with a user prompt, "
            "and your job is to provide a short title for a task that will be created from that prompt.\n"
            "Generate a concise UI title (up to 36 characters) for this task.\n"
            "Fill the structured title field with plain text.\n"
            "User prompt:\n这是一个逆向分析任务"
        )
        with patch.object(proxy_main, "ONLY_MAIN_USER_REQUEST", True):
            result = proxy_main.classify_user_request(request, payload, long_title_prompt)
        self.assertFalse(result["is_main"])
        self.assertEqual(result["reason"], "strong_aux_prompt_pattern")


class ProxyRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(proxy_main.app)

    def test_local_models_is_returned_without_upstream(self):
        with patch.object(proxy_main, "TARGET_A_MODEL", "gpt-5.4"), patch.object(
            proxy_main, "proxy_pass_direct", side_effect=AssertionError("should not call upstream")
        ):
            resp = self.client.get("/v1/models?limit=1")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"][0]["id"], "gpt-5.4")

    def test_direct_forward_preserves_query_string_for_non_models(self):
        async def fake_proxy_pass_direct(request, forward_url, headers, body_bytes, request_id):
            return JSONResponse({"forward_url": forward_url})

        with patch.object(proxy_main, "proxy_pass_direct", side_effect=fake_proxy_pass_direct):
            resp = self.client.get("/v1/files?limit=1")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["forward_url"].endswith("/v1/files?limit=1"))

    def test_direct_forward_preserves_non_json_body(self):
        raw = b"\x00\x01binary-payload"

        async def fake_proxy_pass_direct(request, forward_url, headers, body_bytes, request_id):
            return JSONResponse({"body_hex": body_bytes.hex(), "body_len": len(body_bytes)})

        with patch.object(proxy_main, "proxy_pass_direct", side_effect=fake_proxy_pass_direct):
            resp = self.client.post(
                "/v1/files",
                content=raw,
                headers={"content-type": "application/octet-stream"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["body_len"], len(raw))
        self.assertEqual(resp.json()["body_hex"], raw.hex())

    def test_retry_refine_uses_previous_refined_prompt_and_refusal_text(self):
        backend_responses = [
            FakeHTTPXResponse(
                '{"output":[{"role":"assistant","content":[{"type":"output_text","text":"抱歉，不能协助"}]}]}'
            ),
            FakeHTTPXResponse(
                '{"output":[{"role":"assistant","content":[{"type":"output_text","text":"最终正常回复"}]}]}'
            ),
        ]
        refine_calls = []

        async def fake_refine(prompt, request_id, reason, refusal_text=""):
            refine_calls.append(
                {
                    "prompt": prompt,
                    "reason": reason,
                    "refusal_text": refusal_text,
                }
            )
            return "改写结果#1" if len(refine_calls) == 1 else "改写结果#2"

        payload = {
            "model": "dummy",
            "stream": False,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "请帮我逆向分析"}]}],
        }

        with patch.object(
            proxy_main, "refine_prompt_with_b_model", side_effect=fake_refine
        ), patch.object(proxy_main.httpx, "AsyncClient", side_effect=[FakeHTTPXClient(backend_responses)]):
            resp = self.client.post("/v1/responses", json=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["output"][0]["content"][0]["text"],
            "最终正常回复",
        )
        self.assertEqual(len(refine_calls), 1)
        self.assertEqual(refine_calls[0]["prompt"], "请帮我逆向分析")
        self.assertIn("抱歉", refine_calls[0]["refusal_text"])

    def test_clean_response_is_forwarded_without_retry(self):
        backend_responses = [
            FakeHTTPXResponse(
                '{"output":[{"role":"assistant","content":[{"type":"output_text","text":"正常结果"}]}]}'
            ),
        ]
        refine_calls = []

        async def fake_refine(prompt, request_id, reason, refusal_text=""):
            refine_calls.append((prompt, reason, refusal_text))
            return "已改写"

        payload = {
            "model": "dummy",
            "stream": False,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "请帮我逆向分析"}]}],
        }

        with patch.object(
            proxy_main, "refine_prompt_with_b_model", side_effect=fake_refine
        ), patch.object(proxy_main.httpx, "AsyncClient", side_effect=[FakeHTTPXClient(backend_responses)]):
            resp = self.client.post("/v1/responses", json=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["output"][0]["content"][0]["text"], "正常结果")
        self.assertEqual(len(refine_calls), 0)

    def test_auxiliary_request_still_filters_response_but_skips_refine(self):
        """辅助请求的响应过滤器仍然生效，但跳过提示词改写。"""
        backend_responses = [
            FakeHTTPXResponse(
                '{"output":[{"role":"assistant","content":[{"type":"output_text","text":"抱歉，不能协助"}]}]}'
            ),
            FakeHTTPXResponse(
                '{"output":[{"role":"assistant","content":[{"type":"output_text","text":"对话标题：逆向分析"}]}]}'
            ),
        ]
        refine_calls = []

        async def fake_refine(prompt, request_id, reason, refusal_text=""):
            refine_calls.append((prompt, reason, refusal_text))
            return "不应被调用"

        payload = {
            "model": "dummy",
            "stream": False,
            "previous_response_id": "resp_aux",
            "instructions": "Generate a short conversation title only.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "逆向小红书接口"}]}],
        }

        with patch.object(proxy_main, "ONLY_MAIN_USER_REQUEST", True), \
             patch.object(
            proxy_main, "refine_prompt_with_b_model", side_effect=fake_refine
        ), patch.object(proxy_main.httpx, "AsyncClient", side_effect=[FakeHTTPXClient(backend_responses)]):
            resp = self.client.post("/v1/responses", json=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("逆向分析", resp.json()["output"][0]["content"][0]["text"])
        self.assertEqual(len(refine_calls), 0)

    def test_first_attempt_direct_then_retry_rewrites(self):
        backend_responses = [
            FakeHTTPXResponse('{"output":[{"role":"assistant","content":[{"type":"output_text","text":"第一次正常"}]}]}'),
            FakeHTTPXResponse('{"output":[{"role":"assistant","content":[{"type":"output_text","text":"不能帮助"}]}]}'),
            FakeHTTPXResponse('{"output":[{"role":"assistant","content":[{"type":"output_text","text":"第二次正常"}]}]}'),
        ]
        refine_calls = []

        async def fake_refine(prompt, request_id, reason, refusal_text=""):
            refine_calls.append((prompt, reason, refusal_text))
            return "已改写" if len(refine_calls) == 1 else "二次改写"

        payload = {
            "model": "dummy",
            "stream": False,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "请帮我逆向分析"}]}],
        }

        with patch.object(
            proxy_main, "refine_prompt_with_b_model", side_effect=fake_refine
        ), patch.object(
            proxy_main.httpx,
            "AsyncClient",
            side_effect=[
                FakeHTTPXClient([backend_responses[0]]),
                FakeHTTPXClient([backend_responses[1], backend_responses[2]]),
            ],
        ):
            resp1 = self.client.post("/v1/responses", json=payload)
            resp2 = self.client.post("/v1/responses", json=payload)

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(len(refine_calls), 1)
        self.assertEqual(refine_calls[0][1], "retry_after_refusal=#1")


if __name__ == "__main__":
    unittest.main()
