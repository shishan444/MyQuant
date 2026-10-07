"""DNAModel 传输面保真锚（trigger-policy 卡5 / D11 / tasks 6.1）。

五消费面（evolution initial/champion、策略创建读回、compare、batch、
verify）共享 DNAModel（extra="forbid"）——扩键前后带 policy 策略经
API 创建/读回通道往返保真；openapi.json 与 TS 派生物含新键（生成链
同步锚——npm run generate:types 产物漂移即红）。
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]


_DNA_BODY = {
    "signal_genes": [],
    "execution_genes": {"timeframe": "4h"},
    "entry_trigger_policy": {"mode": "suppress_bars", "bars": 48},
}


class TestDnaPolicyTransport:
    def test_创建读回_往返保真(self, api_client) -> None:
        resp = api_client.post("/api/strategies", json={
            "dna": _DNA_BODY, "symbol": "BTCUSDT", "timeframe": "4h",
            "name": "policy-transport-check"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["dna"]["entry_trigger_policy"] == \
            {"mode": "suppress_bars", "bars": 48}

        got = api_client.get(f"/api/strategies/{body['strategy_id']}")
        assert got.status_code == 200
        assert got.json()["dna"]["entry_trigger_policy"] == \
            {"mode": "suppress_bars", "bars": 48}

    def test_无policy策略_响应不含键(self, api_client) -> None:
        resp = api_client.post("/api/strategies", json={
            "dna": {"signal_genes": [],
                    "execution_genes": {"timeframe": "4h"}},
            "symbol": "BTCUSDT", "timeframe": "4h"})
        assert resp.status_code == 201, resp.text
        assert "entry_trigger_policy" not in resp.json()["dna"] \
            or resp.json()["dna"]["entry_trigger_policy"] is None

    def test_openapi与TS派生物含新键(self) -> None:
        """生成链同步锚：DNAModel 扩键后 generate:types 产物须重跑——
        派生物缺键即漂移（先例 d42115e/acf03e3）。"""
        root = Path(__file__).resolve().parents[2]
        openapi = json.loads((root / "web" / "openapi.json").read_text())
        assert "entry_trigger_policy" in json.dumps(
            openapi["components"]["schemas"])
        ts = (root / "web" / "src" / "types" / "generated" /
              "api.ts").read_text()
        assert "entry_trigger_policy" in ts
