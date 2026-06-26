import argparse
import os
import sys
from pathlib import Path

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GPT5.4_JMP CLI")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="启动代理服务")
    serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8999, help="监听端口，默认 8999")
    serve_parser.add_argument("--workers", type=int, default=1, help="worker 数量，默认 1")
    serve_parser.add_argument(
        "--config",
        default="",
        help="指定配置文件路径；未提供时使用默认 config.json",
    )

    return parser


def serve(args: argparse.Namespace) -> None:
    config_path = str(args.config).strip()
    if config_path:
        resolved_config = Path(config_path).expanduser().resolve()
        os.environ["GPT54_JMP_CONFIG"] = str(resolved_config)
        print(f"[CLI] 配置文件: {resolved_config}")
    else:
        print("[CLI] 配置文件: 默认运行目录下的 config.json")

    print(f"[CLI] 启动代理: http://{args.host}:{args.port}")
    print(f"[CLI] workers={args.workers}")

    uvicorn.run(
        "proxy.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=False,
    )


def main() -> None:
    parser = build_parser()
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-"):
        args = parser.parse_args(argv)
    else:
        args = parser.parse_args(["serve", *argv])

    if args.command == "serve":
        serve(args)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
