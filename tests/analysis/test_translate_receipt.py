"""翻译回执身份三键测试（unify-tool-return-contract 任务 5，S2a-f）。

规则②：影响研究判断的身份信息（direction/max_hold/reuse_note）在
翻译回执回显——四返回点统一形制（禁只测 happy path）。

research-execution-config-alignment 1.1 增：TestRiskGenesDisclosure
（C1-A 组——执行配置全量披露：values 库内行回读同源+default_filled
声明块差集，新登记/存量回放双路径拆分+钻石锚）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytestmark = [pytest.mark.integration]

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


_NO_SP = object()  # 哨兵：未传补最小合法块；显式 None（负锚）透传


def _hyp(db, conds=None, strategy_params=_NO_SP) -> str:
    if strategy_params is _NO_SP:
        strategy_params = {"stop_loss": 0.05}
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
        hid = _hyp(db, strategy_params={"stop_loss": 0.05, "max_hold": 6})
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
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            db, record_id=f"rec_s2e_{sid_x[:8]}", hypothesis_id=hid_x,
            strategy_id=sid_x, level="isolated_backtest", params={},
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
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            db, record_id=f"rec_s2d_{sid[:8]}", hypothesis_id=hid,
            strategy_id=sid, level="isolated_backtest", params={},
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


class TestIntentionalBoundaryNote:
    """S10：跨品种有意边界标注（unify-tool-return-contract 任务 9）。"""

    def test_s10_cross_symbol_note_present(self, db):
        """跨品种拒绝 detail 附「有意设计边界」标注短语（纯文案锚）。"""
        from core.analysis.errors import ResearchError
        from core.persistence.repository import research as rr
        import json as _json
        ranges_multi = [
            {"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
            {"symbol": "ETHUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
        ]
        # 登记面拦截同 symbol 校验——跨品种假设经库内直插构造（该
        # 分支是对存量/历史形态的防御，绕过登记面直测翻译防线）
        hid = "hyp_cross_symbol_s10"
        rr.save_hypothesis_with_log(
            db, hypothesis_id=hid,
            conditions=_json.dumps(_CONDS),
            conditions_hash="ch_s10_cross", statement="s",
            data_ranges=_json.dumps(ranges_multi),
            ranges_hash="rh_s10_cross")
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"
        assert "有意设计边界" in ei.value.detail.get("note", ""), \
            "detail.note 缺有意边界标注"


class TestRiskGenesDisclosure:
    """C1-A 组（卡 A 披露契约）：单一组装双消费的 translate 侧面。

    values=落库 dna_json.risk_genes 原样回显（禁两处组装——同源断言）；
    default_filled=库内实际键集−声明块键集（固定序）；新登记/存量回放
    双路径各自断言（A2 拆分——数据面可反推声明路径）。
    """

    def _row_risk_genes(self, db, strategy_id):
        from core.persistence.repository.strategy import get_strategy
        return json.loads(
            get_strategy(db, strategy_id)["dna_json"])["risk_genes"]

    def test_a1_values_mirror_stored_row(self, db):
        """A1：新建分支 values==落库行 risk_genes 逐键相等；键集=7
        恒写键+已声明条件键，不多不少（to_dict 形制断言）。"""
        hid = _hyp(db, strategy_params={"stop_loss": 0.08})
        out = translate_hypothesis(db, hypothesis_id=hid)
        disc = out["risk_genes_disclosure"]
        assert disc["values"] == self._row_risk_genes(db, out["strategy_id"])
        # C4-E1 演进（dynamic-stop-loss-series）：非 series DNA 16 键
        # 基线逐键不变（series 档新增 sl_series_source 条件键——
        # 非 series 声明不携带则不落库，键集仍 7 恒写键）
        assert set(disc["values"]) == {
            "stop_loss", "take_profit", "position_size", "leverage",
            "direction", "sl_mode", "atr_period"}
        assert disc["values"]["stop_loss"] == 0.08

    def test_a2_new_registration_path(self, db):
        """A2 新登记路径（C4-E1 限定「非 series 档」）：显式声明
        stop_loss → 不入 default_filled 且 values 回显声明值；
        risk_origin=declared（CARD-03 回归④）。series 档新登记
        default_filled 含 stop_loss 为合法恒态（结构性残留非行为性
        默认——C4-A5 分态，承载于 test_sl_series_declaration）。"""
        hid = _hyp(db, strategy_params={"stop_loss": 0.08})
        out = translate_hypothesis(db, hypothesis_id=hid)
        disc = out["risk_genes_disclosure"]
        assert "stop_loss" not in disc["default_filled"]
        assert disc["values"]["stop_loss"] == 0.08
        assert out["risk_genes"] == "declared"

    def test_a2_legacy_replay_path(self, db):
        """A2 存量回放路径：strategy_params NULL（直插存量形）→
        stop_loss ∈ default_filled 且值==平台默认 0.05；
        risk_origin=platform_default（D6 存量界线——翻译不拦）。"""
        rr.save_hypothesis_with_log(
            db, hypothesis_id="hyp_legacy_a2",
            conditions=json.dumps(_CONDS), conditions_hash="ch_a2_legacy",
            statement="s", data_ranges=json.dumps(_RANGES),
            ranges_hash="rh_a2_legacy")
        out = translate_hypothesis(db, hypothesis_id="hyp_legacy_a2")
        disc = out["risk_genes_disclosure"]
        assert "stop_loss" in disc["default_filled"]
        assert disc["values"]["stop_loss"] == 0.05
        assert out["risk_genes"] == "platform_default"

    def test_default_filled_fixed_order(self, db):
        """default_filled 按固定序输出（结构断言——序对照常量不硬编码
        字面量；default_filled ⊆ 固定序∩库内键集）。"""
        from core.analysis.hypothesis_to_strategy import (
            _RISK_GENES_DISCLOSURE_ORDER as ORDER)
        hid = _hyp(db, strategy_params={"stop_loss": 0.08, "max_hold": 6})
        out = translate_hypothesis(db, hypothesis_id=hid)
        disc = out["risk_genes_disclosure"]
        expected = [k for k in ORDER
                    if k in disc["values"]
                    and k not in {"stop_loss", "max_hold"}]
        assert disc["default_filled"] == expected, \
            "default_filled 须按固定序过滤输出"

    def test_a4_idempotent_replay_isomorphic(self, db):
        """A4：同假设重复 translate → 披露逐键同构；幂等分支 values==
        行回读（库内权威事实，非当前构造对象）。"""
        hid = _hyp(db, strategy_params={"stop_loss": 0.08})
        first = translate_hypothesis(db, hypothesis_id=hid)
        replay = translate_hypothesis(db, hypothesis_id=hid)
        assert replay["duplicated"] is True
        assert replay["risk_genes_disclosure"] == \
            first["risk_genes_disclosure"]
        assert replay["risk_genes_disclosure"]["values"] == \
            self._row_risk_genes(db, replay["strategy_id"])

    def test_a5_declared_all_reachable_keys(self, db):
        """A5 集成态：声明块覆盖全部可声明键（11 键——direction 与
        atr_period 不在声明块白名单、分批三键须宿主组不纳入）→
        default_filled 恰为 ["direction", "atr_period"]（唯二残留）。"""
        full = {"stop_loss": 0.05, "take_profit": 0.10, "sl_mode": "pct",
                "position_size": 0.3, "leverage": 2,
                "max_hold": 6, "time_exit_utc": 600,
                "position_mode": "fixed", "risk_fraction": 0.02,
                "initial_fraction": 0.5, "max_position_loss": 5000.0}
        hid = _hyp(db, conds=dict(_CONDS, direction="short"),
                   strategy_params=full)
        out = translate_hypothesis(db, hypothesis_id=hid)
        disc = out["risk_genes_disclosure"]
        assert disc["default_filled"] == ["direction", "atr_period"], \
            "全键显式下残留应恰为 direction+atr_period（白名单无此二键）"
        assert disc["values"]["direction"] == "short"

    def test_a5_unit_sixteen_declared_empty(self):
        """A5 单元边界：声明块覆盖库内全部键（直插形——登记面不可达）
        → default_filled==[]；解析失败 → values={} 且 default_filled=[]
        （不炸回执）。"""
        from core.analysis.hypothesis_to_strategy import (
            _risk_genes_disclosure)
        dna_rg = {"stop_loss": 0.05, "take_profit": 0.1,
                  "position_size": 0.3, "leverage": 2, "direction": "long",
                  "sl_mode": "pct", "atr_period": 14}
        disc = _risk_genes_disclosure(
            json.dumps({"risk_genes": dna_rg}), dict(dna_rg))
        assert disc["default_filled"] == []
        assert _risk_genes_disclosure(
            "{not json", {"stop_loss": 0.08}) == \
            {"values": {}, "default_filled": []}

    def test_a7_diamond_same_dna_two_declared_faces(self, db):
        """A7 钻石锚：显式声明恰等默认值（A）与无声明存量（B）→ 同
        risk_genes 值 → 分流同一策略实体；披露各按声明面（A 无标注
        declared/B 含标注 platform_default）——把 A 的显式声明错标
        默认填充即红。"""
        hid_a = _hyp(db, strategy_params={"stop_loss": 0.05})
        out_a = translate_hypothesis(db, hypothesis_id=hid_a)
        assert out_a["risk_genes"] == "declared"
        assert "stop_loss" not in \
            out_a["risk_genes_disclosure"]["default_filled"]
        assert out_a["risk_genes_disclosure"]["values"]["stop_loss"] == 0.05

        rr.save_hypothesis_with_log(
            db, hypothesis_id="hyp_b_a7",
            conditions=json.dumps(_CONDS), conditions_hash="ch_a7_b",
            statement="s", data_ranges=json.dumps(_RANGES),
            ranges_hash="rh_a7_b")
        out_b = translate_hypothesis(db, hypothesis_id="hyp_b_a7")
        assert out_b["strategy_id"] == out_a["strategy_id"], \
            "同基因值应分流同一 draft 实体"
        assert out_b["duplicated"] is True
        assert out_b["risk_genes"] == "platform_default"
        disc_b = out_b["risk_genes_disclosure"]
        assert "stop_loss" in disc_b["default_filled"]
        assert disc_b["values"]["stop_loss"] == 0.05
        # 3a 稳定锚：同一假设重复挂载 → 披露逐键稳定
        out_b2 = translate_hypothesis(db, hypothesis_id="hyp_b_a7")
        assert out_b2["risk_genes_disclosure"] == disc_b

    def test_default_filled_set_identity_property(self):
        """闸门④属性补强：差集恒等式全称（固定种子随机采样 declared
        子集 × 库内键集）——default_filled == ORDER ∩ values 键集 −
        declared 非 null 键集，且 ⊆ values 键集（标注的必是回显的）。"""
        from core.analysis.hypothesis_to_strategy import (
            _RISK_GENES_DISCLOSURE_ORDER as ORDER,
            _risk_genes_disclosure)
        rng = np.random.default_rng(11)
        pool = list(ORDER) + ["max_hold", "time_exit_utc", "risk_fraction",
                              "initial_fraction", "max_position_loss"]
        for _ in range(30):
            n_values = int(rng.integers(1, len(pool) + 1))
            value_keys = list(rng.choice(pool, size=n_values, replace=False))
            dna_rg = {k: 0.05 for k in value_keys}
            declared = {k: (None if rng.random() < 0.3 else 0.1)
                        for k in rng.choice(
                            pool, size=int(rng.integers(0, 6)),
                            replace=False)}
            disc = _risk_genes_disclosure(
                json.dumps({"risk_genes": dna_rg}), dict(declared))
            expected = [k for k in ORDER
                        if k in dna_rg and declared.get(k) is None]
            assert disc["default_filled"] == expected, \
                f"差集恒等式破坏: values={sorted(dna_rg)} declared={declared}"
            assert set(disc["default_filled"]) <= set(disc["values"]), \
                "标注集不得超出回显键集"

    def test_corrupted_block_null_disclosure(self, db):
        """CARD-04 特征锚（坏数据态——从实现行为记录防倒退）：存量行
        strategy_params 坏 JSON/非 dict 真值（登记面 F1 不可达，仅
        库内坏数据形态）→ translate 200 不炸；产物走平台默认构造
        （origin=platform_default）；default_filled=null（无从判定≠
        全部默认——不虚标全集）。对照：合法列 NULL→全集标注（D6
        界线，test_a2_legacy 承载）。"""
        import sqlite3
        bad_raw = ["{not json", "[1, 2]", "123"]  # 坏JSON/list/数字真值
        for i, raw in enumerate(bad_raw):
            hid = f"hyp_corrupt_{i}"
            rr.save_hypothesis_with_log(
                db, hypothesis_id=hid, conditions=json.dumps(_CONDS),
                conditions_hash=f"ch_{hid}", statement="s",
                data_ranges=json.dumps(_RANGES),
                ranges_hash=f"rh_{hid}")
            conn = sqlite3.connect(db)
            with conn:
                conn.execute(
                    "UPDATE research_hypotheses SET strategy_params=? "
                    "WHERE hypothesis_id=?", (raw, hid))
            conn.close()
            out = translate_hypothesis(db, hypothesis_id=hid)
            assert out["risk_genes"] == "platform_default", raw
            disc = out["risk_genes_disclosure"]
            assert disc["default_filled"] is None, \
                f"坏块 {raw!r} 须 null（无从判定不虚标全集）"
            assert disc["values"], "values 披露仍完整"

    def test_active_diversion_disclosure_reflects_existing_row(self, db):
        """spec Scenario「分流命中披露为既有产物事实」（tester 缺口
        补齐）：翻译命中库内同基因型既有 active 策略（same_dna_existing
        =true）→ 披露 values==被命中 active 行 dna_json 回读；声明面差
        集按当前假设声明块（存量无块假设 → default_filled 含 stop_loss）
        ——披露的是落库事实，标注反映本次声明面。"""
        import sqlite3
        hid_a = _hyp(db, strategy_params={"stop_loss": 0.05})
        out_a = translate_hypothesis(db, hypothesis_id=hid_a)
        sid = out_a["strategy_id"]
        # arrange：draft 产物行升 active（分流分支选择面——非断言对象）
        conn = sqlite3.connect(db)
        with conn:
            conn.execute(
                "UPDATE strategy SET status='active' WHERE strategy_id=?",
                (sid,))
        conn.close()

        rr.save_hypothesis_with_log(
            db, hypothesis_id="hyp_b_active",
            conditions=json.dumps(_CONDS), conditions_hash="ch_b_act",
            statement="s", data_ranges=json.dumps(_RANGES),
            ranges_hash="rh_b_act")
        out_b = translate_hypothesis(db, hypothesis_id="hyp_b_active")
        assert out_b["same_dna_existing"] is True
        assert out_b["strategy_id"] == sid
        disc = out_b["risk_genes_disclosure"]
        assert disc["values"] == self._row_risk_genes(db, sid), \
            "values==被命中 active 行落库回读（非当前构造对象）"
        assert disc["values"]["stop_loss"] == 0.05
        assert "stop_loss" in disc["default_filled"], \
            "存量无块假设 → 默认填充标注按本次声明面"
        assert out_b["risk_genes"] == "platform_default"


class TestSeriesDisclosureEvolution:
    """C4-E1 跨 change 锚演进（dynamic-stop-loss-series 4.5）：非
    series DNA 键集基线逐键不变；series 档 16+1 条件键。"""

    def test_non_series_baseline_unchanged(self, db):
        """非 series 声明（pct 档）→ disclosure 键集无 sl_series_source
        （7 恒写键——16 键基线的最小在场子集）。"""
        hid = _hyp(db, strategy_params={"stop_loss": 0.08})
        out = translate_hypothesis(db, hypothesis_id=hid)
        disc = out["risk_genes_disclosure"]
        assert "sl_series_source" not in disc["values"]
        assert "sl_series_source" not in disc["default_filled"]

    def test_series_17_keys_declared(self, db):
        """series 档声明→values 含 sl_series_source（16+1 条件键）+
        原样回显（A5 残留恒态注记在 test_sl_series_declaration 承载）。"""
        hid = _hyp(db, strategy_params={
            "sl_mode": "series",
            "sl_series_source": {"indicator": "RSI",
                                 "params": {"period": 14}}})
        out = translate_hypothesis(db, hypothesis_id=hid)
        disc = out["risk_genes_disclosure"]
        assert disc["values"]["sl_series_source"] == {
            "indicator": "RSI", "params": {"period": 14}}
        # sl_series_source 登记必携→恒不入 default_filled 差集
        assert "sl_series_source" not in disc["default_filled"]
