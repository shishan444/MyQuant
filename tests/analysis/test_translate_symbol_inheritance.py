"""translate symbol 缺省继承锚（fr_296f43410afb 修复①——非分歧面）。

锚点面（断言从 design D5①「缺省值从 BTCUSDT 常量改为继承
data_ranges[0].symbol；显式传参优先」推导）：
- 缺省继承主锚（修复前红）：ETHUSDT 假设不传 strategy_symbol →
  产物 symbol=ETHUSDT（execution_genes.symbol 与 strategy 行
  symbol 列双面）——修复前缺省 BTCUSDT 常量在非 BTC 假设上产物
  错标（缺陷①本体，evidence=st_755c04c53a96 库存行）。
- 显式传 symbol 零变化：显式传参照常转化且产物=显式值。
- 守卫锚：data_ranges 畸形/首条目缺 symbol 键＋缺省请求 → 结构化
  拒绝 422（与 timeframe 继承守卫同款形制——param/need_hint/
  data_ranges 回显）。
- 兼容面：BTCUSDT 假设缺省请求产物仍 BTCUSDT（继承值恰为旧常量）。

幂等重译回纠（design D5① 后半）另列——见对质裁决后补锚。
"""

import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import (
    ResearchError, register_hypothesis)
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _ranges(symbol: str) -> list:
    return [{"symbol": symbol, "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z",
             "end": "2024-02-01T00:00:00Z"}]


_CONDS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14},
                     "field": "rsi"}, "action": "lt", "target": 35}],
    "then_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 30},
                     "field": "rsi"}, "action": "gt", "target": 60}],
}


def _hyp(db: Path, symbol: str) -> str:
    return register_hypothesis(
        db, conditions=_CONDS, statement="symbol 继承测试",
        data_ranges=_ranges(symbol),
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]


def _symbol_of(db: Path, sid: str) -> tuple:
    row = get_strategy(db, sid)
    dna = json.loads(row["dna_json"])
    return row["symbol"], dna["execution_genes"]["symbol"]


class TestSymbolDefaultInheritance:
    """缺省继承（修复①前半——修复前红）。"""

    @pytest.mark.parametrize("symbol", ["ETHUSDT", "BTCUSDT"])
    def test_default_inherits_range_source_symbol(self, db, symbol):
        """缺省请求产物 symbol=data_ranges[0].symbol（修复前恒
        BTCUSDT——ETHUSDT 格即缺陷①错标本体；BTCUSDT 格为兼容面：
        继承值恰为旧常量，产物不变）。"""
        hid = _hyp(db, symbol)
        out = translate_hypothesis(db, hypothesis_id=hid)
        col_sym, dna_sym = _symbol_of(db, out["strategy_id"])
        assert dna_sym == symbol
        assert col_sym == symbol

    def test_explicit_symbol_takes_precedence(self, db):
        """显式传 symbol 零变化：产物=显式值（用户指定优先——
        显式 ETHUSDT 传给 ETHUSDT 假设与缺省同值但路径独立）。"""
        hid = _hyp(db, "ETHUSDT")
        out = translate_hypothesis(
            db, hypothesis_id=hid, strategy_symbol="ETHUSDT")
        col_sym, dna_sym = _symbol_of(db, out["strategy_id"])
        assert dna_sym == "ETHUSDT"
        assert col_sym == "ETHUSDT"

    def test_explicit_symbol_cross_market_allowed(self, db):
        """显式跨市场传参维持既有自由度（显式 BTCUSDT 传给 ETHUSDT
        假设——修复不收窄显式路径，行为与修复前一致）。"""
        hid = _hyp(db, "ETHUSDT")
        out = translate_hypothesis(
            db, hypothesis_id=hid, strategy_symbol="BTCUSDT")
        _, dna_sym = _symbol_of(db, out["strategy_id"])
        assert dna_sym == "BTCUSDT"


class TestSymbolInheritanceGuard:
    """继承守卫（与 timeframe 守卫同款形制）。"""

    @pytest.mark.parametrize(
        "ranges_json",
        ['["4h"]', "[]"],
        ids=["首元素非dict", "空数组"])
    def test_malformed_ranges_rejected_422(self, db, ranges_json):
        """畸形 data_ranges＋缺省 symbol → 结构化拒绝（无继承源
        不静默落 None 触发 DB 层延迟爆炸——CARD-24 同族收口）。"""
        hid = _hyp(db, "ETHUSDT")
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE research_hypotheses SET data_ranges=? "
                "WHERE hypothesis_id=?", (ranges_json, hid))
        with pytest.raises(ResearchError) as exc:
            translate_hypothesis(
                db, hypothesis_id=hid, strategy_timeframe="4h")
        assert exc.value.code == "RESEARCH_PARAM_INVALID"
        assert exc.value.status == 422
        assert "symbol" in str(exc.value.message)
        assert exc.value.detail["param"] == "strategy_symbol"

    def test_missing_symbol_key_rejected_422(self, db):
        """首条目缺 symbol 键（手工数据）→ 同款拒绝（守卫覆盖
        键缺失形态——get("symbol") 假值含 None/空串）。"""
        hid = _hyp(db, "ETHUSDT")
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE research_hypotheses SET data_ranges=? "
                "WHERE hypothesis_id=?",
                (json.dumps([{"timeframe": "4h", "start": "2024-01-01",
                              "end": "2024-02-01"}]), hid))
        with pytest.raises(ResearchError) as exc:
            translate_hypothesis(
                db, hypothesis_id=hid, strategy_timeframe="4h")
        assert exc.value.code == "RESEARCH_PARAM_INVALID"
        assert exc.value.detail["param"] == "strategy_symbol"


def _mislabel_row(db: Path, sid: str) -> None:
    """把产物行手工错标为 BTCUSDT（模拟修复前缺省常量时代落库——
    st_755c04c53a96 库存形态的同构构造）。"""
    from core.persistence.repository.strategy import update_strategy
    row = get_strategy(db, sid)
    dna = json.loads(row["dna_json"])
    dna["execution_genes"]["symbol"] = "BTCUSDT"
    update_strategy(db, strategy_id=sid, symbol="BTCUSDT",
                    dna_json=json.dumps(dna, ensure_ascii=False, indent=2))


class TestIdempotentRetranslateCorrection:
    """幂等回纠（design D5① 后半——lead 裁 A 回写纠正）。"""

    def test_mislabeled_row_corrected_on_default_retranslate(self, db):
        """主锚：存量错标行（产物 BTCUSDT、假设区间源 ETHUSDT）缺省
        重译 → 策略行 symbol 列与 dna_json.execution_genes.symbol 均
        回纠为 ETHUSDT（不静默延续错标——缺陷①「幂等不更新」面）。"""
        hid = _hyp(db, "ETHUSDT")
        first = translate_hypothesis(db, hypothesis_id=hid)
        sid = first["strategy_id"]
        _mislabel_row(db, sid)
        second = translate_hypothesis(db, hypothesis_id=hid)
        assert second["duplicated"] is True
        assert second["strategy_id"] == sid, "回纠不重建（谱系幂等保持）"
        col_sym, dna_sym = _symbol_of(db, sid)
        assert col_sym == "ETHUSDT", "symbol 列回纠"
        assert dna_sym == "ETHUSDT", "dna_json.execution_genes.symbol 回纠"

    def test_explicit_symbol_retranslate_does_not_correct(self, db):
        """显式传参不触发回纠（用户意图优先）：错标行＋显式传
        BTCUSDT 重译 → 行保持 BTCUSDT（不覆盖显式决定）。"""
        hid = _hyp(db, "ETHUSDT")
        first = translate_hypothesis(db, hypothesis_id=hid)
        sid = first["strategy_id"]
        _mislabel_row(db, sid)
        second = translate_hypothesis(
            db, hypothesis_id=hid, strategy_symbol="BTCUSDT")
        assert second["duplicated"] is True
        col_sym, _ = _symbol_of(db, sid)
        assert col_sym == "BTCUSDT", "显式路径零回纠"

    def test_well_labeled_row_unchanged_on_retranslate(self, db):
        """非错标行缺省重译 → 行逐位不变（回纠判据不误伤正常幂等——
        无写发生的零副作用锚）。"""
        hid = _hyp(db, "ETHUSDT")
        first = translate_hypothesis(db, hypothesis_id=hid)
        sid = first["strategy_id"]
        before = get_strategy(db, sid)
        second = translate_hypothesis(db, hypothesis_id=hid)
        assert second["duplicated"] is True
        after = get_strategy(db, sid)
        assert before["symbol"] == after["symbol"] == "ETHUSDT"
        assert before["dna_json"] == after["dna_json"], \
            "正常行重译 dna_json 逐位不变（无回纠写）"

    def test_broken_dna_row_degrades_not_raises(self, db):
        """坏行降级锚（CARD-02 可达性两侧之一·base_tf 可解析侧）：
        错标行＋dna_json 损坏 → 缺省重译放行重建不炸，新行 symbol
        正确标注（错标的「不静默延续」由重建天然达成），回执正常回显。

        可达性判定（tester 6aab9149 复核修正后结论——两侧并存）：
        base_tf 可解析（登记面形制）时，坏 dna_json 行在
        _find_draft_by_parent 幂等查询即因 exec_tf 不可解析判「漂移
        draft 放行重建」——本侧到不了回纠段；**base_tf=None**（
        data_ranges 条目缺 timeframe——手工/历史库行可绕过登记面）
        时 :1261 不进 exec_tf 收窄、退回现行幂等——坏行被谱系命中
        进入回纠段，降级分支真实可达（可达面锚见下一
        test_broken_dna_row_hit_degrades_in_correction_segment——
        修复前该路径裸炸 JSONDecodeError 裸穿错误通道，即 CARD-02
        修复的真实承载面；security 线索 Low→architect 裁 P2 入卡）。"""
        from core.persistence.repository.strategy import update_strategy

        hid = _hyp(db, "ETHUSDT")
        first = translate_hypothesis(db, hypothesis_id=hid)
        sid = first["strategy_id"]
        # 错标＋坏 dna_json 双重构造（base_tf 可解析侧：坏 JSON 在
        # 幂等查询即放行重建——漂移收窄行为面）
        update_strategy(db, strategy_id=sid, symbol="BTCUSDT",
                        dna_json="{not-valid-json")
        second = translate_hypothesis(db, hypothesis_id=hid)
        assert second["duplicated"] is False, "坏行放行重建（幂等不命中）"
        assert second["strategy_id"] != sid
        # 新行正确标注（缺省继承链承载——错标未延续）；坏旧行保留审计
        col_sym, dna_sym = _symbol_of(db, second["strategy_id"])
        assert col_sym == "ETHUSDT"
        assert dna_sym == "ETHUSDT"
        assert get_strategy(db, sid)["dna_json"] == "{not-valid-json"

    def test_broken_dna_row_hit_degrades_in_correction_segment(self, db):
        """坏行命中降级锚（CARD-02 可达面——与上一锚构成可达性两侧）：
        base_tf=None（data_ranges 条目缺 timeframe——手工/历史库行可
        绕过登记面 RESEARCH_RANGE_INVALID 校验的形态）时
        _find_draft_by_parent :1261 不进 exec_tf 收窄分支、退回现行
        幂等——坏 dna_json 错标行被谱系命中**进入回纠段**：修复前
        （5d2264c3^）该路径裸炸 JSONDecodeError 裸穿错误通道，修复后
        跳过回纠返回存量行（错标保持、行逐位不变、回执经 _identity_
        reflection 值域收敛正常回显）。降级分支真实可达非死代码。"""
        from core.persistence.repository.strategy import update_strategy

        hid = _hyp(db, "ETHUSDT")
        first = translate_hypothesis(db, hypothesis_id=hid)
        sid = first["strategy_id"]
        # 库行 UPDATE 构造 base_tf=None（条目去 timeframe）——登记面
        # 会拒此形制（RESEARCH_RANGE_INVALID），故经库行构造
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE research_hypotheses SET data_ranges=?"
                " WHERE hypothesis_id=?",
                (json.dumps([{"symbol": "ETHUSDT",
                              "start": "2024-01-01T00:00:00Z",
                              "end": "2024-02-01T00:00:00Z"}]), hid))
            conn.commit()
        # 错标＋坏 dna_json 双构造（同上一锚；差异面在 base_tf）
        update_strategy(db, strategy_id=sid, symbol="BTCUSDT",
                        dna_json="{not-valid-json")
        second = translate_hypothesis(db, hypothesis_id=hid,
                                      strategy_timeframe="4h")
        assert second["duplicated"] is True, \
            "base_tf=None 退回现行幂等（坏行命中而非放行重建）"
        assert second["strategy_id"] == sid
        row = get_strategy(db, sid)
        assert row["symbol"] == "BTCUSDT", "坏 dna_json 回纠被跳过（错标保持）"
        assert row["dna_json"] == "{not-valid-json", "存量行逐位不变"
        assert second["direction"] == "long", \
            "回执值域收敛正常回显（不依赖坏行可解析）"
        # 回执正常回显（新建分支——不依赖坏行可解析）
        assert second["direction"] == "long"
