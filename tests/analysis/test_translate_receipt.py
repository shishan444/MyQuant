"""翻译回执身份三键测试（unify-tool-return-contract 任务 5，S2a-f）。

规则②：影响研究判断的身份信息（direction/max_hold/reuse_note）在
翻译回执回显——四返回点统一形制（禁只测 happy path）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext

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


def _hyp(db, conds=None, strategy_params=None) -> str:
    return register_hypothesis(
        db, conditions=conds or _CONDS, statement="s",
        data_ranges=_RANGES,
        strategy_params=strategy_params)["hypothesis_id"]


class TestTranslateReceiptThreeKeys:
    """S2a/S2b/S2c/S2e/S2f：新建与幂等分支三键回显。"""

    def test_s2a_default_three_keys(self, db):
        """S2a 默认三键：无 direction 无声明 → (long, null, null)。"""
        hid = _hyp(db)
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["direction"] == "long"
        assert out["max_hold"] is None
        assert out["reuse_note"] is None

    def test_s2b_short_echoed(self, db):
        """S2b short 假设产物回显 short。"""
        conds = dict(_CONDS, direction="short")
        hid = _hyp(db, conds=conds)
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["direction"] == "short"

    def test_s2c_max_hold_declared_echoed(self, db):
        """S2c 声明 max_hold=6 → 回显 int 6。"""
        hid = _hyp(db, strategy_params={"max_hold": 6})
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["max_hold"] == 6
        assert isinstance(out["max_hold"], int)

    def test_s2e_all_four_return_points(self, db):
        """S2e 四返回点各一例三键全在场：
        ①新建（:361）②谱系 draft 幂等（:221）③同签名 draft 分流（:339）
        ④同签名 active 分流（:329，same_dna_existing=True）。
        """
        from core.analysis.hypothesis_service import transition_hypothesis
        from core.persistence.repository import research as rr
        from core.persistence.repository.strategy import get_strategy

        hid = _hyp(db)
        first = translate_hypothesis(db, hypothesis_id=hid)      # ①新建
        replay = translate_hypothesis(db, hypothesis_id=hid)     # ②谱系 draft 幂等
        assert replay["duplicated"] is True
        sid = first["strategy_id"]

        # ③同签名 draft 分流：hid 终态后新假设同条件翻译——谱系无
        # draft，同签名分流命中既有 draft
        transition_hypothesis(db, hid, target_status="abandoned",
                              evidence_ref="s2 测试腾位")
        hid2 = _hyp(db)
        third = translate_hypothesis(db, hypothesis_id=hid2)     # ③draft 分流
        assert third["duplicated"] is True and \
            third.get("same_dna_existing") is False, "③应命中同签名 draft"

        # ④同签名 active 分流：独立条件链构造（转正绑定 parent 谱系，
        # 与 ①②③ 链解耦）——hidX 首译产 draft，pass 挂 (hidX, sidX)
        # 后 transition(hidX) 转正，hidY 同条件翻译命中 active
        _conds_b = {
            "when_conditions": [
                {"subject": {"indicator": "EMA", "params": {"period": 20}},
                 "action": "gt", "target": 55000}],
            "then_conditions": [
                {"subject": {"indicator": "EMA", "params": {"period": 50}},
                 "action": "lt", "target": 60000}],
        }
        hid_x = _hyp(db, conds=_conds_b)
        sid_x = translate_hypothesis(db, hypothesis_id=hid_x)["strategy_id"]
        rr.save_validation_with_log(
            db, record_id=f"rec_s2e_{sid_x[:8]}", hypothesis_id=hid_x,
            strategy_id=sid_x, level="isolated_backtest", params="{}",
            result=None, verdict="pass", range_hash=f"rh_s2e_{sid_x[:6]}")
        transition_hypothesis(db, hid_x, target_status="supported",
                              evidence_ref=f"rec_s2e_{sid_x[:8]}")
        assert get_strategy(db, sid_x)["status"] == "active", "转正前置"
        hid_y = _hyp(db, conds=_conds_b)
        out4 = translate_hypothesis(db, hypothesis_id=hid_y)     # ④active 分流
        assert out4.get("same_dna_existing") is True, "同签名 active 分流应命中"

        for out, tag in ((first, "①新建"), (replay, "②谱系幂等"),
                         (third, "③draft 分流"), (out4, "④active 分流")):
            for key in ("direction", "max_hold", "reuse_note"):
                assert key in out, f"{tag}分支缺 {key}"
        assert out4["reuse_note"] is not None, "S2d 前置"

    def test_s2f_idem_library_no_direction_converges_long(self, db):
        """S2f 幂等分支库内无 direction（存量记录）→ 收敛 "long"。"""
        hid = _hyp(db)
        first = translate_hypothesis(db, hypothesis_id=hid)
        # 库内产物 dna_json 抹掉 direction（模拟存量）
        from core.persistence.repository.strategy import get_strategy, update_strategy
        import json as _json
        row = get_strategy(db, first["strategy_id"])
        dna = _json.loads(row["dna_json"])
        dna["risk_genes"].pop("direction", None)
        update_strategy(db, strategy_id=first["strategy_id"], dna_json=_json.dumps(dna))
        replay = translate_hypothesis(db, hypothesis_id=hid)
        assert replay["duplicated"] is True
        assert replay["direction"] == "long", "库内无 direction 收敛 long"


class TestReuseNote:
    """S2d：same_dna_existing=true 时 reuse_note 三要素。"""

    def test_s2d_reuse_note_three_elements(self, db):
        """reuse_note 固定文案含三要素（共享复用/hypothesis_id 挂载/
        执行周期参数）。"""
        from core.persistence.repository.strategy import save_strategy
        from core.analysis.hypothesis_service import transition_hypothesis
        import json as _json

        hid = _hyp(db)
        first = translate_hypothesis(db, hypothesis_id=hid)
        sid = first["strategy_id"]
        # draft 经 pass 记录+transition 官方路径转正（假设随之终态）
        from core.persistence.repository import research as rr
        rr.save_validation_with_log(
            db, record_id=f"rec_s2d_{sid[:8]}", hypothesis_id=hid,
            strategy_id=sid, level="isolated_backtest", params="{}",
            result=None, verdict="pass", range_hash=f"rh_s2d_{sid[:6]}")
        transition_hypothesis(db, hid, target_status="supported",
                              evidence_ref=f"rec_s2d_{sid[:8]}")

        hid2 = _hyp(db)
        out = translate_hypothesis(db, hypothesis_id=hid2)
        assert out.get("same_dna_existing") is True, \
            f"预期同签名分流命中，got {out.get('strategy_id')}"
        assert out["reuse_note"] is not None
        for element in ("共享", "hypothesis_id", "验证请求参数"):
            assert element in out["reuse_note"], f"三要素缺「{element}」"

    def test_no_extra_duplicated_key(self, db):
        """A3：不新增 duplicated_by_same_dna 近义键。"""
        hid = _hyp(db)
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert "duplicated_by_same_dna" not in out
