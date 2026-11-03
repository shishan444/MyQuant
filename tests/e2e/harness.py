"""e2e 自管服务组机制件（add-e2e-isolated-harness H1）。

conftest 只保 pytest 两层（pytest_configure hook + session fixture 链），
进程/端口/探活/清理的全部机制收在 本模块——单元用例（test_harness_unit）
直接 import 本模块逐形态直测，不经 fixture 装配、不碰 conftest 命名空间。

三独立两复用：独立端口（内核随机分配）/独立业务库（MYQUANT_DB_PATH
临时库）/独立进程组（start_new_session+killpg）；复用工程代码（-m api
+ vite dev）与生产行情目录（FAPI_DATA_DIR 只读挂载）。
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_FAPI_DIR = PROJECT_ROOT / "data" / "market_fapi"
VENV_PYTHON = PROJECT_ROOT / "venv" / "bin" / "python"

# pytest_configure 阶段产出的端口/pid 台账：fixture 消费端口、
# e2e_db teardown 消费 pid/port 做测后零残留断言。
E2E_STATE: dict[str, int] = {}


def probe_free_port() -> int:
    """内核分配随机空闲端口（bind(0) 即刻释放；实绑由子进程完成）。

    竞态窗口=探测释放到 fixture 实绑之间（collection 时长，秒级）——
    碰撞由子进程绑定失败 fail-fast 承载（端口已被用例模块 BASE 固化，
    无法换端口重传播，fail-fast 是唯一自洽语义）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def kill_process_group(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """进程组销毁：TERM → ≤timeout 轮询退出 → KILL 兜底。

    子进程经 start_new_session=True 拉起，proc.pid 即 pgid——killpg
    收全组（pnpm→node 子链等），teardown 与 armed 失败路径同形制。
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.2)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # 未及收割的残余由 init 承接；端口随进程死亡由内核回收，不阻塞 teardown
        pass


def log_tail(log_path: Path, lines: int = 20) -> str:
    """读子进程日志尾部（探活失败错误信息附载，便于归因）。"""
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-lines:])
    except OSError:
        return f"<日志不可读: {log_path}>"


def wait_http_ready(
    url: str,
    proc: subprocess.Popen,
    log_path: Path,
    description: str,
    timeout: float = 40.0,
    interval: float = 0.5,
) -> None:
    """探活轮询：HTTP 200 即就绪；每轮先查进程早退（附日志尾部 20 行）。

    fail-fast 不重试（对齐 svc.sh 量级 0.5s×40s）：换端口重试需重取
    端口，但 E2E_WEB_URL 已被用例模块 BASE 固化无法重传播。
    """
    deadline = time.monotonic() + timeout
    last_error = "未及发起请求"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"{description} 进程早退（exit={proc.returncode}，url={url}）。\n"
                f"日志尾部：\n{log_tail(log_path)}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
                last_error = f"HTTP {resp.status}"
        except (urllib.error.URLError, OSError) as exc:
            last_error = repr(exc)
        time.sleep(interval)
    raise TimeoutError(
        f"{description} 探活超时（{timeout}s，url={url}，last={last_error}）。\n"
        f"日志尾部：\n{log_tail(log_path)}"
    )


@dataclass
class ServiceProcess:
    """e2e 子进程句柄（进程+日志+端口；log_handle 由所有者 teardown 关闭）。"""

    proc: subprocess.Popen
    log_path: Path
    log_handle: IO[str]
    port: int
    base_url: str

    def close(self) -> None:
        """teardown 统一收口：杀进程组+关日志句柄。"""
        kill_process_group(self.proc)
        self.log_handle.close()


def spawn_api_process(api_port: int, db_path: Path, log_path: Path) -> ServiceProcess:
    """拉起临时 api 子进程（`-m api`，独立进程组）。

    env 五项显式注入（覆盖一切继承值；PATH 等进程基座随父环境继承）：
    MYQUANT_API_PORT（随机端口，api/__main__.py 消费）/ MYQUANT_DB_PATH
    （临时库——create_app 缺省读点通路）/ FAPI_DATA_DIR（生产行情目录
    只读挂载）/ FAPI_STREAM_DISABLED=1（旁路 WS 行情流+建库线程+保鲜
    守护器：不连外网、不写生产 parquet，旁路留痕 warning 可断言）/
    PYTHONPATH（项目根导入）。
    """
    env = os.environ.copy()
    env.update(
        {
            "MYQUANT_API_PORT": str(api_port),
            "MYQUANT_DB_PATH": str(db_path),
            "FAPI_DATA_DIR": str(DATA_FAPI_DIR),
            "FAPI_STREAM_DISABLED": "1",
            "PYTHONPATH": str(PROJECT_ROOT),
        }
    )
    log_handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(VENV_PYTHON), "-m", "api"],
        cwd=str(PROJECT_ROOT),
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return ServiceProcess(
        proc=proc,
        log_path=log_path,
        log_handle=log_handle,
        port=api_port,
        base_url=f"http://127.0.0.1:{api_port}",
    )


def spawn_web_process(web_port: int, api_port: int, log_path: Path) -> ServiceProcess:
    """拉起临时 web 子进程（vite dev --strictPort，独立进程组）。

    双 env 注入（vite 语义：进程 env 优先于 .env* 文件）：
    - VITE_API_PROXY_TARGET 指向临时 api（vite.config.ts 三条目 env 化
      的消费点）；
    - VITE_API_BASE_URL=web 自身 origin——压制 web/.env.development 的
      绝对地址预置（http://localhost:8000 会使 REST 直连常驻 api：
      常驻在跑时读生产数据=全绿假象，停止时连接拒绝=页面错误态）。
      同源绝对地址使 REST 与实时链路统一经 proxy 三条目，零 CORS 面。
    --strictPort 保证端口冲突立即退出而非漂移挪用。
    """
    env = os.environ.copy()
    env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{api_port}"
    env["VITE_API_BASE_URL"] = f"http://127.0.0.1:{web_port}"
    log_handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            "pnpm",
            "exec",
            "vite",
            "--host",
            "127.0.0.1",
            "--port",
            str(web_port),
            "--strictPort",
        ],
        cwd=str(PROJECT_ROOT / "web"),
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return ServiceProcess(
        proc=proc,
        log_path=log_path,
        log_handle=log_handle,
        port=web_port,
        base_url=f"http://127.0.0.1:{web_port}",
    )


def assert_no_residue(db_path: Path) -> None:
    """测后零残留断言（pgid+端口+临时库三伴生级，与常驻组正交）。

    由 e2e_db teardown 调用（fixture 逆序保证 web/api 均已销毁）。
    常驻跑停态皆适用：断言对象仅限本会话拉起的 pid 与预定的随机端口。
    """
    for key in ("api_pid", "web_pid"):
        pid = E2E_STATE.get(key)
        if pid is None:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise AssertionError(f"e2e 残留进程未销毁：pid={pid}（{key}）")
    for key in ("api_port", "web_port"):
        port = E2E_STATE.get(key)
        if port is None:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            still_listening = sock.connect_ex(("127.0.0.1", port)) == 0
        assert not still_listening, f"e2e 预定端口仍被监听：{port}（{key}）"
    for suffix in ("", "-shm", "-wal"):
        residue = Path(f"{db_path}{suffix}")
        residue.unlink(missing_ok=True)
        assert not residue.exists(), f"临时库伴生文件未清理：{residue}"


def db_sidecar_paths(db_path: Path) -> tuple[Path, Path, Path]:
    """临时库三伴生路径（db/-shm/-wal，teardown unlink 与守护共用）。"""
    return (
        db_path,
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-wal"),
    )


def iter_session_logs(base_dir: Path) -> Iterator[Path]:
    """本会话子进程日志路径迭代（api.log/web.log，哨兵断言消费）。"""
    yield base_dir / "api.log"
    yield base_dir / "web.log"
