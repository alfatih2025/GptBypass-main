# GptBypass

**A local proxy for OpenAI-compatible API that detects model refusals and automatically rewrites prompts for retry.**

[中文说明](#中文说明)

---

## Features

- **Transparent proxy** — Forwards all `/v1/*` requests to your target model endpoint
- **Fake `/v1/models`** — Returns a local model list so clients can check connectivity
- **Refusal detection** — Scans the model's last reply against a configurable denylist
- **Automatic prompt rewriting** — When refusal is detected, an auxiliary model rewrites the last user message and retries
- **GUI & CLI** — Comes with both a desktop GUI (CustomTkinter) and a CLI mode
- **Rust-accelerated filtering** — Optional Aho-Corasick matcher via PyO3 for high-performance keyword detection
- **Streaming support** — Fully handles SSE streaming responses with buffered analysis

## How It Works

```
Client  →  GptBypass Proxy  →  Target Model
                ↓                     ↓
         Denylist Check      Model Response
                ↓                     ↓
         Hit? → Rewrite      Clean → Forward
         Prompt via Aux       to Client
         Model → Retry
```

1. Client sends a request through the proxy
2. First attempt goes straight to the target model (no rewrite)
3. Proxy buffers and analyzes the response
4. If the response matches any denial keyword → discard, rewrite the prompt via the auxiliary model, and retry
5. If the response is clean → forward to client

## Quick Start

### Prerequisites

- Python 3.10+
- A target model API endpoint (OpenAI, local, or any compatible API)
- An auxiliary model API endpoint for prompt rewriting

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/GptBypass.git
cd GptBypass
pip install -r requirements.txt
```

### Configuration

Copy the example config and fill in your API details:

```bash
cp config.example.json config.json
```

Edit `config.json`:

```json
{
    "target_model": {
        "model": "gpt-5.4",
        "message_type": "responses",
        "reasoning_depth": "high",
        "baseurl": "http://your-target-api/v1",
        "apikey": "your-target-api-key"
    },
    "optimization_model": {
        "model": "your-aux-model",
        "baseurl": "https://openrouter.ai/api/v1",
        "apikey": "your-aux-api-key",
        "max_retries": 5
    },
    "response_filter": {
        "denylist": ["抱歉", "不能协助", "I'm sorry", "I cannot assist"]
    }
}
```

> **⚠️ Never commit your `config.json` with real API keys.** It is already in `.gitignore`.

### Run

**GUI mode:**

```bash
python gui_app.py
```

**CLI mode:**

```bash
# Start proxy server
python cli_app.py serve --host 127.0.0.1 --port 8999

# Or use the simple runner
python run_proxy.py --host 127.0.0.1 --port 8999
```

Then point your client (Cursor, Continue, etc.) to `http://127.0.0.1:8999/v1`.

## Configuration Reference

### `target_model`

The model you ultimately want to use.

| Field | Description |
|-------|-------------|
| `model` | Model name forwarded to the target backend |
| `message_type` | API type: `responses` or `chat.completions` |
| `reasoning_depth` | Reasoning effort level (e.g. `high`, `medium`, `low`) |
| `baseurl` | Target API endpoint URL (include `/v1`) |
| `apikey` | Target API key (leave empty if not needed) |

### `optimization_model`

The auxiliary model used to rewrite prompts when refusal is detected.

| Field | Description |
|-------|-------------|
| `model` | Auxiliary model name |
| `baseurl` | Auxiliary API endpoint |
| `apikey` | Auxiliary API key |
| `system_prompt` | System prompt for the rewriting model |
| `log_full_refined_content` | Log full rewritten prompts (`true`/`false`) |
| `only_main_user_request` | Only rewrite main requests, skip auxiliary ones like title generation |
| `max_retries` | Maximum retry attempts after refusal |

### `response_filter`

| Field | Description |
|-------|-------------|
| `denylist` | List of keywords/phrases. If the model's last reply contains any, it triggers a rewrite + retry |

## Rust Filter (Optional)

For better keyword matching performance, build the native Rust module:

```bash
cd rust_filter
pip install maturin
maturin develop --release
```

This provides Aho-Corasick based matching via PyO3. The proxy automatically falls back to Python if the Rust module is unavailable.

## Building Standalone Executables

**CLI executable:**
```powershell
.\build_exe.ps1
```

**GUI executable:**
```powershell
.\build_gui_exe.ps1
```

Outputs are placed in `dist/` and `dist_gui/` respectively.

## Project Structure

```
GptBypass/
├── proxy/
│   ├── __init__.py
│   └── main.py              # Core proxy engine (FastAPI)
├── rust_filter/
│   ├── src/lib.rs            # Rust Aho-Corasick filter (PyO3)
│   └── Cargo.toml
├── gui_app.py                # Desktop GUI (CustomTkinter)
├── cli_app.py                # CLI entry point
├── run_proxy.py              # Simple proxy runner
├── app_defaults.py           # Default configuration
├── config.example.json       # Example config (no secrets)
├── guide.html                # Usage guide (Chinese)
├── app_icon.ico              # Application icon
├── ico.png                   # Icon PNG
├── tests/
│   ├── test_app.py           # Unit & integration tests
│   └── test_client.py        # Simple client test
├── build_exe.ps1             # Build CLI exe
├── build_gui_exe.ps1         # Build GUI exe
├── requirements.txt
├── LICENSE
└── README.md
```

## API

The proxy exposes these HTTP endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET/HEAD | `/v1/models` | Returns a fake model list for client compatibility |
| * | `/{path:path}` | Proxies everything else to the target model |

### Request Classification Headers

You can control bypass behavior per-request using custom headers:

| Header | Value | Effect |
|--------|-------|--------|
| `x-jmp-main-request` | `true` | Force treat as main request (always rewrite on refusal) |
| `x-jmp-aux-request` | `true` | Force treat as auxiliary request (skip rewrite) |
| `x-jmp-skip-optimize` | `true` | Skip optimization entirely |

## Logs

Default log file: `proxy.log` (alongside the executable)

Common log patterns:
- `请求进入` — New request received
- `首轮直发目标模型` — First attempt, sent directly
- `命中过滤 | #1` — Response matched denylist
- `改写 | #2` — Starting rewrite retry
- `放行 | stream=True` — Clean response forwarded to client

## License

[MIT](LICENSE)

---

## 中文说明

一个基于 FastAPI 的 OpenAI 兼容 API 本地代理工具，主要用于自动检测模型拒答并改写提示词重试。

### 核心流程

1. 客户端请求发往本地代理
2. 首轮请求原样转发目标模型
3. 代理检测模型回复是否命中拒答关键词
4. 命中 → 丢弃该回复，调用辅助模型改写用户提示词后重试
5. 未命中 → 正常返回给客户端

### 快速开始

```bash
pip install -r requirements.txt
cp config.example.json config.json
# 编辑 config.json 填入你的 API 地址和密钥
python gui_app.py   # 图形界面
python cli_app.py serve  # 命令行
```

### 配置说明

- **`target_model`** — 目标模型接口配置（最终请求转发目标）
- **`optimization_model`** — 辅助改写模型配置（拒答后用于改写提示词）
- **`response_filter.denylist`** — 拒答关键词列表，命中任一关键词即触发改写重试

> 详见 `guide.html` 或复制 `config.example.json` 查看完整配置项。
# GptBypass-main
