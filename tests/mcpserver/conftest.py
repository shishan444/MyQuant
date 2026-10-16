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
