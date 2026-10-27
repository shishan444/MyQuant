"""验证记录执行配置披露（research-execution-config-alignment 1.1，C1-A3）。

卡 A 双消费点的验证侧面：validation_service 五组装点（isolated
result_json / random result_json+返回体 / staged result_json+返回体）
与 translate 回执共用 _risk_genes_disclosure 单点——键集与值同源同值
（禁两处组装）；hypothesis_id 缺失（手工/进化产物 adhoc）→
default_filled=null 两态（is None 与 ==[] 类型级区分）。
幂等重放经 result_json 内嵌键自动同构。
"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext

CONDITIONS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}},
         "action": "lt", "target": 35}],
    "then_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 30}},
         "action": "gt", "target": 60}],
}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def env(tmp_path: Path):
    """500 bar 4h 数据 + 库；app.state 由用例内覆写（TestClient 时序）。"""
    rng = np.random.default_rng(11)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 120, n))
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    pd.DataFrame({
        "open": close, "high": close + 80, "low": close - 80,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 100,
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    return {"db": db, "data_dir": data_dir}


def _register_hyp(client, strategy_params=None):
    body = {"conditions": CONDITIONS, "statement": "披露测试",
            "data_ranges": _RANGES}
    if strategy_params is not None:
        body["strategy_params"] = strategy_params
    r = client.post("/api/research/hypotheses", json=body)
    assert r.status_code == 200, r.text
    return r.json()["hypothesis_id"]


def _record_result(db: Path, hypothesis_id: str, strategy_id: str,
                   level: str):
    """按三元（hypothesis/strategy/level）取记录行 result json。"""
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT result FROM validation_records "
            "WHERE hypothesis_id=? AND strategy_id=? AND level=?",
            (hypothesis_id, strategy_id, level)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"无 {level} 记录行"
    return json.loads(row[0])


class TestIsolatedSameSourceAsReceipt:
    """A3：隔离验证披露面与 translate 回执同源同值（双消费点断言）。"""

    def test_iso_disclosure_equals_translate_receipt(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        tr = api_client.post(f"/api/research/translate/{hid}", json={})
        assert tr.status_code == 200, tr.text
        receipt = tr.json()
        sid = receipt["strategy_id"]

        r = api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hid, "strategy_id": sid,
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-03-01T00:00:00Z",
                              "end": "2024-03-15T00:00:00Z"}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"], "验证披露与回执不同源同值"
        assert "stop_loss" not in \
            body["risk_genes_disclosure"]["default_filled"]
        assert body["risk_genes_disclosure"]["values"]["stop_loss"] == 0.08
        # 记录行读回：result_json 落库同值
        result = _record_result(env["db"], hid, sid, "isolated_backtest")
        assert result["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"]

    def test_iso_idempotent_replay_isomorphic(self, env, api_client):
        """幂等重放：duplicated=True 且披露与首次逐键同构（result_json
        内嵌键经 _validation_payload 顶层化）。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        sid = api_client.post(f"/api/research/translate/{hid}",
                              json={}).json()["strategy_id"]
        payload = {"hypothesis_id": hid, "strategy_id": sid,
                   "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                                     "start": "2024-03-01T00:00:00Z",
                                     "end": "2024-03-15T00:00:00Z"}}
        first = api_client.post("/api/research/validate-backtest",
                                json=payload)
        again = api_client.post("/api/research/validate-backtest",
                                json=payload)
        assert again.status_code == 200, again.text
        assert again.json()["duplicated"] is True
        assert again.json()["risk_genes_disclosure"] == \
            first.json()["risk_genes_disclosure"]


class TestAdhocNullDefaultFilled:
    """hypothesis_id 缺失两态：default_filled=null（is None 与 ==[]
    类型级区分——空列表冒充全部默认是可断言的语义错误）。"""

    def test_random_adhoc_null_default_filled(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        sid = api_client.post(f"/api/research/translate/{hid}",
                              json={}).json()["strategy_id"]
        r = api_client.post("/api/research/random-window-test", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-10T00:00:00Z",
            "data_end": "2024-03-20T00:00:00Z",
            "window_bars": 60, "n_windows": 2, "seed": 7})
        assert r.status_code == 200, r.text
        disc = r.json()["risk_genes_disclosure"]
        assert disc["default_filled"] is None, \
            "adhoc 无谱系 → null（非空列表）"
        assert disc["values"], "values 仍完整回显"
        # 记录行读回同值
        result = _record_result(env["db"], "adhoc", sid, "random_window")
        assert result["risk_genes_disclosure"]["default_filled"] is None

    def test_staged_adhoc_null_default_filled(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        sid = api_client.post(f"/api/research/translate/{hid}",
                              json={}).json()["strategy_id"]
        r = api_client.post("/api/research/staged-validation", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-03-20T00:00:00Z",
            "segmentation": "auto"})
        assert r.status_code == 200, r.text
        disc = r.json()["risk_genes_disclosure"]
        assert disc["default_filled"] is None
        assert disc["values"]["stop_loss"] == 0.08


class TestCorruptedDeclarationStates:
    """CARD-04 特征锚（坏数据态——从实现行为记录防倒退）：披露归因
    三态收敛——正常块→差集；列 NULL/空→全集（D6 界线）；坏 JSON/
    非 dict 真值/假设行缺失→null（无从判定≠全部默认，不虚标全集）。
    行缺失态在 API 面被 404 前置拦截（validation_service:509），
    _risk_disclosure 的该分支为纵深防御层——单元直调锚定。"""

    def _dna_stub(self, stop_loss=0.05):
        from core.strategy.dna import RiskGenes
        rg = RiskGenes(stop_loss=stop_loss).to_dict()

        class _D:
            @staticmethod
            def to_json():
                return json.dumps({"risk_genes": rg})
        return _D()

    def test_row_missing_null_not_full_set(self, env):
        """行缺失（hypothesis_id 指向不存在行）→ null——原全集虚标
        收正的特征锚（修复前 declared=None 落全集标注）。"""
        from core.analysis.validation_service import _risk_disclosure
        disc = _risk_disclosure(env["db"], self._dna_stub(), "hyp_ghost")
        assert disc["default_filled"] is None, \
            "行缺失无从判定 → null（不虚标全集）"
        assert disc["values"], "values 仍完整"

    def test_corrupted_block_null(self, env):
        """声明块坏 JSON/非 dict 真值 → null（与正常块差集、合法
        NULL 全集三态可区分——同 null 值异因，对照断言防过收敛）。"""
        import sqlite3
        from core.analysis.validation_service import _risk_disclosure
        from core.analysis.hypothesis_service import register_hypothesis
        hid = register_hypothesis(
            env["db"], conditions=CONDITIONS, statement="s",
            data_ranges=_RANGES, strategy_params={"stop_loss": 0.08},
        )["hypothesis_id"]
        conn = sqlite3.connect(env["db"])
        with conn:
            conn.execute(
                "UPDATE research_hypotheses SET strategy_params=? "
                "WHERE hypothesis_id=?", ("[1, 2]", hid))
        conn.close()
        disc = _risk_disclosure(env["db"], self._dna_stub(0.08), hid)
        assert disc["default_filled"] is None, "坏块 → null"
        # 对照：同假设列 NULL（合法无声明）→ 全集差集标注非 null
        conn = sqlite3.connect(env["db"])
        with conn:
            conn.execute(
                "UPDATE research_hypotheses SET strategy_params=NULL "
                "WHERE hypothesis_id=?", (hid,))
        conn.close()
        disc2 = _risk_disclosure(env["db"], self._dna_stub(0.08), hid)
        assert isinstance(disc2["default_filled"], list) \
            and disc2["default_filled"], "合法 NULL → 全集（D6 保留）"


class TestDirectBuildMaterializedDefault:
    """A8 直建路径披露两要素（卡 A 入口分诊③——tester 缺口补齐）。

    POST /api/strategies 直建（dna.risk_genes 不写 stop_loss）→
    RiskGenesModel pydantic default 物化 0.05 落库（物化显式——与
    登记面「拒绝默认填充」分层的入口语义）；验证披露 default_filled
    =null 恒态（直建请求体不算声明面：schema 物化发生在验证前，
    落库后请求体键集信息已丢失——「物化与显式不可区分」恒态语义）；
    values 完整回显（含物化 stop_loss）。
    """

    def test_direct_build_materializes_and_validates_null(
            self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        dna = {
            "signal_genes": [
                {"indicator": "RSI", "params": {"period": 14},
                 "role": "entry_trigger", "field": None,
                 "condition": {"type": "lt", "threshold": 35}},
                {"indicator": "RSI", "params": {"period": 14},
                 "role": "exit_trigger", "field": None,
                 "condition": {"type": "gt", "threshold": 60}},
            ],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            # 不写 stop_loss——schema default 物化路径（对照 A2：登记面
            # 此形态被 F1 拒，直建面合法且物化）
            "risk_genes": {"take_profit": 0.10, "position_size": 0.3},
        }
        r = api_client.post("/api/strategies", json={
            "name": "直建披露", "dna": dna,
            "symbol": "BTCUSDT", "timeframe": "4h"})
        assert r.status_code == 201, r.text
        sid = r.json()["strategy_id"]
        # 物化显式：落库行 dna_json.risk_genes.stop_loss==0.05（非 None
        # 缺席——pydantic default 已写进落库事实）
        from core.persistence.repository.strategy import get_strategy
        row = get_strategy(env["db"], sid)
        assert json.loads(row["dna_json"])["risk_genes"]["stop_loss"] == 0.05

        # 验证披露两要素（adhoc 面——直建策略无假设谱系）
        rv = api_client.post("/api/research/random-window-test", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-10T00:00:00Z",
            "data_end": "2024-03-20T00:00:00Z",
            "window_bars": 60, "n_windows": 2, "seed": 7})
        assert rv.status_code == 200, rv.text
        disc = rv.json()["risk_genes_disclosure"]
        assert disc["default_filled"] is None, \
            "直建无谱系 → null 恒态（物化不可反推声明面）"
        assert disc["values"]["stop_loss"] == 0.05, "values 含物化值"
        assert {"stop_loss", "take_profit", "position_size",
                "leverage"} <= set(disc["values"]), "values 完整回显"
        # 记录行读回同值
        result = _record_result(env["db"], "adhoc", sid, "random_window")
        assert result["risk_genes_disclosure"]["default_filled"] is None
        assert result["risk_genes_disclosure"]["values"]["stop_loss"] == 0.05


class TestStagedWithHypothesis:
    """staged 挂假设：result_json 与返回体披露在场且同 translate 回执。"""

    def test_staged_disclosure_equals_receipt(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        tr = api_client.post(f"/api/research/translate/{hid}", json={})
        receipt = tr.json()
        sid = receipt["strategy_id"]
        r = api_client.post("/api/research/staged-validation", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-03-20T00:00:00Z",
            "segmentation": "auto", "hypothesis_id": hid})
        assert r.status_code == 200, r.text
        assert r.json()["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"]
        result = _record_result(env["db"], hid, sid, "staged_validation")
        assert result["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"]
