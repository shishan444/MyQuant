"""止损显式声明强制（research-execution-config-alignment 1.4，卡 D'
/裁决一——C1-F 组 F1-F7）。

两判据：①登记链 3.6 步分流点 strategy_params None 一律 422（「None
不参与比对」与「有组无值→空 dict 字面化」两态收拢）；②
_validate_strategy_params 内 stop_loss 键在场强制（档位声明不冒充
数值声明）。三态各归其错：null→CARD-01 既有拒、越域→分叉值域拒、
缺失→本判据拒。拒绝时零写入。存量界线（D6）：已落库假设的
translate/幂等重放/验证不经登记链照旧成功（登记时拦、翻译不拦）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr

_CONDS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}},
         "action": "lt", "target": 35}],
    "then_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 30}},
         "action": "gt", "target": 60}],
}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


def _count_hypotheses(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT count(*) FROM research_hypotheses").fetchone()[0]
    finally:
        conn.close()


class TestF1FourStatesRejected:
    """F1/F2/F3：四态拒绝（None/空 dict/有块无 stop_loss/仅 sl_mode）
    +sl_mode 三档 legal_range 分叉+档位冒充反例。"""

    @pytest.mark.parametrize("params,field,legal", [
        (None, "strategy_params", None),                     # ①None
        ({}, "strategy_params.stop_loss", [0.005, 0.2]),     # ②空 dict
        ({"max_hold": 6}, "strategy_params.stop_loss",
         [0.005, 0.2]),                                      # ③有块无 SL
        ({"sl_mode": "pct"}, "strategy_params.stop_loss",
         [0.005, 0.2]),                                      # ④仅 sl_mode
        ({"sl_mode": "trail"}, "strategy_params.stop_loss",
         [0.005, 0.2]),                                      # trail 档分叉
        ({"sl_mode": "atr"}, "strategy_params.stop_loss",
         [0.5, 5.0]),                                        # atr 档分叉+冒充反例
    ])
    def test_four_states_422(self, db, params, field, legal):
        n0 = _count_hypotheses(db)
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=_CONDS, statement="s",
                data_ranges=_RANGES, strategy_params=params)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["field"] == field
        if legal is not None:
            assert ei.value.detail["legal_range"] == legal, \
                "legal_range 按 sl_mode 分叉（缺省按 pct 档）"
        assert "stop_loss" in ei.value.detail.get(
            "hint", "") + str(ei.value.message), "强制声明指引在场"
        # 拒绝时零写入（校验先于落库）
        assert _count_hypotheses(db) == n0


class TestF4ExistingChannelsUnchanged:
    """F4 既有面不动：null→CARD-01；越域→分叉值域拒（互补不重叠）。"""

    def test_null_stop_loss_card01(self, db):
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=_CONDS, statement="s",
                data_ranges=_RANGES,
                strategy_params={"stop_loss": None})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["field"] == "strategy_params.stop_loss"
        assert "null_keys" in ei.value.detail or \
            "显式 null" in ei.value.message

    @pytest.mark.parametrize("params,legal", [
        ({"stop_loss": 0.5}, [0.005, 0.2]),
        ({"stop_loss": 0.001}, [0.005, 0.2]),
        ({"sl_mode": "atr", "stop_loss": 0.05}, [0.5, 5.0]),
    ])
    def test_out_of_range_value_domain(self, db, params, legal):
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=_CONDS, statement="s",
                data_ranges=_RANGES, strategy_params=params)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["legal_range"] == legal


class TestF5LegacyTranslatePath:
    """F5 存量界线（D6 行为级）：存量假设（无声明）translate 成功+
    披露继续标注默认填充（登记时拦、翻译不拦）。"""

    def test_legacy_translate_succeeds_with_default_disclosure(self, db):
        rr.save_hypothesis_with_log(
            db, hypothesis_id="hyp_f5",
            conditions=json.dumps(_CONDS), conditions_hash="ch_f5",
            statement="s", data_ranges=json.dumps(_RANGES),
            ranges_hash="rh_f5")
        out = translate_hypothesis(db, hypothesis_id="hyp_f5")
        disc = out["risk_genes_disclosure"]
        assert out["risk_genes"] == "platform_default"
        assert "stop_loss" in disc["default_filled"]
        assert disc["values"]["stop_loss"] == 0.05


class TestF6ReplayAndReregistration:
    """F6 拦截口径：登记链一律过校验（含同指纹重放）；存量原样下游
    （translate）不过登记链照旧成功。"""

    def test_reregistration_without_block_rejected(self, db):
        first = register_hypothesis(
            db, conditions=_CONDS, statement="s", data_ranges=_RANGES,
            strategy_params={"stop_loss": 0.08})
        stored = json.loads(rr.get_hypothesis(
            db, first["hypothesis_id"])["strategy_params"])
        assert stored == {"stop_loss": 0.08}, \
            "F7 合法登记零变化：归一形落库回读"
        n0 = _count_hypotheses(db)
        # 同条件同区间无块重登记（含重放语义）→ 3.6 步拒（零写入）
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=_CONDS, statement="s", data_ranges=_RANGES)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert _count_hypotheses(db) == n0

    def test_idempotent_replay_with_same_block(self, db):
        """同块重放幂等不受新校验影响（归一形全等比对分支保留）。"""
        a = register_hypothesis(
            db, conditions=_CONDS, statement="s", data_ranges=_RANGES,
            strategy_params={"stop_loss": 0.08, "max_hold": 6})
        b = register_hypothesis(
            db, conditions=_CONDS, statement="s", data_ranges=_RANGES,
            strategy_params={"stop_loss": 0.08, "max_hold": 6})
        assert b["duplicated"] is True
        assert b["hypothesis_id"] == a["hypothesis_id"]


class TestF7LegalPathUnchanged:
    """F7 禁止变化面：stop_loss 显式声明的合法登记行为零变化（非 SL
    域字段不受强制校验影响）。"""

    def test_legal_registration_echoes_params(self, db):
        out = register_hypothesis(
            db, conditions=_CONDS, statement="s", data_ranges=_RANGES,
            strategy_params={"stop_loss": 0.08, "take_profit": 0.15,
                             "max_hold": 6, "leverage": 2})
        assert out["hypothesis_id"].startswith("hyp_")
        stored = json.loads(rr.get_hypothesis(
            db, out["hypothesis_id"])["strategy_params"])
        assert stored["stop_loss"] == 0.08
        assert stored["max_hold"] == 6
        # 产物链贯通：translate 落库声明值
        t = translate_hypothesis(db, hypothesis_id=out["hypothesis_id"])
        assert t["risk_genes_disclosure"]["values"]["stop_loss"] == 0.08
        assert "stop_loss" not in \
            t["risk_genes_disclosure"]["default_filled"]
