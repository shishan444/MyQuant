"""出场组内 OR 通道——翻译构造与回执纯增键（add-exit-group-or 任务 2.1/2.2）。

覆盖 tasks 完成标准：
- 小写→大写映射：conditions 层 "or" → 产物 logic_genes.exit_logic="OR"
  （partial 通道→reduce_logic="OR"，邻通道保持缺省 "AND"）
- 无键路径产物逐位不变：无键/显式 "and"/缺省三形翻译产物 dna_json
  逐位一致（归一化「or」才写键的产物级证据）
- 回执纯增 logic_genes 键：存在性＋生效值（最小面——缺省 AND 与
  显式 OR 两形均回显实际生效值；幂等/分流返回点同形）
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
    }


def _ranges() -> list:
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]


def _register(db: Path, *, conditions=None, exit_logic=None,
              partial_exit_logic=None) -> str:
    out = register_hypothesis(
        db, conditions=conditions or _cond(), statement="组内 OR 翻译测试",
        data_ranges=_ranges(), strategy_params={"stop_loss": 0.05},
        exit_logic=exit_logic, partial_exit_logic=partial_exit_logic)
    return out["hypothesis_id"]


def _translate_dna(db: Path, hypothesis_id: str) -> tuple:
    t = translate_hypothesis(db, hypothesis_id=hypothesis_id)
    row = get_strategy(db, t["strategy_id"])
    return t, json.loads(row["dna_json"])


class TestLogicGenesConstruction:
    """任务 2.1：小写→大写映射与无键路径逐位不变。"""

    def test_or_maps_uppercase_exit_logic(self, db):
        """exit_logic="or" → 产物 logic_genes.exit_logic=="OR"（conditions
        层小写 → LogicGenes 层大写映射）；邻通道 add/reduce 保持缺省。"""
        h = _register(db, exit_logic="or")
        _, dna = _translate_dna(db, h)
        assert dna["logic_genes"]["exit_logic"] == "OR"
        assert dna["logic_genes"]["reduce_logic"] == "AND"
        assert dna["logic_genes"]["add_logic"] == "AND"

    def test_partial_or_maps_uppercase_reduce_logic(self, db):
        """partial_exit_logic="or" → reduce_logic=="OR"；exit 通道保持
        缺省（两通道独立）。"""
        cond = _cond()
        cond["exit_partial_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 75}]
        h = _register(db, conditions=cond, partial_exit_logic="or")
        _, dna = _translate_dna(db, h)
        assert dna["logic_genes"]["reduce_logic"] == "OR"
        assert dna["logic_genes"]["exit_logic"] == "AND"

    def test_both_channels_or(self, db):
        """两通道同时 or：exit_logic 与 reduce_logic 均 "OR"。"""
        cond = _cond()
        cond["exit_partial_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 75}]
        h = _register(db, conditions=cond, exit_logic="or",
                      partial_exit_logic="or")
        _, dna = _translate_dna(db, h)
        assert dna["logic_genes"]["exit_logic"] == "OR"
        assert dna["logic_genes"]["reduce_logic"] == "OR"

    @pytest.mark.parametrize(
        "explicit_and", [None, "and"], ids=["缺省不传", "显式and"])
    def test_and_forms_bitwise_identical(self, db, explicit_and):
        """无键（缺省不传）与显式 "and" 两形翻译产物 dna_json 逐位
        一致——归一化「or」才写键，显式 and 与缺省同产物（dict 级
        逐位比对，含 logic_genes 四键全 "AND" 缺省形）。"""
        h_plain = _register(db)
        _, dna_plain = _translate_dna(db, h_plain)
        h_and = _register(db, exit_logic=explicit_and)
        _, dna_and = _translate_dna(db, h_and)
        assert dna_and == dna_plain
        assert dna_plain["logic_genes"] == {
            "entry_logic": "AND", "exit_logic": "AND",
            "add_logic": "AND", "reduce_logic": "AND"}

    def test_explicit_and_partial_form_bitwise_identical(self, db):
        """partial 通道显式 "and" 同款归一：带组假设显式
        partial_exit_logic="and" 与无键产物逐位一致。"""
        cond = _cond()
        cond["exit_partial_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 75}]
        h_plain = _register(db, conditions=cond)
        _, dna_plain = _translate_dna(db, h_plain)
        h_and = _register(db, conditions=cond, partial_exit_logic="and")
        _, dna_and = _translate_dna(db, h_and)
        assert dna_and == dna_plain

    def test_or_form_add_channel_stays_and(self, db):
        """Q-E2 负向锚：exit_logic="or" 产物 add_logic 无 OR 通道——
        add_trigger/add_suppress 组不设逻辑键（加仓语义维持合取）。"""
        cond = _cond()
        cond["add_trigger_conditions"] = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "lt", "target": 35}]
        h = _register(db, conditions=cond, exit_logic="or")
        _, dna = _translate_dna(db, h)
        assert dna["logic_genes"]["add_logic"] == "AND"


class TestReceiptLogicGenes:
    """任务 2.2：回执纯增 logic_genes 键（最小面——存在性＋生效值）。"""

    def test_new_product_receipt_or_effective(self, db):
        """新建路径：回执 logic_genes.exit_logic 回显实际生效值 "OR"。"""
        h = _register(db, exit_logic="or")
        t = translate_hypothesis(db, hypothesis_id=h)
        assert t["logic_genes"]["exit_logic"] == "OR"

    def test_new_product_receipt_default_and(self, db):
        """缺省路径：回显实际生效值全 "AND"。"""
        h = _register(db)
        t = translate_hypothesis(db, hypothesis_id=h)
        assert t["logic_genes"]["exit_logic"] == "AND"
        assert t["logic_genes"]["reduce_logic"] == "AND"

    def test_idempotent_retranslate_receipt_from_row(self, db):
        """幂等路径（谱系 draft 已存在即返回）：重译回执 logic_genes
        读行回显实际生效值（披露的就是落库的）。"""
        h = _register(db, exit_logic="or")
        first = translate_hypothesis(db, hypothesis_id=h)
        second = translate_hypothesis(db, hypothesis_id=h)
        assert second["duplicated"] is True
        assert second["strategy_id"] == first["strategy_id"]
        assert second["logic_genes"]["exit_logic"] == "OR"

    def test_same_dna_divert_receipt_from_row(self, db):
        """分流路径（同签名异基因型→建新；同签名且等价→命中既有）：
        同条件 or/and 两假设产物签名异（xlor 段），各自独立建新——
        第二个回执回显自身产物生效值。"""
        h_and = _register(db)
        t_and = translate_hypothesis(db, hypothesis_id=h_and)
        h_or = _register(db, exit_logic="or")
        t_or = translate_hypothesis(db, hypothesis_id=h_or)
        assert t_or["strategy_id"] != t_and["strategy_id"]
        assert t_or["same_dna_existing"] is False
        assert t_or["logic_genes"]["exit_logic"] == "OR"
        assert t_and["logic_genes"]["exit_logic"] == "AND"

    def test_active_reuse_receipt_from_row(self, db):
        """active 命中返回点（回执四返回点收尾——新建/幂等/分流建新
        三点已有，本点补齐）：假设 A（or）翻译得 draft D → 挂 pass
        验证记录 → 经生命周期迁移服务（credential 唯一合法通道）转正
        active → 假设 B（同 conditions 同 symbol/timeframe、异区间
        start——conditions+ranges 双 hash 之一异即独立身份）翻译 →
        同签名且基因型等价命中 active D → reuse 回执 logic_genes 读
        行回显 "OR"（披露的就是落库的）。"""
        from sqlalchemy import insert

        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import ValidationRecord
        from core.strategy.lifecycle.service import transit

        # A：登记（or）→ 翻译得 draft D
        h_a = _register(db, exit_logic="or")
        t_a = translate_hypothesis(db, hypothesis_id=h_a)
        d_id = t_a["strategy_id"]

        # 挂 (hypothesis_id, strategy_id, verdict=pass) 凭证 → 转正
        rid = "vr_active_reuse_probe"
        from core.persistence.timestamps import utc_now_iso as _now
        with engine_for(db).begin() as conn:
            conn.execute(insert(ValidationRecord).values(
                record_id=rid, hypothesis_id=h_a, strategy_id=d_id,
                level="isolated_backtest", params="{}", verdict="pass",
                range_hash="active-reuse-probe", created_at=_now()))
        out = transit(db, d_id, target="active", trigger="credential",
                      evidence={"record_ids": [rid]})
        assert out["migrated"] is True

        # B：同 conditions 同 symbol/timeframe、异 start（独立身份、
        # 同 DNA）→ 翻译命中 active D
        ranges_b = [{"symbol": "BTCUSDT", "timeframe": "4h",
                     "start": "2024-02-01T00:00:00Z",
                     "end": "2024-06-01T00:00:00Z"}]
        reg_b = register_hypothesis(
            db, conditions=_cond(), statement="active reuse B",
            data_ranges=ranges_b, strategy_params={"stop_loss": 0.05},
            exit_logic="or")
        assert reg_b["duplicated"] is False  # ranges 异 → 独立假设
        t_b = translate_hypothesis(db, hypothesis_id=reg_b["hypothesis_id"])
        assert t_b["same_dna_existing"] is True
        assert t_b["strategy_id"] == d_id
        assert t_b["logic_genes"]["exit_logic"] == "OR"
