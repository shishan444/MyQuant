"""translate 幂等回执库存镜像锚（fr_296f43410afb 修复②）。

锚点面（断言从 design D5②「回执=库存事实镜像非重新构造」推导）：
- divergence 主锚：库存行 risk_genes 与任何重译构造值天然分叉的场
  景（声明 direction=short/max_hold/time_exit_utc/position_mode/
  risk_fraction 落库），重译幂等命中 → 回执五键逐字段读库存行回显
  （修复前幂等分支硬编码 long/None/None/fixed/None 占位——回执与
  库存不一致即缺陷②本体）。
- 手工 divergence 锚：库存行被外部更新（如运营修正）后重译，回执
  报库存新值而非重译构造值——「镜像」优先于「重构」的机械证明。
- exit-OR 回执键协同：幂等分支 logic_genes 读行回显形制不变（回
  归锚在 test_exit_group_logic_translation.py，此处补同一返回点五键
  与 logic_genes 并存的形制断言）。
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _cond() -> dict:
    return {
        "when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "lt", "target": 30}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 70}],
        "direction": "short",
    }


def _ranges() -> list:
    return [{"symbol": "ETHUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]


_DECLARED = {
    "max_hold": 6, "time_exit_utc": 600,
    "position_mode": "risk_budget", "risk_fraction": 0.25,
    "stop_loss": 0.05,
}


def _register(db: Path) -> str:
    out = register_hypothesis(
        db, conditions=_cond(), statement="回执镜像测试",
        data_ranges=_ranges(), strategy_params=dict(_DECLARED))
    return out["hypothesis_id"]


class TestIdempotentReceiptMirrorsInventory:
    """幂等分支回执五键读库存行（修复②验收面——修复前红）。"""

    def test_receipt_reflects_stored_row_all_fields(self, db):
        """divergence 主锚：声明 short/max_hold=6/time_exit=600/
        risk_budget/0.25 落库后重译 → 回执逐字段=库存行值（修复前
        硬编码 long/None/None/fixed/None——五键全部分叉即缺陷现场）。"""
        h = _register(db)
        first = translate_hypothesis(db, hypothesis_id=h)
        second = translate_hypothesis(db, hypothesis_id=h)
        assert second["duplicated"] is True
        assert second["strategy_id"] == first["strategy_id"]
        # 库存行事实（dna_json.risk_genes）
        row = get_strategy(db, second["strategy_id"])
        rg = json.loads(row["dna_json"])["risk_genes"]
        assert rg["direction"] == "short"
        # 回执五键逐字段镜像库存
        assert second["direction"] == rg["direction"] == "short"
        assert second["max_hold"] == rg["max_hold"] == 6
        assert second["time_exit_utc"] == rg["time_exit_utc"] == 600
        assert second["position_mode"] == rg["position_mode"] == "risk_budget"
        assert second["risk_fraction"] == rg["risk_fraction"] == 0.25

    def test_receipt_reflects_manual_row_update_not_retranslation(self, db):
        """手工 divergence 锚：库存行 risk_genes 被外部更新（模拟运营
        修正）→ 重译回执报库存新值而非重译构造值（镜像优先于重构）。"""
        from core.persistence.repository.strategy import update_strategy

        h = _register(db)
        first = translate_hypothesis(db, hypothesis_id=h)
        sid = first["strategy_id"]
        row = get_strategy(db, sid)
        dna = json.loads(row["dna_json"])
        dna["risk_genes"]["direction"] = "long"
        dna["risk_genes"]["max_hold"] = 99
        update_strategy(db, strategy_id=sid,
                        dna_json=json.dumps(dna, ensure_ascii=False))
        second = translate_hypothesis(db, hypothesis_id=h)
        assert second["duplicated"] is True
        # 库存已改 long/99（≠声明 short/6）——回执报库存事实
        assert second["direction"] == "long"
        assert second["max_hold"] == 99

    def test_receipt_defaults_when_row_risk_genes_missing(self, db):
        """缺段收敛锚：库存行 risk_genes 段缺失（畸形存量）→ 回执收敛
        缺省（long/None/None/fixed/None），不炸回执（_identity_
        reflection 值域收敛族）。"""
        from core.persistence.repository.strategy import update_strategy

        h = _register(db)
        first = translate_hypothesis(db, hypothesis_id=h)
        sid = first["strategy_id"]
        row = get_strategy(db, sid)
        dna = json.loads(row["dna_json"])
        dna.pop("risk_genes")
        update_strategy(db, strategy_id=sid,
                        dna_json=json.dumps(dna, ensure_ascii=False))
        second = translate_hypothesis(db, hypothesis_id=h)
        assert second["duplicated"] is True
        assert second["direction"] == "long"
        assert second["max_hold"] is None
        assert second["time_exit_utc"] is None
        assert second["position_mode"] == "fixed"
        assert second["risk_fraction"] is None

    def test_receipt_keeps_exit_or_logic_genes_key(self, db):
        """exit-OR 协同锚：幂等返回点五键镜像与 logic_genes 读行回显
        并存（add-exit-group-or D3 最小面键形制不受本修复影响——回执
        仍带 logic_genes 键且读行）。"""
        cond = _cond()
        out = register_hypothesis(
            db, conditions=cond, statement="镜像+logic_genes 并存",
            data_ranges=_ranges(),
            strategy_params=dict(_DECLARED), exit_logic="or")
        h = out["hypothesis_id"]
        first = translate_hypothesis(db, hypothesis_id=h)
        second = translate_hypothesis(db, hypothesis_id=h)
        assert second["duplicated"] is True
        assert "logic_genes" in second
        assert second["logic_genes"]["exit_logic"] == "OR"
        assert second["direction"] == "short"
