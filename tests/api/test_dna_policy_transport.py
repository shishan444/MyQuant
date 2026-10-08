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


_BAD_POLICY_DNA = {
    "signal_genes": [],
    "execution_genes": {"timeframe": "4h"},
    # dict 形非法：schema 层 Dict[str, Any] 放行（字符串形由 pydantic
    # 先拦属双闸另一层），到 to_dna() 后由登记点校验拦——CARD-01 锚
    # 的正是这条通道
    "entry_trigger_policy": {"mode": "bogus"},
}


class TestPolicyShapeRejection:
    """CARD-01（event-triggers 批次三）登记点早拒绝锚：策略通道
    entry_trigger_policy 此前全程无校验——三路由 to_dna() 后经
    validate_policy 形制校验，非法 dict 形 422 先于持久化（specs
    trigger-policy「非法枚举结构化拒绝」在策略通道的落地面）。
    """

    def test_create_非法策略_422(self, api_client) -> None:
        resp = api_client.post("/api/strategies", json={
            "dna": _BAD_POLICY_DNA, "symbol": "BTCUSDT", "timeframe": "4h",
            "name": "card01-bad"})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "none" in resp.text            # 合法枚举清单附出

    def test_update_非法策略_422_原值不动(self, api_client) -> None:
        created = api_client.post("/api/strategies", json={
            "dna": _DNA_BODY, "symbol": "BTCUSDT", "timeframe": "4h",
            "name": "card01-target"})
        assert created.status_code == 201, created.text
        sid = created.json()["strategy_id"]

        resp = api_client.put(f"/api/strategies/{sid}",
                              json={"dna": _BAD_POLICY_DNA})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        # 失败场景禁止变化：读回 dna 保持原合法 policy（校验先于写）
        got = api_client.get(f"/api/strategies/{sid}")
        assert got.status_code == 200
        assert got.json()["dna"]["entry_trigger_policy"] == \
            {"mode": "suppress_bars", "bars": 48}

    def test_evolution_initial_dna_非法策略_422(self, api_client) -> None:
        resp = api_client.post("/api/evolution/tasks", json={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "initial_dna": _BAD_POLICY_DNA})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"


class TestPolicyNormalizationWriteback:
    """CARD-01 观察项（security 采纳）：登记点回写归一形——显式
    {"mode":"none"} 归一 None 落库=与缺省同存储同签名（D4 幂等签名
    语义在策略通道的对齐锚）；first_true 归一形原样落库。
    """

    def test_显式none_落库归一为缺省(self, api_client) -> None:
        resp = api_client.post("/api/strategies", json={
            "dna": {"signal_genes": [],
                    "execution_genes": {"timeframe": "4h"},
                    "entry_trigger_policy": {"mode": "none"}},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "name": "card01-none-norm"})
        assert resp.status_code == 201, resp.text
        got = api_client.get(
            f"/api/strategies/{resp.json()['strategy_id']}")
        assert got.status_code == 200
        # 归一 None=与缺省同存储（不保留显式 none 字面）
        assert got.json()["dna"].get("entry_trigger_policy") is None

    def test_first_true_落库归一形原样(self, api_client) -> None:
        resp = api_client.post("/api/strategies", json={
            "dna": {"signal_genes": [],
                    "execution_genes": {"timeframe": "4h"},
                    "entry_trigger_policy": {"mode": "first_true"}},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "name": "card01-ft-norm"})
        assert resp.status_code == 201, resp.text
        got = api_client.get(
            f"/api/strategies/{resp.json()['strategy_id']}")
        assert got.status_code == 200
        assert got.json()["dna"]["entry_trigger_policy"] == \
            {"mode": "first_true"}
