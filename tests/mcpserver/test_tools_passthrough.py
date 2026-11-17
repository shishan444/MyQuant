"""MCP 工具层 body 组装行为锚。

contract_parity 承担 AST 结构对拍；此处补行为级：工具 body 组装对
可选字段的条件透传（带值传入 / 缺省省略）。假设登记族透传用例
（trigger_policy/strategy_params/分批组键）随 retire-legacy 任务 9
register_hypothesis 工具退役删除；query_indicator 侧存活承载。
"""

import pytest

pytestmark = [pytest.mark.integration]

from conftest import _FakeApi, _FakeMcp  # 同目录（pytest prepend）


class TestQueryIndicatorBareBody:
    """query_indicator 可选键全缺省侧（indicator/groups/params/reductions/
    start/end 均 is not None 透传——缺省侧不带键；单组/批量互斥交 API
    单一事实源判，工具层不吞键）。"""

    def test_no_optional_keys_bare_body(self):
        mcp, api = _FakeMcp(), _FakeApi()
        from mcpserver import tools as tools_mod

        tools_mod.register_research_tools(mcp, api)
        mcp.registered["query_indicator"](
            symbol="BTCUSDT", timeframe="4h")
        url, body = api.posts[0]
        assert url == "/api/research/query-indicator"
        assert body == {"symbol": "BTCUSDT", "timeframe": "4h"}, \
            "可选键缺省侧不带 indicator/groups 等键（裸 body）"
