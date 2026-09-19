"""MyQuant 应用总入口（switch-mcp-http-transport 2.1）。

一键启动进程组：API(:8000) + MCP streamable-http server(:8800)。
启动即打印服务地址与 MCP 宿主接入指引；Ctrl-C / SIGTERM 优雅同停。

用法：python run.py [--api-port 8000] [--mcp-port 8800] [--profile research]
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

# 后台重定向时行缓冲（启动横幅即时可见——块缓冲会吞掉）
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_http_ok(url: str, timeout_s: float = 30.0) -> bool:
    import httpx

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0, trust_env=False)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def _wait_tcp(port: int, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _port_in_use(port):
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="MyQuant 应用总入口")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--mcp-port", type=int, default=8800)
    parser.add_argument("--profile", choices=["readonly", "research"],
                        default="research",
                        help="MCP 工具档位（默认 research——本机单用户研究形态）")
    args = parser.parse_args()

    # 端口预检：占用即显式失败（不静默换端口——宿主会连错）
    for label, port in (("API", args.api_port), ("MCP", args.mcp_port)):
        if _port_in_use(port):
            print(f"[启动失败] {label} 端口 {port} 已被占用。"
                  f"若为既有实例请先停止（或检查：lsof -i :{port}）；"
                  f"或用 --{'api' if label == 'API' else 'mcp'}-port 换端口。",
                  file=sys.stderr)
            return 1

    env = os.environ.copy()
    env["MYQUANT_MCP_PROFILE"] = args.profile
    env["MYQUANT_MCP_PORT"] = str(args.mcp_port)
    env["MYQUANT_API_PORT"] = str(args.api_port)

    api_proc = subprocess.Popen(
        [PYTHON, "-m", "api"], cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mcp_proc = subprocess.Popen(
        [PYTHON, "-m", "mcpserver.server"], cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    stopping = False

    def _shutdown(signum, _frame):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for p in (api_proc, mcp_proc):
            if p.poll() is None:
                p.terminate()
        for p in (api_proc, mcp_proc):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"MyQuant 启动中…（API :{args.api_port} / MCP :{args.mcp_port}）", flush=True)
    api_ok = _wait_http_ok(
        f"http://127.0.0.1:{args.api_port}/api/data/available-sources")
    mcp_ok = _wait_tcp(args.mcp_port)

    if not (api_ok and mcp_ok):
        print(f"[启动失败] 健康等待超时：API={'OK' if api_ok else '未就绪'}，"
              f"MCP={'OK' if mcp_ok else '未就绪'}。日志见子进程输出。",
              file=sys.stderr)
        _shutdown(signal.SIGTERM, None)
        return 1

    print()
    print("=" * 62, flush=True)
    print(f"  MyQuant 已就绪", flush=True)
    print(f"    API            http://127.0.0.1:{args.api_port}", flush=True)
    print(f"    MCP (HTTP)     http://127.0.0.1:{args.mcp_port}/mcp"
          f"   [档位: {args.profile}]")
    print()
    print("  MCP 宿主接入（一次性配置，任何 MCP 客户端）：", flush=True)
    print(f"    claude mcp add --transport http myquant-research "
          f"http://127.0.0.1:{args.mcp_port}/mcp")
    print()
    print("  Ctrl-C 停止（两服务优雅同退）", flush=True)
    print("=" * 62, flush=True)

    try:
        while True:
            for p, name in ((api_proc, "API"), (mcp_proc, "MCP")):
                code = p.poll()
                if code is not None:
                    print(f"[异常] {name} 子进程退出（code={code}），"
                          f"应用停止。", file=sys.stderr)
                    _shutdown(signal.SIGTERM, None)
                    return 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
