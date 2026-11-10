"""ab_compare MCP 面透传与披露测试（sandbox-and-report-supplement B4）。

透传一次（mock api.post 捕获 body——test_evaluation_ruler_mcp 先例
形制）＋docstring 在场锚（第 16 模板——枚举与必填键段）。
"""
import pytest

pytestmark = [pytest.mark.integration]


def _tools_by_name():
    from mcpserver.server import build_server
    from tests.mcpserver.test_contract_parity import run_async

    mcp, _ = build_server(profile="research")
    tools = run_async(mcp.list_tools())
    return {t.name: t for t in tools}


class TestAbCompareMcp:
    def test_docstring_mentions_ab_compare(self) -> None:
        """第 16 模板在场锚：枚举与必填键段双在场。"""
        desc = _tools_by_name()["run_analysis"].description
        assert "ab_compare" in desc, "docstring 枚举缺 ab_compare"
        assert "ab_compare 须 mode+trade_source_a+trade_source_b" in desc

    def test_ab_compare_passthrough(self, monkeypatch):
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}

        def _post(path, json_body=None):
            captured.update(path=path, body=json_body)
            return {"template": json_body["template"], "sample_size": 1,
                    "confidence": {}, "data_ranges": [], "payload": {}}

        api.post = _post
        run_async(mcp.call_tool("run_analysis", {
            "template": "ab_compare",
            "params": {"mode": "ab",
                       "trade_source_a": {"kind": "validation",
                                          "record_id": "rec_a"},
                       "trade_source_b": {"kind": "validation",
                                          "record_id": "rec_b"},
                       "dimension": "wave"},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2026-07-01", "end": "2026-11-07"}))
        body = captured["body"]
        assert body["template"] == "ab_compare"
        assert body["params"]["mode"] == "ab"
        assert body["params"]["trade_source_a"]["record_id"] == "rec_a"
        assert body["params"]["trade_source_b"]["record_id"] == "rec_b"
        assert body["params"]["dimension"] == "wave"
        assert "hypothesis_id" not in body, "对照不携带假设关联"
