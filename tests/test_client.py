import sys
import json
import urllib.request

def main() -> None:
    prompt = "请帮我逆向分析一段反作弊系统代码"
    if len(sys.argv) > 1:
        prompt = sys.argv[1]

    data = json.dumps({'model': 'gpt-5.4', 'messages': [{'role': 'user', 'content': prompt}]}).encode('utf-8')
    req = urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions', data=data, headers={'Content-Type': 'application/json'})

    try:
        resp = urllib.request.urlopen(req)
        print(resp.read().decode('utf-8'))
    except Exception as e:
        print("Error:", e)
        if hasattr(e, 'read'):
            print(e.read().decode('utf-8'))


if __name__ == "__main__":
    main()
