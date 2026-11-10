"""A/B 对照模板 ab_compare 测试（sandbox-and-report-supplement 任务
B2/B3）。

分层：校验链同源消费（同坏输入同 422——与 ruler_compare 逐字段
对齐）→ 模板壳集成（双记录对照算例/区间不等拒绝/kind 拦截两侧/
幂等/dimension 三值 import 复用/组间差 n 三档）→ D2 demo 算例复算
（A0 vs B1 臂对 boot_diff CI 与 r9_d4_final.py 输出对账 ±1pp——
期望值为 r9_d4_final.py 实跑登记值 2026-11-09）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError
from core.analysis.templates.ab_compare import ab_compare, diff_stats
from core.analysis.templates.ruler_compare import (
    load_validation_trades, ruler_compare)

FIXTURE_DIR = (Path(__file__).resolve().parents[3]
               / "tests/fixtures/evaluation_ruler")
RNG = {"symbol": "BTCUSDT", "timeframe": "4h",
       "start": "2026-07-01", "end": "2026-11-07"}


# --------------------------------------------------------------- 夹具与助手

def _ts(v):
    t = pd.Timestamp(v)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _mk_trade(entry, exit_, direction, entry_px, exit_px, pnl,
              seq=0):
    return {"seq": (seq, 0),
            "entry_time": _ts(entry),
            "exit_time": _ts(exit_) if exit_ else None,
            "entry_price": entry_px, "exit_price": exit_px,
            "size": 1.0, "direction": direction, "pnl": pnl}


@pytest.fixture()
def vr_db(tmp_path):
    from core.analysis.executor import ensure_research_tables
    from core.persistence.db_ext import init_db_ext

    d = tmp_path / "t.db"
    init_db_ext(d)
    ensure_research_tables(d)
    from core.persistence.orm.engine import engine_for
    from sqlalchemy import text
    with engine_for(d).begin() as conn:
        conn.execute(text(
            "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
            " conditions_hash, statement, data_ranges, ranges_hash,"
            " status, created_at, updated_at) VALUES"
            " ('hyp_r', '[]', 'ch_r', 's', '[]', 'rh_r', 'active',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"))
    return d


def _insert_record(db, record_id, rng_dict, trade_rows, init_cash=100000.0):
    from sqlalchemy import text
    from core.persistence.orm.engine import engine_for

    params = json.dumps({
        "request_range": rng_dict, "range_hash": "x",
        "threshold_profile": "default",
        "init_cash": init_cash, "fee": 0.001, "slippage": 0.0005})
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, params, result, verdict,"
            " range_hash, created_at) VALUES (:rid, 'hyp_r', NULL,"
            " 'isolated_backtest', '', :params, '{}', 'pass', 'x',"
            " '2026-01-01T00:00:00Z')"),
            {"rid": record_id, "params": params})
        for i, t in enumerate(trade_rows):
            conn.execute(text(
                "INSERT INTO vr_trade_detail (record_id, exec_ref,"
                " exec_index, range_start, range_end, seq, entry_time,"
                " exit_time, entry_price, exit_price, size, direction,"
                " pnl) VALUES (:rid, 'main', 0, :rs, :re, :seq, :et,"
                " :xt, :ep, :xp, 1.0, :dir, :pnl)"),
                {"rid": record_id, "rs": rng_dict["start"],
                 "re": rng_dict["end"], "seq": i,
                 "et": str(t["entry_time"]),
                 "xt": (str(t["exit_time"])
                        if t["exit_time"] is not None else None),
                 "ep": t["entry_price"], "xp": t["exit_price"],
                 "dir": t["direction"].capitalize(), "pnl": t["pnl"]})


ROWS_A = [
    _mk_trade("2026-07-02", "2026-07-03", "long", 100, 105, 3.0),
    _mk_trade("2026-07-10", "2026-07-11", "long", 100, 102, 2.0),
    _mk_trade("2026-08-01", "2026-08-02", "short", 100, 98, 1.5),
    _mk_trade("2026-09-01", "2026-09-02", "long", 100, 99, -1.0),
]
ROWS_B = [
    _mk_trade("2026-07-05", "2026-07-06", "long", 100, 101, 1.0),
    _mk_trade("2026-07-20", "2026-07-21", "long", 100, 98, -2.0),
    _mk_trade("2026-08-15", "2026-08-16", "short", 100, 99, 0.5),
]


def _seed_two(vr_db, rng=None):
    rng = rng or RNG
    _insert_record(vr_db, "rec_a", rng, ROWS_A, init_cash=100.0)
    _insert_record(vr_db, "rec_b", rng, ROWS_B, init_cash=100.0)


def _call(vr_db, tmp_path, rng=None, *, dimension=None, **over):
    kw = dict(
        mode="ab",
        trade_source_a={"kind": "validation", "record_id": "rec_a"},
        trade_source_b={"kind": "validation", "record_id": "rec_b"},
        data_ranges=[rng or RNG],
    )
    if dimension is not None:
        kw["dimension"] = dimension
    kw.update(over)
    return ab_compare(vr_db, tmp_path, rng or RNG, **kw)


# ------------------------------------------------- B2 校验链同源消费

class TestSharedValidationChain:
    """同坏输入同 422 行为断言（公共校验件同源消费的机械证明）：
    对同一坏输入，ab_compare 与 ruler_compare 抛 ResearchError 的
    code/message 逐字段一致。"""

    def _assert_same_rejection(self, vr_db, tmp_path, bad_rows,
                               record_id="rec_bad"):
        """两侧引用同一坏记录（b 侧坏输入与 a 侧同源——同记录同错误，
        排除 b 侧存在性先拦的干扰）。"""
        _insert_record(vr_db, record_id, RNG, bad_rows)
        with pytest.raises(ResearchError) as ruler_ei:
            ruler_compare(
                vr_db, tmp_path, RNG, mode="wave",
                trade_source={"kind": "validation", "record_id": record_id},
                data_ranges=[RNG])
        with pytest.raises(ResearchError) as ab_ei:
            ab_compare(
                vr_db, tmp_path, RNG, mode="ab",
                trade_source_a={"kind": "validation",
                                "record_id": record_id},
                trade_source_b={"kind": "validation",
                                "record_id": record_id},
                data_ranges=[RNG])
        assert ab_ei.value.code == ruler_ei.value.code
        assert ab_ei.value.message == ruler_ei.value.message
        assert ab_ei.value.detail == ruler_ei.value.detail

    def test_record_not_found_same_422(self, vr_db, tmp_path):
        _insert_record(vr_db, "rec_b", RNG, ROWS_B)
        with pytest.raises(ResearchError) as ruler_ei:
            load_validation_trades(vr_db, "rec_missing", RNG)
        with pytest.raises(ResearchError) as ab_ei:
            ab_compare(
                vr_db, tmp_path, RNG, mode="ab",
                trade_source_a={"kind": "validation",
                                "record_id": "rec_missing"},
                trade_source_b={"kind": "validation",
                                "record_id": "rec_b"},
                data_ranges=[RNG])
        assert ab_ei.value.code == ruler_ei.value.code
        assert ab_ei.value.message == ruler_ei.value.message
        assert ab_ei.value.detail == ruler_ei.value.detail

    def test_range_mismatch_vs_eval_same_422(self, vr_db, tmp_path):
        """记录区间≠评估区间（记录级区间一致性公共件）——同 422。"""
        self._assert_same_rejection(
            vr_db, tmp_path,
            [_mk_trade("2026-06-15", "2026-07-03", "long", 100, 105, 5)])

    def test_all_open_trades_same_422(self, vr_db, tmp_path):
        """全持仓中行（持仓中排除公共件）——同 422。"""
        self._assert_same_rejection(
            vr_db, tmp_path,
            [_mk_trade("2026-07-05", None, "long", 100, 101, None)])

    def test_backtest_kind_a_rejected_with_guidance(self, vr_db, tmp_path):
        _seed_two(vr_db)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path,
                  trade_source_a={"kind": "backtest", "record_id": "rec_a"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "沙盒回测不落库逐笔（防污染纪律）" in ei.value.message
        assert "隔离验证记录 record_id" in ei.value.message
        assert ei.value.detail["guidance"], "指引文案随 detail 出站"

    def test_backtest_kind_b_rejected_with_guidance(self, vr_db, tmp_path):
        """kind 拦截两侧各一（trade_source_b 侧）。"""
        _seed_two(vr_db)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path,
                  trade_source_b={"kind": "backtest", "record_id": "rec_b"})
        assert "不落库逐笔" in ei.value.message
        assert ei.value.detail["param"] == "trade_source.kind"

    def test_unknown_kind_rejected(self, vr_db, tmp_path):
        _seed_two(vr_db)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path,
                  trade_source_a={"kind": "sandbox", "record_id": "rec_a"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_backtest_rejection_precedes_record_resolution(
            self, vr_db, tmp_path):
        """校验顺序（kind 拦截在前——纪律拦截优先于引用解析）：传不存在
        record_id 的 backtest source，错误仍是 kind 拒绝。"""
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path,
                  trade_source_a={"kind": "backtest",
                                  "record_id": "rec_never"})
        assert "不落库逐笔" in ei.value.message
        assert "不存在" not in ei.value.message

    @pytest.mark.parametrize(
        "bad",
        [pytest.param({"identify_window": 1.5}, id="identify_window 越上界"),
         pytest.param({"identify_window": True}, id="bool identify_window"),
         pytest.param({"buffer": "0.25"}, id="字符串数字 buffer"),
         pytest.param({"edge_q_low": 0.5, "edge_q_high": 0.3},
                      id="edge_q 联合反转"),
         pytest.param({"B": 3000.5}, id="非整 B"),
         pytest.param({"B": True}, id="bool B"),
         pytest.param({"seed": "x"}, id="非整 seed"),
         pytest.param({"seed": True}, id="bool seed")])
    def test_numeric_param_rejection_parity_with_ruler(
            self, vr_db, tmp_path, bad):
        """参数面平价锚（对质①提取的护栏——同坏参数 ab_compare 与
        ruler_compare 同 code/message/detail）：六参数域校验单源在
        validate_compare_numeric_params，本断言机械证明两壳同源消费
        ——未来改 BOOT_B_MAX 语义或 bool 排除规则只动公共件一处，
        漂移即红。参数校验先于 trade_source 解析（无需库记录）。"""
        with pytest.raises(ResearchError) as ruler_ei:
            ruler_compare(
                vr_db, tmp_path, RNG, mode="wave",
                trade_source={"kind": "validation", "record_id": "rec_x"},
                data_ranges=[RNG], **bad)
        with pytest.raises(ResearchError) as ab_ei:
            ab_compare(
                vr_db, tmp_path, RNG, mode="ab",
                trade_source_a={"kind": "validation",
                                "record_id": "rec_a"},
                trade_source_b={"kind": "validation", "record_id": "rec_b"},
                data_ranges=[RNG], **bad)
        assert ab_ei.value.code == ruler_ei.value.code
        assert ab_ei.value.message == ruler_ei.value.message
        assert ab_ei.value.detail == ruler_ei.value.detail


# ------------------------------------------------- B2 模板壳集成

class TestAbCompareShell:
    def test_two_record_full_run_hand_computed(self, vr_db, tmp_path):
        """双记录对照算例（手算锚）：nets_a=[3,2,1.5,-1] mean=1.375、
        win=3/4；nets_b=[1,-2,0.5] mean=-1/6、win=2/3（1 与 0.5 为正）；
        diff_mean=1.375+1/6=1.5417；月频 a=4 笔跨 3 月=4/3、b=3 笔跨
        2 月=1.5。"""
        _seed_two(vr_db)
        out = _call(vr_db, tmp_path)
        assert out["template"] == "ab_compare"
        assert out["sample_size"] == 7  # 4+3 两组观测合计
        p = out["payload"]
        assert p["mode"] == "ab"
        assert p["dimension"] == "none"
        assert p["trade_source_a"] == {"kind": "validation",
                                       "record_id": "rec_a"}
        assert p["trade_source_b"] == {"kind": "validation",
                                       "record_id": "rec_b"}
        assert p["group_a"]["net_mean_pct"] == pytest.approx(1.375,
                                                             rel=1e-12)
        assert p["group_a"]["win_rate"] == pytest.approx(0.75)
        assert p["group_a"]["monthly_frequency"] == pytest.approx(4 / 3)
        assert p["group_a"]["trades_months_distinct"] == 3
        assert p["group_b"]["net_mean_pct"] == pytest.approx(
            -1 / 6, rel=1e-12)
        assert p["group_b"]["win_rate"] == pytest.approx(2 / 3)
        assert p["group_b"]["monthly_frequency"] == pytest.approx(1.5)
        d = p["group_diff"]
        assert d["diff_mean_pct"] == pytest.approx(1.375 + 1 / 6, rel=1e-12)
        assert d["diff_ci_pct"] is not None, "两侧各 n≥3 附着 CI"
        assert d["direction"] == "a_minus_b"
        assert d["n_a_for_ci"] == 4 and d["n_b_for_ci"] == 3
        assert out["confidence"]["interval"] == d["diff_ci_pct"]
        assert out["confidence"]["method"] == "bootstrap_percentile"
        # dimension=none 不挂结构指标（负向：无 wave/box 键）
        assert "wave" not in p["group_a"] and "box" not in p["group_a"]

    def test_range_mismatch_between_records_rejected_with_sides(
            self, vr_db, tmp_path):
        """区间不等拒绝（spec scenario）：rec_a 区间=评估区间、rec_b
        区间不同（start 偏移）→ 422 附两侧归一值（b vs 评估基准——
        记录级区间一致性公共件承载，双记录相等由此保证）。"""
        _insert_record(vr_db, "rec_a", RNG, ROWS_A, init_cash=100.0)
        rng_b = dict(RNG, start="2026-07-15")
        _insert_record(vr_db, "rec_b", rng_b, ROWS_B, init_cash=100.0)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        d = ei.value.detail
        assert d["record_normalized"] and d["request_normalized"], \
            "不等时附两侧归一值"

    def test_symbol_mismatch_between_records_rejected(self, vr_db,
                                                      tmp_path):
        """双记录 symbol 不同（rec_b 为 ETHUSDT 记录）→ 422（对照前提
        不满足——记录级一致性公共件拦截）。"""
        _insert_record(vr_db, "rec_a", RNG, ROWS_A, init_cash=100.0)
        rng_b = dict(RNG, symbol="ETHUSDT")
        _insert_record(vr_db, "rec_b", rng_b, ROWS_B, init_cash=100.0)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path)
        assert "symbol" in ei.value.message

    def test_idempotent_bitwise(self, vr_db, tmp_path):
        _seed_two(vr_db)
        a = _call(vr_db, tmp_path)
        b = _call(vr_db, tmp_path)
        assert a == b

    def test_unknown_mode_rejected(self, vr_db, tmp_path):
        _seed_two(vr_db)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path, mode="wave")
        assert ei.value.detail["param"] == "mode"
        assert ei.value.detail["legal_range"] == ["ab"]

    def test_unknown_dimension_rejected(self, vr_db, tmp_path):
        _seed_two(vr_db)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path, dimension="both")
        assert ei.value.detail["param"] == "dimension"
        assert ei.value.detail["legal_range"] == ["none", "wave", "box"]

    @pytest.mark.parametrize(
        ("kw", "param"),
        [pytest.param({"B": 3000.5}, "B", id="非整 B"),
         pytest.param({"seed": "x"}, "seed", id="非整 seed")])
    def test_b_seed_precheck_rejected(self, vr_db, tmp_path, kw, param):
        """B/seed 前置 422（ruler_compare 同款契约——不裸穿
        boot_diff/np 层）。"""
        _seed_two(vr_db)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, tmp_path, **kw)
        assert ei.value.detail["param"] == param

    def test_diff_ci_null_when_one_side_below_three(
            self, vr_db, tmp_path):
        """组间差 n 三档：b 侧仅 2 笔（<3）→ diff CI=null 附两侧样本数
        （指标可算——附着层契约）。"""
        _insert_record(vr_db, "rec_a", RNG, ROWS_A, init_cash=100.0)
        _insert_record(vr_db, "rec_b", RNG, ROWS_B[:2], init_cash=100.0)
        out = _call(vr_db, tmp_path)
        d = out["payload"]["group_diff"]
        assert d["diff_mean_pct"] is not None, "均值差可算"
        assert d["diff_ci_pct"] is None, "任一侧 n<3 时 CI=null"
        assert d["n_a_for_ci"] == 4 and d["n_b_for_ci"] == 2
        assert out["confidence"]["method"] == "descriptive_only"
        assert out["payload"]["group_b"]["net_ci_pct"] is None, \
            "组内 CI 同款三档（b 组 n=2）"

    def test_same_record_both_sides_diff_converges(self, vr_db, tmp_path):
        """a/b 引用同一条记录：diff_mean=0、重抽样分布对称（两组同
        分布——CI 关于 0 对称且含 0；boot_diff 两侧独立重抽样，同值
        样本不退化为零宽区间——常数收敛契约仅适用单样本 boot_ci）。"""
        _insert_record(vr_db, "rec_a", RNG, ROWS_A, init_cash=100.0)
        out = _call(vr_db, tmp_path,
                    trade_source_b={"kind": "validation",
                                    "record_id": "rec_a"})
        d = out["payload"]["group_diff"]
        assert d["diff_mean_pct"] == pytest.approx(0.0, abs=1e-12)
        lo, hi = d["diff_ci_pct"]
        assert lo == pytest.approx(-hi, abs=1e-9), "同分布差值区间对称"
        assert lo <= 0.0 <= hi


class TestDimensionMount:
    """dimension 三值（wave/box 挂载指标 import 复用断言＋底账现算
    一次两组共享）。"""

    @pytest.fixture()
    def env(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        import numpy as np
        closes = np.concatenate([
            np.linspace(100, 60.2, 200), [63.5],
            np.linspace(63.9, 110, 199), [104.0],
            np.linspace(104, 80, 200)])
        idx = pd.date_range("2026-01-01", periods=len(closes), freq="4h",
                            tz="UTC", name="timestamp")
        pd.DataFrame({"open": closes, "high": closes + 0.3,
                      "low": closes - 0.3, "close": closes,
                      "volume": 1.0}, index=idx).to_parquet(
            d / "TESTUSDT_4h.parquet")
        rng = {"symbol": "TESTUSDT", "timeframe": "4h",
               "start": "2026-01-01", "end": "2026-04-20"}
        return {"data_dir": d, "rng": rng}

    def _rows(self):
        return [
            _mk_trade("2026-02-05", "2026-02-12", "long", 62, 80, 3.0),
            _mk_trade("2026-02-20", "2026-02-25", "long", 70, 95, 2.0),
            _mk_trade("2026-03-01", "2026-03-05", "short", 100, 90, 1.5),
        ]

    def test_wave_dimension_mounts_and_reuses_compute(
            self, vr_db, env, monkeypatch):
        """wave 挂载：两组各含识别率/捕获率结构指标；浪池底账现算一次
        两组共享（compute_zigzag_waves 计数=1——import 复用＋共享断言）。"""
        import sys
        rng = env["rng"]
        _insert_record(vr_db, "rec_a", rng, self._rows(), init_cash=100.0)
        rows_b = [r | {"pnl": r["pnl"] * 0.5} for r in self._rows()]
        _insert_record(vr_db, "rec_b", rng, rows_b, init_cash=100.0)
        wi = sys.modules["core.analysis.templates.wave_inventory"]
        calls = []
        orig = wi.compute_zigzag_waves
        monkeypatch.setattr(
            wi, "compute_zigzag_waves",
            lambda *a, **k: calls.append(1) or orig(*a, **k))
        out = _call(vr_db, env["data_dir"], rng, dimension="wave")
        assert len(calls) == 1, "浪池现算一次、两组共享（import 复用）"
        p = out["payload"]
        assert p["dimension"] == "wave"
        for g in ("group_a", "group_b"):
            assert "identification_rate" in p[g]["wave"]
            assert "capture_median" in p[g]["wave"]
        assert p["threshold"] == 0.05

    def test_box_dimension_mounts_and_shares_channel(
            self, vr_db, env, monkeypatch):
        """box 挂载：两组各含先到对侧比例/三分类；箱通道 df 装载一次
        两组共享（load_and_prepare_df 计数=1）。"""
        import core.data.mtf_loader as mtf
        rng = env["rng"]
        _insert_record(vr_db, "rec_a", rng, self._rows(), init_cash=100.0)
        rows_b = [r | {"pnl": r["pnl"] * 0.5} for r in self._rows()]
        _insert_record(vr_db, "rec_b", rng, rows_b, init_cash=100.0)
        calls = []
        orig = mtf.load_and_prepare_df
        monkeypatch.setattr(
            mtf, "load_and_prepare_df",
            lambda *a, **k: calls.append(1) or orig(*a, **k))
        out = _call(vr_db, env["data_dir"], rng, dimension="box",
                    edge_window=24)
        assert len(calls) == 1, "行情装载一次、两组共享同一通道底账"
        p = out["payload"]
        for g in ("group_a", "group_b"):
            assert "opposite_rate" in p[g]["box"]
            assert {"n_opposite", "n_stop_line", "n_other"} <= set(
                p[g]["box"])

    def test_box_non_frozen_tf_rejected(self, vr_db, env):
        """4h 轴缺 edge_window → 422 附「无冻结默认」（ruler 同款）。"""
        rng = env["rng"]
        _insert_record(vr_db, "rec_a", rng, self._rows(), init_cash=100.0)
        _insert_record(vr_db, "rec_b", rng, self._rows(), init_cash=100.0)
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, env["data_dir"], rng, dimension="box")
        assert "无冻结默认" in ei.value.message
        assert ei.value.detail["frozen_defaults"] == {"1h": 240,
                                                      "15m": 480}

    def test_insufficient_data_rejected(self, vr_db, env):
        """评估区间完全在数据外（wave 分支）→ RESEARCH_DATA_
        INSUFFICIENT（ruler 同款拒绝面）。"""
        gone = dict(env["rng"], start="2025-01-01", end="2025-02-01")
        _insert_record(vr_db, "rec_a", gone, [
            _mk_trade("2025-01-05", "2025-01-10", "long", 100, 105, 5)])
        _insert_record(vr_db, "rec_b", gone, [
            _mk_trade("2025-01-06", "2025-01-11", "long", 100, 103, 2)])
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, env["data_dir"], gone, dimension="wave")
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    def test_box_insufficient_data_rejected(self, vr_db, env):
        """box 分支数据不足（阶段四闸门消解：box 装载 df None 拒绝
        面 :236-237 与 wave 分支各自独立 raise）——同构造换
        dimension=box＋显式 edge_window（4h 无冻结默认不抢跑）。"""
        gone = dict(env["rng"], start="2025-01-01", end="2025-02-01")
        _insert_record(vr_db, "rec_a", gone, [
            _mk_trade("2025-01-05", "2025-01-10", "long", 100, 105, 5)])
        _insert_record(vr_db, "rec_b", gone, [
            _mk_trade("2025-01-06", "2025-01-11", "long", 100, 103, 2)])
        with pytest.raises(ResearchError) as ei:
            _call(vr_db, env["data_dir"], gone, dimension="box",
                  edge_window=24)
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    def test_box_frozen_default_1h_hit(self, vr_db, tmp_path):
        """1h 冻结默认命中（阶段四闸门消解：edge_window 缺省自动取
        1h=240 的分支——4h 无默认拒绝已测，命中侧补全；1h 轴数据
        前扩覆盖 240 根装载前提）。"""
        import numpy as np
        d = tmp_path / "data1h"
        d.mkdir()
        n = 1200
        closes = np.concatenate([
            np.linspace(100, 60.2, 400), [63.5],
            np.linspace(63.9, 110, 399), [104.0],
            np.linspace(104, 80, 400)])
        idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h",
                            tz="UTC", name="timestamp")
        pd.DataFrame({"open": closes, "high": closes + 0.3,
                      "low": closes - 0.3, "close": closes,
                      "volume": 1.0}, index=idx).to_parquet(
            d / "TESTUSDT_1h.parquet")
        rng = {"symbol": "TESTUSDT", "timeframe": "1h",
               "start": "2026-02-01", "end": "2026-03-01"}
        _insert_record(vr_db, "rec_a", rng, [
            _mk_trade("2026-02-05", "2026-02-12", "long", 62, 80, 3.0),
            _mk_trade("2026-02-20", "2026-02-25", "long", 70, 95, 2.0),
            _mk_trade("2026-02-26", "2026-02-27", "short", 100, 90, 1.5)])
        _insert_record(vr_db, "rec_b", rng, [
            _mk_trade("2026-02-05", "2026-02-12", "long", 62, 80, 1.5),
            _mk_trade("2026-02-20", "2026-02-25", "long", 70, 95, 1.0),
            _mk_trade("2026-02-26", "2026-02-27", "short", 100, 90, 0.7)])
        out = _call(vr_db, d, rng, dimension="box")  # edge_window 缺省
        p = out["payload"]
        assert p["dimension"] == "box"
        for g in ("group_a", "group_b"):
            assert "opposite_rate" in p[g]["box"], \
                "1h 冻结默认命中不拒（240 自动取）"


# ------------------------------------------------- B3 demo 语义锚

class TestD2AbReconciliation:
    """D2 四臂复算之 A0 vs B1 臂对（任务 B3）：固化夹具（lock 段
    双臂，源 works/research/consensus-ema/r9_d2_trades_all.csv）
    双臂合成两组交易清单，组间均值差 boot_diff CI 与 r9_d4_final.py
    输出对账。

    demo 引擎与平台装载差异面（docstring 注记同 ruler，承载于
    ab_compare 模块 docstring）：demo 的组间差为模块级共享 rng 流
    后续消耗（先耗五臂 boot_ci 再 boot_diff）、平台为独立 seed=42
    首耗——bootstrap 端点蒙特卡洛差（实测 lo −0.050pp/hi +0.060pp）
    由 ±1pp 门限承载；组间差为纯统计件路径（无行情装载），无 zigzag
    起点效应差异面。
    """

    # r9_d4_final.py 实跑登记值（2026-11-09）：
    # 每笔净差 B1−A0: [-1.193, +0.780]；A0 60 笔均值 +0.24% 胜率 60%；
    # B1 31 笔均值 +0.00% 胜率 32%
    _DEMO = {"n_a": 60, "n_b": 31, "mean_a": 0.24, "mean_b": 0.00,
             "win_a": 0.60, "win_b": 0.32,
             "b1_minus_a0": (-1.193, 0.780)}

    def _arm_trades(self, arm):
        t = pd.read_csv(FIXTURE_DIR / "r9_d2_trades_ab_lock.csv")
        sub = t[t.arm == arm]
        return [_mk_trade(r.entry_t, r.exit_t,
                          "long" if r.dir == "多" else "short",
                          r.entry, r.out, r.net, seq=i)
                for i, r in enumerate(sub.itertuples())]

    def test_a0_vs_b1_boot_diff_reconciliation(self):
        """组间均值差 CI 对账：平台 a−b（A0−B1）与 demo B1−A0 镜像
        对齐（±1pp）；两组均值/胜率/样本数与 demo 登记值对齐。"""
        trades_a = self._arm_trades("A0")
        trades_b = self._arm_trades("B1")
        assert len(trades_a) == self._DEMO["n_a"]
        assert len(trades_b) == self._DEMO["n_b"]
        # init_cash=100 使 net_i=csv net 值（百分比形同序列对齐）
        d = diff_stats(trades_a, 100.0, trades_b, 100.0)
        assert d["n_a_for_ci"] == self._DEMO["n_a"]
        assert d["n_b_for_ci"] == self._DEMO["n_b"]
        assert d["diff_mean_pct"] == pytest.approx(
            self._DEMO["mean_a"] - self._DEMO["mean_b"], abs=0.005)
        lo, hi = d["diff_ci_pct"]
        # demo 方向 B1−A0 → 平台 a−b 期望=镜像区间（逐端点取负）。
        # 容差 0.15pp＝实测差异面（lo −0.050/hi +0.060pp——demo 共享
        # rng 流后续消耗 vs 平台独立 seed 首耗的蒙特卡洛端点差）留
        # 2.5 倍余量；任务门限 ±1pp 为上限，此处紧化保留回归敏感度
        demo_lo, demo_hi = self._DEMO["b1_minus_a0"]
        assert lo == pytest.approx(-demo_hi, abs=0.15), \
            "平台 CI 下界 vs demo 镜像上界（±1pp 门限紧化档）"
        assert hi == pytest.approx(-demo_lo, abs=0.15), \
            "平台 CI 上界 vs demo 镜像下界（±1pp 门限紧化档）"

    def test_group_stats_reconciliation(self):
        """两组各自指标对账（净均值/胜率——r9_d4_final.py 锁箱臂行
        登记值，打印两位小数 → ±0.005/±0.01 容差）。"""
        from core.analysis.templates.ab_compare import _group_stats
        a = _group_stats(self._arm_trades("A0"), 100.0)
        b = _group_stats(self._arm_trades("B1"), 100.0)
        assert a["net_mean_pct"] == pytest.approx(self._DEMO["mean_a"],
                                                  abs=0.005)
        assert b["net_mean_pct"] == pytest.approx(self._DEMO["mean_b"],
                                                  abs=0.005)
        assert a["win_rate"] == pytest.approx(self._DEMO["win_a"],
                                              abs=0.01)
        assert b["win_rate"] == pytest.approx(self._DEMO["win_b"],
                                              abs=0.01)
        assert a["n_for_ci"] == self._DEMO["n_a"]
        assert b["n_for_ci"] == self._DEMO["n_b"]

    def test_demo_source_csv_reconciles_with_fixture(self):
        """夹具固化校验：源 csv（works/research/consensus-ema）在库时，
        固化夹具与源双臂 lock 段逐行 net 序列一致（防夹具漂移）。"""
        src = (Path(__file__).resolve().parents[3]
               / "works/research/consensus-ema/r9_d2_trades_all.csv")
        if not src.exists():
            pytest.skip("demo 源 csv 不在库")
        t = pd.read_csv(src)
        sub = t[(t.seg == "lock") & (t.arm.isin(["A0", "B1"]))]
        fix = pd.read_csv(FIXTURE_DIR / "r9_d2_trades_ab_lock.csv")
        for arm in ("A0", "B1"):
            assert list(sub[sub.arm == arm]["net"]) == \
                list(fix[fix.arm == arm]["net"]), \
                f"{arm} 臂 net 序列与源 csv 漂移（夹具须重新固化）"
