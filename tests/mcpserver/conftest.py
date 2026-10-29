"""mcpserver 测试共享基座（fix-review CARD-05）。

捕获型 _FakeMcp（恒等装饰捕获工具注册）与 _FakeApi 两变体原散布
四处逐字同构定义（tools_passthrough / direction_mcp_parity /
page_parity / return_contract），提取为共享单源；消费文件裸名
import（同目录 pytest prepend——_expected_tools 同款先例）。

两变体口径：
- _FakeApi：post-only 固定返回版（单端点 body 组装断言用）
- RecordingFakeApi：固定样本记录版（GET/POST/POLL 全记录、样本
  参数化，全集契约自检用；原名 _FakeApi，因与 post-only 版同名
  冲突在提取时定名）

test_server.py 的 _FakeMcp 为 run-only 分派 stub（仅 run 方法，
名字撞车语义不同）不属本提取面。
"""

import os

import pytest


@pytest.fixture(scope="module", autouse=True)
def _fapi_bypass_for_live_servers():
    """live 起服 fixture 的网络旁路前置（阶段四 13 errors 修复）。

    根 conftest 的 FAPI_STREAM_DISABLED 旁路是 function 级 autouse，
    而 live_api/live_env 类起服 fixture 是 module/class 级——实例化
    早于 function 级，起服线程的 lifespan 以真实网络模式装配（fapi
    流+空 data_dir 建库）。外网不可达时 startup 死等（隔离复现 120s
    started=False、无异常抛出），live_api 的 15 秒轮询超时后
    server.servers 取值 AttributeError（13 用例 setup error）。外网
    通时（基线）真实拉取秒过，掩盖了 fixture 作用域时序缺口。

    此处置位使起服时 lifespan 即走旁路（K2 未启动缺省语义）；
    paper_trading 域的装配专项 delenv 形制不受本 fixture 影响
    （异域+晚执行的 function 级语义保留）。live 类用例断言面
    （MCP 工具调用/页面对照/DNA 门）不测 fapi 流本身，旁路
    不弱化断言语义。monkeypatch fixture 为 function 级（module
    级请求不可用），故直接 os.environ 置位并在 teardown 仅撤
    自身置位（进程 env 原有值不动）。
    """
    _ours = "FAPI_STREAM_DISABLED" not in os.environ
    if _ours:
        os.environ["FAPI_STREAM_DISABLED"] = "1"
    yield
    if _ours:
        os.environ.pop("FAPI_STREAM_DISABLED", None)


class _FakeMcp:
    """捕获 @mcp.tool() 注册的工具函数（恒等装饰）。"""

    def __init__(self):
        self.registered = {}

    def tool(self):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


class _FakeApi:
    """post-only 固定返回版（记录 POST 调用，固定回 hypothesis_id）。"""

    def __init__(self):
        self.posts = []

    def post(self, url, body):
        self.posts.append((url, body))
        return {"hypothesis_id": "h1"}


class RecordingFakeApi:
    """固定样本记录版（GET 透传记录；POST/POLL 固定样本返回）。"""

    def __init__(self, samples: dict | None = None):
        self.samples = samples or {}
        self.calls: list[tuple] = []

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return self.samples.get(url, {"items": [], "total": 0})

    def post(self, url, body):
        self.calls.append(("POST", url, body))
        return self.samples.get(url, {"ok": True})

    def poll_until_terminal(self, url, terminal=(), total_timeout=60.0):
        self.calls.append(("POLL", url, None))
        return self.samples.get(url, {"status": "completed"})
