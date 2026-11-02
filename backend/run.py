import argparse
import socket
from pathlib import Path

import uvicorn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动 AskData 页面和后端接口")
    parser.add_argument("--port", type=int, default=8000, help="服务端口，默认 8000")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.exit(1, "端口必须在 1 到 65535 之间。\n")
    frontend = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html"
    if not frontend.is_file():
        parser.exit(1, "缺少前端构建文件。请先在 frontend 目录执行 npm run build，再运行本命令。\n")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.exit(1, f"端口 {args.port} 已被占用。若 AskData 已启动，请直接打开 http://127.0.0.1:{args.port}/\n"
                           f"或者运行 python run.py --port {args.port + 1 if args.port < 65535 else 8001} 使用其他端口。\n")
    print(f"AskData 页面：http://127.0.0.1:{args.port}/", flush=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
