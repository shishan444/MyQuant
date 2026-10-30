"""启动脚本假成功修复——脚本行为测试（fix-start-silent-failure B0-B11）。

工程首个 shell 行为测试。三件自起自清 fixture（B0）：
① 端口注入——bind 探测随机高位空闲端口（start.sh env 注入支持）；
② PID 文件守护——备份/恢复三 PID 文件，宿主有活服务（8000/8800 被
   占）时前置 skip（诚实 skip 优于污染）；
③ 占用进程工厂——health_hog（/api/health 返 200 的 http.server）/
   tcp_hog（纯 listener）/delayed_hog（延迟 listen，构造预检后竞态窗）。

B7 正常路径逐字节：git show main:scripts/start.sh（修复前版本）与本
分支版本在相同环境下输出对拍（PID/端口数字归一）。
"""

import os
import re
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
START = REPO / "scripts" / "start.sh"
STOP = REPO / "scripts" / "stop.sh"
RESTART = REPO / "scripts" / "restart.sh"
VENV_PY = REPO / "venv" / "bin" / "python"

PID_FILES = [REPO / ".myquant_api.pid", REPO / ".myquant_mcp.pid",
             REPO / ".myquant_web.pid"]


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture()
def ports():
    """bind 探测两个空闲高位端口（API/MCP）——env 注入下测试不触碰
    宿主固定端口服务。"""
    got = []
    holders = []
    for _ in range(2):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        got.append(s.getsockname()[1])
        holders.append(s)
    for s in holders:
        s.close()
    return {"api": got[0], "mcp": got[1]}


@pytest.fixture()
def pid_guard():
    """PID 文件接管：备份三文件（内容+存在性）后移出场——用例期间对
    PID 文件有完全控制权（宿主真服务的进程不受影响，文件 finally
    原样恢复）。测试内的清理一律自管理（直读 PID kill+rm），不经
    stop.sh——其 web 兜底段（ps 杀 vite）会误伤宿主 Web 服务。"""
    backup = {p: (p.exists(), p.read_bytes() if p.exists() else None)
              for p in PID_FILES}
    for p in PID_FILES:
        p.unlink(missing_ok=True)
    yield
    for p, (existed, content) in backup.items():
        if existed:
            p.write_bytes(content)
        else:
            p.unlink(missing_ok=True)


def _cleanup_started_services():
    """测试自管理清理：按 PID 文件杀本次拉起的进程并清文件（不经
    stop.sh——防其 web ps 兜底误伤宿主）。"""
    for p in PID_FILES:
        if p.exists():
            try:
                pid = int(p.read_text().strip())
                import signal
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    try:
                        os.kill(pid, 0)
                        time.sleep(0.1)
                    except ProcessLookupError:
                        break
            except (ValueError, ProcessLookupError, PermissionError):
                pass
            p.unlink(missing_ok=True)


def _env(ports, db_path=None):
    env = dict(os.environ)
    env["MYQUANT_API_PORT"] = str(ports["api"])
    env["MYQUANT_MCP_PORT"] = str(ports["mcp"])
    env["MYQUANT_WEB_PORT"] = str(ports["mcp"] + 1)
    if db_path is not None:
        env["MYQUANT_DB_PATH"] = str(db_path)
    return env


def _run_start(args, env, timeout=90):
    return subprocess.run(
        ["bash", str(START)] + args, env=env, capture_output=True,
        text=True, timeout=timeout)


# ── 占用进程工厂 ─────────────────────────────────────────────

_HEALTH_HOG = """
import http.server, threading

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass

http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()
"""

_TCP_HOG = """
import socket, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', {port}))
s.listen(5)
while True:
    time.sleep(60)
"""

_DELAYED_HOG = """
import socket, time
time.sleep({delay})
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', {port}))
s.listen(5)
while True:
    time.sleep(60)
"""

_DELAYED_HEALTH_HOG = """
import http.server, time
time.sleep({delay})

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass

http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()
"""


class _Hog:
    def __init__(self, script, port, delay=0):
        self.proc = subprocess.Popen(
            [str(VENV_PY), "-c", script.format(port=port, delay=delay)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.port = port

    def wait_listening(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _port_busy(self.port):
                return True
            time.sleep(0.1)
        return False

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture()
def hogs():
    started = []
    try:
        yield lambda script, port, delay=0: _launch(started, script, port,
                                                    delay)
    finally:
        for h in started:
            h.stop()


def _launch(started, script, port, delay):
    h = _Hog(script, port, delay)
    started.append(h)
    return h


# ── B1/B2：预检失败 ──────────────────────────────────────────

class TestPrecheck:
    def test_b1_api_precheck_fails_with_holder_clue(self, ports, pid_guard,
                                                    hogs, tmp_path):
        """B1（预检失败 API）：health_hog 占端口且无 PID 文件 → 退出码
        1、无「启动成功」、错误含占用者 PID 与命令行、PID 文件未写入。"""
        hog = hogs(_HEALTH_HOG, ports["api"])
        assert hog.wait_listening()
        r = _run_start(["-a"], _env(ports, tmp_path / "q.db"))
        assert r.returncode == 1
        assert "启动成功" not in r.stdout + r.stderr
        out = r.stdout + r.stderr
        assert "已被以下进程监听" in out
        assert str(hog.proc.pid) in out, "错误须含占用进程 PID"
        assert "python" in out, "错误须含占用进程命令行线索（ps command）"
        assert "stop.sh" in out, "错误须附处置指引（stop.sh 清理或手工处置）"
        assert hog.proc.poll() is None, "预检失败不得擅杀占用进程（处置权归操作者）"
        assert not (REPO / ".myquant_api.pid").exists(), "PID 文件未写入"

    def test_b2_mcp_precheck_fails(self, ports, pid_guard, hogs, tmp_path):
        """B2（预检失败 MCP）：任意监听进程占 MCP 端口 → 退出码 1。"""
        hog = hogs(_TCP_HOG, ports["mcp"])
        assert hog.wait_listening()
        r = _run_start(["-m"], _env(ports, tmp_path / "q.db"))
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "启动成功" not in out
        assert str(ports["mcp"]) in out
        assert str(hog.proc.pid) in out

    def test_default_mode_api_failure_stops_subsequent_services(
            self, ports, pid_guard, hogs, tmp_path):
        """spec「端口占用显式失败」（默认模式形态）+ B3 完成标准③：
        多服务模式先行服务预检失败 → exit 1 使后续 MCP/Web 均不被拉起
        （无 PID 文件、无 Web 启动输出、汇总块不打印）。"""
        hog = hogs(_HEALTH_HOG, ports["api"])
        assert hog.wait_listening()
        r = _run_start([], _env(ports, tmp_path / "q.db"))
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "启动成功" not in out
        assert "MyQuant 服务已启动" not in out, "失败路径汇总块不得打印"
        assert "Web 前端" not in out, "先行失败后 Web 不被拉起"
        assert not (REPO / ".myquant_mcp.pid").exists(), "后续 MCP 不被拉起"
        assert not (REPO / ".myquant_web.pid").exists()


# ── B3/B4：假成功路径（竞态窗构造） ──────────────────────────

class TestVerifyResponder:
    _BAD_DB = "/nonexistent-dir/q.db"  # 坏 DB 路径——子进程 lifespan 崩即秒死

    def test_b3_api_fake_success_intercepted(self, ports, pid_guard, hogs):
        """B3（change 级必过·假成功路径 API）：原始事故形态复刻——
        delayed health_hog 在预检后竞态窗内开始应答 /api/health，健康
        检查命中的不是本次进程 → verify_responder 拦截（形态一：本次
        已死+他人应答 / 形态二：本次活但非监听者——取决于子进程与 hog
        的 bind 竞速，两形态均属「假成功被拦」的验收面）：退出码 1、
        无「启动成功」、报应答者/监听者线索。"""
        hog = hogs(_DELAYED_HEALTH_HOG, ports["api"], delay=1.5)
        r = _run_start(["-a"], _env(ports, self._BAD_DB))
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "启动成功" not in out
        assert ("实际应答者是既有进程" in out
                or "监听者不是本次进程" in out), out
        assert str(hog.proc.pid) in out, "错误须含实际应答/监听者 PID"
        if "实际应答者是既有进程" in out:
            # spec Scenario「假成功路径」THEN：形态一（本次已死）PID 文件被清理
            assert not (REPO / ".myquant_api.pid").exists(), \
                "形态一（本次进程已死）死 PID 文件必须清理"

    def test_b4_mcp_fake_success_intercepted(self, ports, pid_guard, hogs):
        """B4（假成功路径 MCP）：本次 MCP 秒死（坏档位 env——经实测
        startup 校验 ValueError 退出）+delayed tcp_hog 在预检后 listen
        ——tcp_probe 命中 hog 但本次进程死 → 形态一拦截。"""
        hog = hogs(_DELAYED_HOG, ports["mcp"], delay=1.5)
        env = _env(ports, self._BAD_DB)
        env["MYQUANT_MCP_PROFILE"] = "__bad_profile__"
        r = _run_start(["-m"], env)
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "启动成功" not in out
        # 两种正确拦截：探活命中 hog 时身份核验拦（「监听者不是本次
        # 进程」）；真进程 bind 失败秒退被先发现（「进程已退出」——
        # svc_lib 版更快的失败路径，时序取决于 hog 延迟）
        assert ("监听者不是本次进程" in out
                or "进程已退出" in out), out
        assert not (REPO / ".myquant_mcp.pid").exists(), "死 PID 文件已清理"


# ── B5/B6：第三态与已死分支（文本断言层——行为面由预检/核验路径
#    联合覆盖，第三态需注入坏 config 构造、环境不可控按任务书降级） ──

class TestThirdState:
    def test_b5_exhausted_alive_exits_nonzero_textual(self):
        """B5（降级文本断言，svc_lib 迁移版）：探活耗尽且进程仍活分支
        return 1（入口层 exit 1）且不含成功字样。"""
        src = (REPO / "scripts" / "svc_lib.sh").read_text(encoding="utf-8")
        seg = src.split("start_one")[1]
        alive = seg.split("健康检查未通过")[1].split("sleep 1")[0]
        assert "return 1" in alive, "仍活分支缺 return 1（入口层转 exit 1）"
        assert "启动成功" not in alive, "仍活分支不得含成功字样"

    def test_b6_dead_branch_unchanged_textual(self):
        """B6（文本断言，svc_lib 迁移版）：进程已退出分支维持
        error+rm PID 文件+return 1 既有形制。"""
        src = (REPO / "scripts" / "svc_lib.sh").read_text(encoding="utf-8")
        assert 'error "$label 启动失败（进程已退出）' in src, "已死分支 error 形制在"
        dead = src.split("进程已退出")[1].split("sleep 1")[0]
        assert "return 1" in dead and "rm -f" in dead, "已死分支 PID 清理+return 1 在"


# ── B7：正常路径逐字节对拍（main 修复前版本 vs 本分支） ──────

class TestNormalPath:
    def test_b7_start_output_structure_anchor(self, ports, pid_guard,
                                                 tmp_path):
        """B7（重构版）：旧「与 main 逐字节对拍」语义随统一性重构终结
        （输出形态有意变更）；改为新输出结构锚——真实启动断言关键行
        齐备（组件成功行连写形制/汇总横幅/访问地址/文档端点）。"""
        try:
            r = _run_start(["-a"], _env(ports, tmp_path / "q.db"))
            assert r.returncode == 0, r.stdout + r.stderr
            out = r.stdout + r.stderr
            assert "API 服务启动成功 (PID:" in out
            assert "MyQuant 服务已启动" in out  # 汇总横幅
            assert "http://localhost:%d" % ports["api"] in out
            assert "/docs" in out
        finally:
            _cleanup_started_services()

    def test_mcp_normal_path_starts_and_exits_zero(self, ports, pid_guard,
                                                   tmp_path):
        """start_mcp 成功路径行为补位（B2/B4 均为失败路径，成功路径此前
        仅 B11 文本锚保护打印行）：-m 端口空闲 → tcp_probe 命中本次进程、
        verify_responder 通过、退出 0、PID 文件在。API 未起仅 warning
        不阻塞（spec「API 可达性自检」语义）。"""
        try:
            r = _run_start(["-m"], _env(ports, tmp_path / "q.db"))
            assert r.returncode == 0, r.stdout + r.stderr
            out = r.stdout + r.stderr
            assert "MCP 服务启动成功" in out
            assert "127.0.0.1:%d/mcp" % ports["mcp"] in out
            assert (REPO / ".myquant_mcp.pid").exists()
        finally:
            _cleanup_started_services()


# ── B9：PID 活跳过 ────────────────────────────────────────────

class TestPidAliveSkip:
    def test_b9_pid_alive_skips_precheck(self, ports, pid_guard, tmp_path):
        """B9（svc_lib 语义升级版）：PID 文件指向无关进程（非本服务
        指纹）→ 判残留清理+照常启动成功（旧版盲信 PID 文件判「已在
        运行」——pid 复用会误跳启动，统一性重构引入指纹核验后该形态
        被正确识别为残留）。真指纹进程的「已在运行」幂等路径由
        precheck_one 分支覆盖（文本锚在 TestTextualAnchors）。"""
        pid_file = REPO / ".myquant_api.pid"
        # 用一个存活的哑进程占用 PID 槽位（不监听任何端口、非 api 指纹）
        dummy = subprocess.Popen(
            ["sleep", "120"], stdout=subprocess.DEVNULL)
        pid_file.write_text(str(dummy.pid))
        try:
            r = _run_start(["-a"], _env(ports, tmp_path / "q.db"))
            assert r.returncode == 0, r.stdout + r.stderr
            out = r.stdout + r.stderr
            assert "按残留清理" in out, "无关进程须被判残留清理"
            assert "API 服务启动成功" in out, "清理后照常启动"
            assert "已在运行" not in out, "盲信 PID 的旧语义不得回归"
            assert "已被以下进程监听" not in out, "哑进程不占端口不进端口预检"
        finally:
            dummy.terminate()
            dummy.wait()
            _cleanup_started_services()


# ── B10：stop/restart 回归 ────────────────────────────────────

class TestStopRestartRegression:
    def test_b10_restart_zero_change_and_propagation(self, ports, pid_guard):
        """B10（svc_lib 迁移版）：restart 薄壳转发统一入口；svc.sh 内
        set -e 传导形制在文——stop 失败即中止（cmd_stop exit 1），
        「重启完成」仅在 start 全或全败成功后打印。"""
        src = RESTART.read_text(encoding="utf-8")
        assert "svc.sh\" restart" in src, "薄壳转发统一入口在文"
        svc = (REPO / "scripts" / "svc.sh").read_text(encoding="utf-8")
        assert "set -euo pipefail" in svc, "set -e 传导前提在文"
        assert "重启完成" in svc
        # stop 失败传导：cmd_restart 段内 cmd_stop 直调先于 cmd_start
        restart_seg = svc.split("cmd_restart()")[1]
        assert restart_seg.index("cmd_stop") < restart_seg.index("cmd_start")


# ── B8/B11：编排与形制文本断言 ────────────────────────────────

class TestTextualAnchors:
    def test_b11_guard_forms_in_script(self):
        """B11（svc_lib 迁移版）：守卫形制在文——预检函数+身份核验文案+
        -sTCP:LISTEN 过滤；成功打印行连写形制（「API 服务启动成功」）；
        身份核验文案先于成功打印（健康→核验→打印顺序铁律）。"""
        src = (REPO / "scripts" / "svc_lib.sh").read_text(encoding="utf-8")
        assert "precheck_one()" in src
        assert "监听者不是本次进程" in src  # 身份核验失败文案
        assert src.count("-sTCP:LISTEN") >= 2, "端口预检+监听者判定过滤在"
        assert 'info "${label}启动成功 (PID: $pid)"' in src
        # 核验文案在成功打印之前（顺序铁律：健康→核验→打印）
        assert src.index("监听者不是本次进程") < src.index("${label}启动成功")
        # 停止三段式形制在文（pid 文件→监听者→指纹兜底）
        assert "指纹兜底" in src and "kill_and_wait" in src

    def test_b8_failure_stops_subsequent_services_textual(self):
        """B8（svc.sh 迁移版）：全或全败——失败回滚已起服务+exit 1，
        汇总块仅在全成功路径到达；失败文案附统一清理指引（新语义：
        回滚+指引兜底，取代旧版不回滚只指引——用户裁决三端一体）。"""
        src = (REPO / "scripts" / "svc.sh").read_text(encoding="utf-8")
        assert "服务已启动" in src  # 汇总块在
        assert "回滚已启动的服务（全或全败）" in src
        assert "已启动的其他服务可用 scripts/stop.sh 统一清理" in src
        # 预检先行：任一障碍整体拒绝（不产生半启动状态）
        assert "整体拒绝启动" in src
