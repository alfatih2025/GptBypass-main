import argparse
import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="GPT5.4_JMP Proxy")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8999, help="监听端口，默认 8999")
    parser.add_argument("--workers", type=int, default=1, help="worker 数量，默认 1")
    args = parser.parse_args()

    uvicorn.run(
        "proxy.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=False,
    )


if __name__ == "__main__":
    main()
