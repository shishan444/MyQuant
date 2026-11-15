"""沙盒统一流入披露 MCP 面测试（add-sandbox-records-unified-flow
B6/C6——TC-8-1/TC-8-2）。

透传形制沿 test_task_cap_mcp 先例（mock api.post）；docstring/
capability 静态文本锚（规整换行后匹配——防折行假红）。
"""
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]


def _flat(text: str) -> str:
    """规整换行与空格（docstring 文本锚教训——源码折行拆中文关键词
    致子串断言假红，断言前去换行去空格再匹配）。"""
    return text.replace("\n", "").replace(" ", "")


def _tools_by_name():
    from mcpserver.server import build_server
    from tests.mcpserver.test_contract_parity import run_async

    mcp, _ = build_server(profile="research")
    tools = run_async(mcp.list_tools())
    return {t.name: t for t in tools}


class TestSandboxUnifiedFlowDisclosure:
    def test_mcp_run_backtest_echoes_record_id(self, monkeypatch):
        """TC-8-1：MCP 工具面响应含 record_id（薄代理透传自动获得——
        工具签名/schema 零处理零变化）。"""
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        mcp, api = build_server(profile="research", base_url="http://x")

        def _post(path, json_body=None):
            assert path == "/api/research/backtest-sandbox"
            return {"strategy_id": "st_1", "metrics": {}, "sandbox": True,
                    "record_id": "rec_mcp_echo_1"}

        api.post = _post
        res = run_async(mcp.call_tool("run_backtest", {
            "strategy_id": "st_1", "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01", "end": "2024-02-01"}))
        import json
        out = json.loads(res.content[0].text)
        assert out["record_id"] == "rec_mcp_echo_1"

    def test_mcp_docstring_and_capability_synced(self):
        """TC-8-2：docstring/capability 同构披露——新句「验证记录
        统一流」在场、旧句「不入验证记录」不在场（G11 防半改锚）。"""
        desc = _tools_by_name()["run_backtest"].description
        flat = _flat(desc)
        assert "验证记录统一流" in flat
        assert "不入验证记录" not in flat
        assert "sandbox_backtest" in desc
        assert "record_id" in desc
        cap = Path("mcpserver/capability.py").read_text(encoding="utf-8")
        cap_flat = _flat(cap)
        assert "验证记录统一流" in cap_flat
        assert "不入验证记录" not in cap_flat
