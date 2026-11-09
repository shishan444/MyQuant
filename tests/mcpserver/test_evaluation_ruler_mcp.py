"""评测尺子模板族 MCP 面透传与披露测试（add-evaluation-ruler 任务 6.1）。

透传三模板各一次（mock api.post 捕获 body——test_server 先例形制）；
docstring 枚举一致性由 test_disclosure_anchors 元断言承载（此处补
三模板名在描述层的在场锚）。
"""
import pytest

pytestmark = [pytest.mark.integration]


def _tools_by_name():
    from mcpserver.server import build_server
    from tests.mcpserver.test_contract_parity import run_async

    mcp, _ = build_server(profile="research")
    tools = run_async(mcp.list_tools())
    return {t.name: t for t in tools}


class TestEvaluationRulerMcp:
    def test_docstring_mentions_three_templates(self) -> None:
        desc = _tools_by_name()["run_analysis"].description
        for name in ("wave_inventory", "box_inventory", "ruler_compare"):
            assert name in desc, f"docstring 枚举缺 {name}"
        # 跨查询可比性披露（zigzag 起点效应——禁混用拼接）
        assert "不可直接混用拼接" in desc

    def _capture(self, monkeypatch):
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}

        def _post(path, json_body=None):
            captured.update(path=path, body=json_body)
            return {"template": json_body["template"], "sample_size": 1,
                    "confidence": {}, "data_ranges": [], "payload": {}}

        api.post = _post
        return mcp, captured, run_async

    def test_wave_inventory_passthrough(self, monkeypatch):
        mcp, captured, run_async = self._capture(monkeypatch)
        run_async(mcp.call_tool("run_analysis", {
            "template": "wave_inventory", "params": {"threshold": 0.05},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2026-07-01", "end": "2026-11-07"}))
        assert captured["path"] == "/api/research/analysis"
        body = captured["body"]
        assert body["template"] == "wave_inventory"
        assert body["params"]["threshold"] == 0.05
        assert body["data_range"]["symbol"] == "BTCUSDT"

    def test_box_inventory_passthrough(self, monkeypatch):
        mcp, captured, run_async = self._capture(monkeypatch)
        run_async(mcp.call_tool("run_analysis", {
            "template": "box_inventory", "params": {"window": 120},
            "symbol": "BTCUSDT", "timeframe": "1h",
            "start": "2026-07-01", "end": "2026-11-07"}))
        assert captured["body"]["template"] == "box_inventory"
        assert captured["body"]["params"]["window"] == 120

    def test_ruler_compare_passthrough(self, monkeypatch):
        mcp, captured, run_async = self._capture(monkeypatch)
        run_async(mcp.call_tool("run_analysis", {
            "template": "ruler_compare",
            "params": {"mode": "wave",
                       "trade_source": {"kind": "validation",
                                        "record_id": "rec_x"}},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2026-07-01", "end": "2026-11-07"}))
        body = captured["body"]
        assert body["template"] == "ruler_compare"
        assert body["params"]["mode"] == "wave"
        assert body["params"]["trade_source"]["record_id"] == "rec_x"
        assert "hypothesis_id" not in body, "对照不携带假设关联"
