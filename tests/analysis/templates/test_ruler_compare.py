"""对照模板 ruler_compare 测试（add-evaluation-ruler 任务 5.1-5.5）。

分层：compute 核心纯函数单测（人工手算算例）→ 数据来源与区间校验
（ORM 直插 ValidationRecord/VrTradeDetail 夹具）→ D4/D3 demo 算例
复算（±1pp 门限；夹具固化时逐数登记）→ 模板壳集成（import 复用/
结构锚）。D4 对账期望值为 r9_d4_final.py 实跑登记值（2026-11-09）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError
from core.analysis.templates.ruler_compare import (
    compare_box, compare_wave, load_validation_trades, ruler_compare,
    validate_trade_source)

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


def _mk_wave(start, end, direction, p0, p1):
    return {"start": _ts(start), "end": _ts(end),
            "start_price": p0, "end_price": p1, "direction": direction,
            "amp": abs(p1 / p0 - 1), "dur": 10}


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


# ------------------------------------------------- 5.1 数据来源与区间校验

class TestTradeSource:
    def test_backtest_kind_rejected_with_guidance(self):
        with pytest.raises(ResearchError) as ei:
            validate_trade_source(
                {"kind": "backtest", "record_id": "rec_x"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "沙盒回测不落库逐笔（防污染纪律）" in ei.value.message
        assert "隔离验证记录 record_id" in ei.value.message
        assert ei.value.detail["guidance"], "指引文案随 detail 出站"

    def test_backtest_rejection_precedes_record_resolution(self, vr_db):
        """校验顺序断言（5.4）：backtest 案不做 record_id 存在性解析
        ——传不存在的 record_id，错误仍是 kind 拒绝而非记录不存在。"""
        with pytest.raises(ResearchError) as ei:
            load_validation_trades(
                vr_db, validate_trade_source(
                    {"kind": "backtest", "record_id": "rec_never"}),
                RNG)
        assert "不落库逐笔" in ei.value.message
        assert "不存在" not in ei.value.message

    @pytest.mark.parametrize("source", [
        {"kind": "sandbox"},
        {"kind": "paper"},
        "validation",
        {"record_id": "rec_x"},
    ])
    def test_unknown_kind_or_malformed_rejected(self, source):
        with pytest.raises(ResearchError) as ei:
            validate_trade_source(source)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_record_not_found_rejected_with_legal_form(self, vr_db):
        with pytest.raises(ResearchError) as ei:
            load_validation_trades(vr_db, "rec_missing", RNG)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["legal_form"], "附合法形制"

    def test_symbol_timeframe_mismatch_rejected(self, vr_db):
        rng = dict(RNG, symbol="ETHUSDT")
        _insert_record(vr_db, "rec_ok", RNG, [
            _mk_trade("2026-07-02", "2026-07-03", "long", 100, 105, 500)])
        with pytest.raises(ResearchError, match="symbol"):
            load_validation_trades(vr_db, "rec_ok", rng)

    def test_record_level_range_mismatch_rejected(self, vr_db):
        rng = dict(RNG, start="2026-07-15")
        _insert_record(vr_db, "rec_ok", RNG, [
            _mk_trade("2026-07-02", "2026-07-03", "long", 100, 105, 500)])
        with pytest.raises(ResearchError) as ei:
            load_validation_trades(vr_db, "rec_ok", rng)
        d = ei.value.detail
        assert d["record_normalized"] and d["request_normalized"], \
            "不等时附两侧归一值"

    def test_mixed_string_timestamp_normalized_equal_passes(self, vr_db):
        """字符串/时间戳混合输入归一相等正例：记录 range 带 Z 后缀、
        评估区间 naive 串——归一后相等不拒。"""
        rng_naive = {"symbol": "BTCUSDT", "timeframe": "4h",
                     "start": "2026-07-01", "end": "2026-11-07"}
        rng_z = {"symbol": "BTCUSDT", "timeframe": "4h",
                 "start": "2026-07-01T00:00:00Z",
                 "end": "2026-11-07T00:00:00+00:00"}
        rows = [_mk_trade("2026-07-02T04:00:00Z", "2026-07-03T04:00:00Z",
                          "long", 100, 105, 500)]
        _insert_record(vr_db, "rec_mix", rng_z, rows)
        trades, init_cash, open_n = load_validation_trades(
            vr_db, "rec_mix", rng_naive)
        assert len(trades) == 1 and init_cash == 100000.0 and open_n == 0

    def test_missing_request_range_rejected(self, vr_db):
        from sqlalchemy import text
        from core.persistence.orm.engine import engine_for
        _insert_record(vr_db, "rec_norange", RNG, [])
        with engine_for(vr_db).begin() as conn:
            conn.execute(text(
                "UPDATE validation_records SET params = '{}' "
                "WHERE record_id = 'rec_norange'"))
        with pytest.raises(ResearchError, match="request_range"):
            load_validation_trades(vr_db, "rec_norange", RNG)

    def test_trade_outside_range_rejected_with_seq(self, vr_db):
        rows = [_mk_trade("2026-06-15", "2026-07-03", "long", 100, 105, 5)]
        _insert_record(vr_db, "rec_out", RNG, rows)
        with pytest.raises(ResearchError) as ei:
            load_validation_trades(vr_db, "rec_out", RNG)
        assert ei.value.detail["seq"] == 0
        assert ei.value.detail["violating"] == "entry_time"
        assert ei.value.detail["time"]

    def test_open_trades_excluded_and_disclosed(self, vr_db):
        """持仓中行（exit_time=None）排除＋excluded_open_trades 计数
        披露（合成夹具含 1 笔持仓中）。"""
        rows = [
            _mk_trade("2026-07-02", "2026-07-03", "long", 100, 105, 500),
            _mk_trade("2026-07-05", None, "long", 100, 101, None, seq=1),
        ]
        _insert_record(vr_db, "rec_open", RNG, rows)
        trades, _, open_n = load_validation_trades(vr_db, "rec_open", RNG)
        assert len(trades) == 1, "持仓中行排除于全部指标"
        assert open_n == 1

    def test_exit_time_outside_range_rejected(self, vr_db):
        """逐笔级双键校验：entry 合法而 exit_time 越界（评估区间
        [2026-07-01, 2026-11-07) 外）→ 422 附 seq+violating=exit_time
        （spec 区间不一致 scenario 的逐笔级双键完备）。"""
        rows = [_mk_trade("2026-07-02", "2026-12-01", "long", 100, 105, 5)]
        _insert_record(vr_db, "rec_exit_out", RNG, rows)
        with pytest.raises(ResearchError) as ei:
            load_validation_trades(vr_db, "rec_exit_out", RNG)
        assert ei.value.detail["seq"] == 0
        assert ei.value.detail["violating"] == "exit_time"

    def test_empty_trade_rows_rejected(self, vr_db):
        """n 三档的 0 档现状记录：记录无逐笔行 → load 层 422（拒绝
        先于 build_result 的 min_samples 路径——防御纵深两道均
        fail-loud，此处锚现状行为面）。"""
        _insert_record(vr_db, "rec_empty", RNG, [])
        with pytest.raises(ResearchError, match="无逐笔行"):
            load_validation_trades(vr_db, "rec_empty", RNG)


# ------------------------------------------------- 5.2 浪模式计算核心

class TestWaveCompareCore:
    """手算算例（人工独立推导）。"""

    WAVES = [
        _mk_wave("2026-07-01", "2026-07-11", "long", 100.0, 120.0),
        _mk_wave("2026-07-11", "2026-07-21", "short", 120.0, 105.0),
    ]

    def test_closed_interval_both_edges_match(self):
        """归属=入场落 [浪起,浪止] 闭区间且方向一致——闭端两侧各 1：
        恰=浪起、恰=浪止（本例恰=浪止=后浪起，方向=后浪 short——共享
        pivot 上前浪方向不符跳过、后浪命中）。"""
        t0 = _mk_trade("2026-07-01T00:00:00Z", "2026-07-02", "long",
                       100, 105, 5)
        t1 = _mk_trade("2026-07-11T00:00:00Z", "2026-07-12", "short",
                       120, 110, 5)
        out = compare_wave([t0, t1], self.WAVES)
        assert out["unmatched_trades"] == 0

    def test_direction_mismatch_unmatched(self):
        t = _mk_trade("2026-07-05", "2026-07-06", "short", 100, 95, 5)
        out = compare_wave([t], self.WAVES)
        assert out["unmatched_trades"] == 1
        assert out["unmatched_direction_trades"] == 1

    def test_identify_window_boundary_inclusive(self):
        """识别窗恰=identify_window×浪长确认/恰超不确认（含等号）。
        浪 [07-01, 07-11] 长 10 天，0.3 窗=3 天：入场 07-04 恰=确认，
        07-04 00:00:01 超 1 秒=不确认。"""
        in_win = _mk_trade("2026-07-04", "2026-07-09", "long", 105, 118, 5)
        out = compare_wave([in_win], self.WAVES)
        assert out["identified_waves"] == 1
        out_win = _mk_trade("2026-07-04 00:00:01", "2026-07-09", "long",
                            105, 118, 5)
        out2 = compare_wave([out_win], self.WAVES)
        assert out2["identified_waves"] == 0

    def test_two_trades_same_wave_counted_once(self):
        t1 = _mk_trade("2026-07-02", "2026-07-05", "long", 102, 110, 5)
        t2 = _mk_trade("2026-07-03", "2026-07-06", "long", 103, 112, 5)
        out = compare_wave([t1, t2], self.WAVES)
        assert out["identified_waves"] == 1, "同浪识别率分子只计 1 浪"
        assert out["identification_rate"] == pytest.approx(1 / 2)

    def test_overlapping_same_direction_waves_first_match(self):
        """多浪重叠取第一匹配（spec 浪模式条款）：两条同向浪时间重叠
        [07-05, 07-11]，入场落在重叠段 → 归时间序第一条（wave_index=0）；
        入场落在仅第二条的尾段 → 第二条。重叠浪对为手工构造（compare_wave
        纯函数吃清单——不要求浪来自 zigzag 可达序列）。"""
        waves = [
            _mk_wave("2026-07-01", "2026-07-11", "long", 100.0, 120.0),
            _mk_wave("2026-07-05", "2026-07-15", "long", 110.0, 130.0),
        ]
        in_overlap = _mk_trade("2026-07-06", "2026-07-09", "long",
                               112, 118, 5)
        out = compare_wave([in_overlap], waves)
        assert out["per_trade"][0]["matched"] is True
        assert out["per_trade"][0]["wave_index"] == 0, \
            "重叠段归时间序第一条浪"
        in_second_only = _mk_trade("2026-07-13", "2026-07-14", "long",
                                   128, 129, 5)
        out2 = compare_wave([in_second_only], waves)
        assert out2["per_trade"][0]["wave_index"] == 1

    def test_capture_directionalized_both_sides(self):
        """捕获=方向化价差÷|end−start|：多 (exit−entry)/span、
        空 (entry−exit)/span（多空各 1 手算）。"""
        t_long = _mk_trade("2026-07-02", "2026-07-09", "long", 105, 118, 5)
        t_short = _mk_trade("2026-07-12", "2026-07-18", "short", 118, 108, 5)
        out = compare_wave([t_long, t_short], self.WAVES)
        # 多：span=20，(118−105)/20=0.65；空：span=15，(118−108)/15≈0.667
        assert out["capture_q25"] is not None
        # 逐笔核验走 identified 集合的中位
        assert out["capture_median"] == pytest.approx(
            (0.65 + (118 - 108) / 15) / 2, rel=1e-9)

    def test_zero_waves_null_with_disclosure(self):
        """n_waves=0 → 识别率/捕获率 null＋「区间无浪」披露（交易侧
        统计照出不炸）。"""
        t = _mk_trade("2026-07-02", "2026-07-05", "long", 105, 118, 5)
        out = compare_wave([t], [])
        assert out["identification_rate"] is None
        assert out["capture_median"] is None
        assert "区间无浪" in out["disclosure"]
        assert out["n_trades"] == 1, "交易侧统计照出"
        assert out["unmatched_trades"] == 1


# ------------------------------------------------- 5.3 箱模式计算核心

def _mk_box_df(closes, start="2026-07-01", freq="1h"):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC",
                        name="timestamp")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                         "volume": 1.0})


class TestBoxCompareCore:
    """手算算例（edge_window=1 通道=前一根收盘，无分位插值——手算
    可精确推导；box_h=0 时 sl_line=该通道值）。"""

    def _trade(self, df, e_i, x_i, direction="long", pnl=0.0):
        return _mk_trade(str(df.index[e_i]), str(df.index[x_i]),
                         direction, 100.0, 100.0, pnl)

    def test_entry_bar_minus_one_freeze_vs_entry_bar(self):
        """e−1/e 取根差异专门样例：close=[100,200,150…]——入场 bar2
        时 e−1（bar1）通道=close[0]=100（box_h=0→sl=100）；错取 e
        （bar2 通道=close[1]=200→sl=200）。判定窗收盘 150：e−1 形制
        不触止损（150>100）、bar3 对侧沿（150>=close[2]=150 含等号）
        → 对侧类；e 形制 bar2 即触止损（150<=200）→ 止损类。断言
        平台归类=对侧即证 e−1 取根。"""
        df = _mk_box_df([100.0, 200.0] + [150.0] * 5)
        t = self._trade(df, 2, 6, "long")
        out = compare_box([t], df, edge_window=1)
        assert out["n_opposite"] == 1 and out["n_stop_line"] == 0

    def test_three_classes_each_covered(self):
        """三分类各覆盖（手算，edge_window=1）。
        - 对侧：入场后首根收盘≥前一根（入场当根即触）。
        - 止损：入场后首根收盘≤close[e−2]。
        - 其他：先跳涨（抬 sl 至低位 close[e−2]）后缓跌——判定窗内
          恒低于前一根（不对侧）且恒高于 sl（不止损）。"""
        # 对侧：入场 bar6（sl=e−1 通道=100），bar6 收盘 110≥q90=100
        df = _mk_box_df([100.0] * 6 + [110.0] * 4)
        out = compare_box([self._trade(df, 6, 9)], df, edge_window=1)
        assert out["n_opposite"] == 1
        # 止损：入场 bar6，bar6 收盘 95≤sl=100 且 95<q90=100 不对侧
        df = _mk_box_df([100.0] * 6 + [95.0] * 4)
        out = compare_box([self._trade(df, 6, 9)], df, edge_window=1)
        assert out["n_stop_line"] == 1
        # 其他：close[1]=90 抬 sl、bar2 跳涨 150 后缓跌不破 90
        df = _mk_box_df([100.0, 90.0, 150.0, 149.0, 148.5, 148.0, 147.5])
        out = compare_box([self._trade(df, 3, 6)], df, edge_window=1)
        assert out["n_other"] == 1, "至出场均未触=其他（计入分母）"

    def test_sequence_first_hit_classified(self):
        """序判定先触者归类：先跌破 sl 后才到对侧沿 → 止损类。
        close=[100,90,150,149,85,200]，入场 bar3（sl=close[1]=90）：
        bar4 收盘 85≤90 先触止损；bar5 的 200 在后不改变归类。"""
        df = _mk_box_df([100.0, 90.0, 150.0, 149.0, 85.0, 200.0])
        t = self._trade(df, 3, 5)
        out = compare_box([t], df, edge_window=1)
        assert out["n_stop_line"] == 1 and out["n_opposite"] == 0

    def test_same_bar_opposite_priority(self):
        """同 bar 双触时对侧优先（demo if/elif 顺序同构）。buffer=0
        构造：close=[100,40,70,70]，入场 bar2（e−1 通道=close[0]=100
        → sl=100）。bar2 收盘 70：≥q90（=close[1]=40）触对侧、
        ≤sl（=100）也触止损——对侧在先。"""
        df = _mk_box_df([100.0, 40.0, 70.0, 70.0])
        t = self._trade(df, 2, 3)
        out = compare_box([t], df, edge_window=1, buffer=0.0)
        assert out["n_opposite"] == 1 and out["n_stop_line"] == 0

    def test_sl_line_subtraction_form_assertion(self):
        """减法形式断言（乘法形式为错）：edge_window=2 振荡窗
        (90,110) 分位插值 q10=92/q90=108（box_h=16）——减法形式
        sl=92−0.25×16=88；乘法（错）92×0.75=69。入场后收盘 75：
        减法触止损（75≤88）；乘法不触且后续对侧沿（(75,75) 窗
        q90=75）当根含等号触对侧——分类翻转判别。"""
        head = [90.0, 110.0] * 4          # 振荡窗稳定 (90,110)
        df = _mk_box_df(head + [75.0] * 4)
        t = self._trade(df, 8, 11, "long")
        out = compare_box([t], df, edge_window=2)
        assert out["n_stop_line"] == 1, "sl_line=q10−buffer×box_h（减法）"

    def test_short_sl_line_addition_form_mirror(self):
        """空头镜像加法形式（spec 箱模式条款：sl_line=q90+buffer×box_h
        ——加法/减法形制）：同 (90,110) 振荡窗 q90=108、box_h=16 →
        空头 sl=108+0.25×16=112；乘法（错）108×1.25=135。空头入场后
        收盘 115：加法触止损（115≥112）；乘法不触（115<135）且 115
        恒高于对侧沿 q10 → 归「其他」——分类翻转判别。"""
        head = [90.0, 110.0] * 4
        df = _mk_box_df(head + [115.0] * 4)
        t = self._trade(df, 8, 11, "short")
        out = compare_box([t], df, edge_window=2)
        assert out["n_stop_line"] == 1, "空头 sl=q90+buffer×box_h（加法）"
        assert out["n_other"] == 0

    def test_short_opposite_side_is_q10(self):
        """空头对侧沿=q10（镜像完备）：空头入场后收盘跌破 q10 →
        对侧类（e−1 通道=100、box_h=0 → sl=100 恰在场，85<100 不触
        空头止损——85≥sl 才触）。"""
        df = _mk_box_df([100.0] * 6 + [85.0] * 4)
        out = compare_box([self._trade(df, 6, 9, "short")], df,
                          edge_window=1)
        assert out["n_opposite"] == 1
        assert out["n_stop_line"] == 0

    def test_monthly_frequency_denominator(self):
        """月频分母=distinct(entry_time YYYY-MM) 下限 1。"""
        df = _mk_box_df([100.0] * 40, start="2026-07-20", freq="12h")
        t1 = _mk_trade("2026-07-21", "2026-07-22", "long", 100, 100, 0)
        t2 = _mk_trade("2026-08-01", "2026-08-02", "long", 100, 100, 0)
        out = compare_box([t1, t2], df, edge_window=1)
        assert out["trades_months_distinct"] == 2
        assert out["monthly_frequency"] == pytest.approx(1.0)


# ------------------------------------------------- demo 算例复算（5.2/5.3）

_D4_EXPECT = {  # r9_d4_final.py 实跑登记值（2026-11-09）
    "A0": {"n": 60, "ided": 7, "cap_med": 0.54, "ci": (-0.343, 0.785)},
    "B1": {"n": 31, "ided": 7, "cap_med": 0.35, "ci": (-0.81, 0.92)},
    "C": {"n": 34, "ided": 6, "cap_med": 0.37, "ci": (-0.69, 1.10)},
}
_NW_TOTAL = 22  # 锁箱浪池 BTC 5 + ETH 17


def _d4_sym_pools():
    """d4 各品种浪池（复刻 r9_d4_final.py 口径）。

    demo 差异面注记：d4 经 r9_d2_arms.zigzag 的 piv[:-1] 砍掉最后一
    个确认 pivot（该实现无 d1 的「append 末端候选再剔除」语句——
    剔除的是真确认 pivot），浪池比 d1 语义（平台实现）每品种少最后
    一条腿；匹配按品种分组（demo map_waves 逐 sym 调用——跨品种
    时间重叠不可互配）。返回 {sym: 全量腿（d4 口径）}。"""
    from core.analysis.templates.wave_inventory import compute_zigzag_waves
    pools = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        path = Path(f"data/market_fapi/{sym}_4h.parquet")
        if not path.exists():
            pytest.skip("真实数据不在库（data/market_fapi）")
        df = pd.read_parquet(path)
        df = df[df.index <= pd.Timestamp("2026-11-07", tz="UTC")]
        pools[sym] = compute_zigzag_waves(
            df["close"], threshold=0.05)[:-1]  # d4 口径：去末条腿
    return pools


def _d4_wave_pools():
    """d4 双浪池现算（复刻 r9_d4_final.py 实际口径）。

    demo 差异面注记：d4 经 r9_d2_arms.zigzag 的 piv[:-1] 砍掉最后一个
    确认 pivot（该实现无 d1 的「append 末端候选再剔除」语句——剔除的
    是真确认 pivot），浪池比 d1 语义（平台实现）每品种少最后一条腿。
    d4 双口径：全量池（csv 行 identified/capture 的标记基准——含
    匹配锁箱外浪的笔）与锁箱池（识别率分母 22）。
    """
    from core.analysis.templates.wave_inventory import compute_zigzag_waves
    all_pool, lock_pool = [], []
    for sym in ("BTCUSDT", "ETHUSDT"):
        path = Path(f"data/market_fapi/{sym}_4h.parquet")
        if not path.exists():
            pytest.skip("真实数据不在库（data/market_fapi）")
        df = pd.read_parquet(path)
        df = df[df.index <= pd.Timestamp("2026-11-07", tz="UTC")]
        sym_waves = compute_zigzag_waves(df["close"], threshold=0.05)
        all_pool += sym_waves[:-1]  # d4 口径：去最后一条腿
        lock_pool += [w for w in sym_waves[:-1]
                      if w["start"] >= pd.Timestamp("2026-07-01", tz="UTC")]
    return all_pool, lock_pool


def _d4_trades(arm):
    t = pd.read_csv(FIXTURE_DIR / "r9_d2_trades_lock.csv")
    sub = t[(t.seg == "lock") & (t.arm == arm)]
    return [_mk_trade(r.entry_t, r.exit_t,
                      "long" if r.dir == "多" else "short",
                      r.entry, r.out, r.net, seq=i)
            for i, r in enumerate(sub.itertuples())]


class TestD4WaveReconciliation:
    """D4 锁箱算例复算（任务 5.2 完成标准）：识别率/捕获中位/CI 与
    r9_d4_final.py 输出对账（±1pp 门限承载装载差异面；A0 臂 CI 为
    rng 首耗、逐位断言）。init_cash=100 使 net_i=csv net 值（同序列
    对齐 CI）。"""

    @pytest.mark.parametrize("arm", ["A0", "B1", "C"])
    def test_d4_arm_reconciliation(self, arm):
        """d4 口径复刻：按品种分组匹配（demo map_waves 逐 sym），
        识别率分子=合并去重「被识别的锁箱浪」（d4 idd 集合）、分母
        22；捕获中位=合并 identified 笔 capture 的中位（csv 行标记
        基准=全量腿含锁箱外匹配）。"""
        exp = _D4_EXPECT[arm]
        pools = _d4_sym_pools()
        lock_cut = pd.Timestamp("2026-07-01", tz="UTC")
        n_lock = sum(1 for ws in pools.values()
                     for w in ws if w["start"] >= lock_cut)
        assert n_lock == _NW_TOTAL, "锁箱浪池 22 条（BTC 5+ETH 17）"
        t = pd.read_csv(FIXTURE_DIR / "r9_d2_trades_lock.csv")
        sub = t[(t.seg == "lock") & (t.arm == arm)]
        ided_lock_waves = set()
        ided_caps = []
        for sym, sym_waves in pools.items():
            sym_rows = sub[sub.sym == sym]
            trades = [_mk_trade(r.entry_t, r.exit_t,
                                "long" if r.dir == "多" else "short",
                                r.entry, r.out, r.net, seq=i)
                      for i, r in enumerate(sym_rows.itertuples())]
            out = compare_wave(trades, sym_waves)
            for i, w in enumerate(sym_waves):
                hit = any(d["matched"] and d["wave_index"] == i
                          and d["identified"]
                          for d in out["per_trade"])
                if hit and w["start"] >= lock_cut:
                    ided_lock_waves.add((sym, i))
            ided_caps += [d["capture"] for d in out["per_trade"]
                          if d["matched"] and d["identified"]
                          and d["capture"] is not None]
        assert len(sub) == exp["n"]
        assert len(ided_lock_waves) == exp["ided"],             f"{arm} 识别浪数对账"
        assert len(ided_lock_waves) / _NW_TOTAL == pytest.approx(
            exp["ided"] / _NW_TOTAL, abs=0.01)
        cap_med = float(pd.Series(ided_caps).median())
        assert cap_med == pytest.approx(exp["cap_med"], abs=0.01),             f"{arm} 捕获中位对账"
        from core.analysis.templates.ruler_compare import _net_stats
        trades_all = [_mk_trade(r.entry_t, r.exit_t,
                                "long" if r.dir == "多" else "short",
                                r.entry, r.out, r.net, seq=i)
                      for i, r in enumerate(sub.itertuples())]
        net = _net_stats(trades_all, init_cash=100.0)
        lo, hi = net["net_ci_pct"]
        # CI 容差分档：A0 臂为 demo rng 首耗（与平台独立 seed 同构，
        # ±1pp 内通过）；B1/C 臂在 demo 为共享 rng 流的后续消耗、
        # 平台为独立 seed=42 首耗——n=31 样本的 bootstrap 端点蒙特
        # 卡洛标准误 ~0.02-0.03pp（非装载差异面，量级/符号/中心
        # 对齐即证算法同构——A0 逐位已证消耗序同构），容差单列
        ci_tol = 0.01 if arm == "A0" else 0.05
        assert lo == pytest.approx(exp["ci"][0], abs=ci_tol), f"{arm} CI lo"
        assert hi == pytest.approx(exp["ci"][1], abs=ci_tol), f"{arm} CI hi"


class TestD3BoxReconciliation:
    """D3 锁箱算例复算（任务 5.3 完成标准）：BTC 1h 对侧率 15%/
    15m 26%（±1pp 门限）。判定窗 [入场 bar, 出场 bar) 与 demo 引擎
    触发 bar 语义同构（次开出场的触发 bar=出场前一根）。"""

    @pytest.mark.parametrize(
        ("tf", "edge_window", "n", "opp_rate"),
        [("1h", 240, 20, 0.15), ("15m", 480, 66, 0.26)])
    def test_d3_opposite_rate(self, tf, edge_window, n, opp_rate):
        path = Path(f"data/market_fapi/BTCUSDT_{tf}.parquet")
        if not path.exists():
            pytest.skip(f"真实数据 BTCUSDT {tf} 不在库")
        df = pd.read_parquet(path)
        df = df[df.index <= pd.Timestamp("2026-11-09", tz="UTC")]
        t = pd.read_csv(FIXTURE_DIR / "r9_d3_trades_lock.csv")
        sub = t[t.tf == tf]
        assert len(sub) == n
        trades = [_mk_trade(r.entry_t, r.exit_t,
                            "long" if r.dir == "多" else "short",
                            r.entry, r.exit, r.net, seq=i)
                  for i, r in enumerate(sub.itertuples())]
        out = compare_box(trades, df, edge_window=edge_window)
        assert out["n_trades"] == n
        assert out["opposite_rate"] == pytest.approx(opp_rate, abs=0.01), \
            f"{tf} 对侧率对账（±1pp）"


# ------------------------------------------------- 5.4/5.5 模板壳与结构锚

class TestRulerCompareShell:
    """模板壳集成：import 复用结构锚、来源分级负向、出站形制。"""

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

    def _seed_record(self, vr_db, rng):
        df_rows = [
            _mk_trade("2026-02-05", "2026-02-12", "long", 62, 80, 3.0),
            _mk_trade("2026-02-20", "2026-02-25", "long", 70, 95, 2.0),
            _mk_trade("2026-03-01", "2026-03-05", "short", 100, 90, 1.5),
        ]
        _insert_record(vr_db, "rec_cmp", rng, df_rows, init_cash=100.0)

    def test_wave_shell_full_run_and_import_reuse(
            self, vr_db, env, monkeypatch):
        """模板壳 wave 全链＋底账内部现算 import 复用断言（复用
        compute_zigzag_waves 而非复制算法）。"""
        import sys
        wi = sys.modules["core.analysis.templates.wave_inventory"]
        calls = []
        orig = wi.compute_zigzag_waves
        monkeypatch.setattr(
            wi, "compute_zigzag_waves",
            lambda *a, **k: calls.append(1) or orig(*a, **k))
        self._seed_record(vr_db, env["rng"])
        out = ruler_compare(
            vr_db, env["data_dir"], env["rng"], mode="wave",
            trade_source={"kind": "validation", "record_id": "rec_cmp"},
            data_ranges=[env["rng"]])
        assert calls, "底账现算经 compute_zigzag_waves 复用（import 关系）"
        assert out["template"] == "ruler_compare"
        assert out["payload"]["mode"] == "wave"
        assert out["payload"]["trade_source"] == {
            "kind": "validation", "record_id": "rec_cmp"}
        assert out["sample_size"] == 3

    def test_box_shell_full_run_and_import_reuse(
            self, vr_db, env, monkeypatch):
        import sys
        self._seed_record(vr_db, env["rng"])
        out = ruler_compare(
            vr_db, env["data_dir"], env["rng"], mode="box",
            trade_source={"kind": "validation", "record_id": "rec_cmp"},
            data_ranges=[env["rng"]], edge_window=24)
        assert out["payload"]["box"]["n_trades"] == 3

    def test_payload_has_no_source_grading_keys(self, vr_db, env):
        """trade_source 输出不含来源分级标注键（单一来源后已删——
        负向断言防实现残留；可追溯性由 record_id 回链承载）。"""
        self._seed_record(vr_db, env["rng"])
        for mode, extra in (("wave", {}), ("box", {"edge_window": 24})):
            out = ruler_compare(
                vr_db, env["data_dir"], env["rng"], mode=mode,
                trade_source={"kind": "validation",
                              "record_id": "rec_cmp"},
                data_ranges=[env["rng"]], **extra)
            keys = set(out["payload"]) | set(
                out["payload"].get("trade_source", {}))
            bad = {k for k in keys
                   if "tier" in k.lower() or "grade" in k.lower()}
            assert not bad, f"来源分级标注键残留: {bad}"

    def test_backtest_rejected_at_shell(self, vr_db, env):
        self._seed_record(vr_db, env["rng"])
        with pytest.raises(ResearchError) as ei:
            ruler_compare(
                vr_db, env["data_dir"], env["rng"], mode="wave",
                trade_source={"kind": "backtest", "record_id": "rec_cmp"},
                data_ranges=[env["rng"]])
        assert "不落库逐笔" in ei.value.message
        assert "隔离验证记录 record_id" in ei.value.message

    def test_small_n_ci_null_with_disclosure(self, vr_db, env):
        """n<3 三档：1-2 笔指标可算 CI=null 附样本数（附着层契约）。"""
        df_rows = [
            _mk_trade("2026-02-05", "2026-02-12", "long", 62, 80, 3.0),
            _mk_trade("2026-02-20", "2026-02-25", "long", 70, 95, 2.0),
        ]
        _insert_record(vr_db, "rec_two", env["rng"], df_rows,
                       init_cash=100.0)
        out = ruler_compare(
            vr_db, env["data_dir"], env["rng"], mode="wave",
            trade_source={"kind": "validation", "record_id": "rec_two"},
            data_ranges=[env["rng"]])
        p = out["payload"]
        assert p["net_mean_pct"] is not None, "指标可算"
        assert p["net_ci_pct"] is None, "n=2 时 CI=null"
        assert p["n_for_ci"] == 2, "样本数披露在场"

    def test_idempotent_bitwise(self, vr_db, env):
        self._seed_record(vr_db, env["rng"])
        a = ruler_compare(
            vr_db, env["data_dir"], env["rng"], mode="wave",
            trade_source={"kind": "validation", "record_id": "rec_cmp"},
            data_ranges=[env["rng"]])
        b = ruler_compare(
            vr_db, env["data_dir"], env["rng"], mode="wave",
            trade_source={"kind": "validation", "record_id": "rec_cmp"},
            data_ranges=[env["rng"]])
        assert a == b

    def test_net_mean_caliber_hand_computed(self, vr_db, env):
        """净均值口径手算锚（spec 浪模式条款）：net_i=逐笔 pnl÷init_cash
        ×100（百分比形，pnl 已含费滑不后扣）。三笔 pnl 3.0/2.0/1.5、
        init_cash=100 → mean=(3.0+2.0+1.5)/3≈2.1667、win_rate=1.0。"""
        self._seed_record(vr_db, env["rng"])
        out = ruler_compare(
            vr_db, env["data_dir"], env["rng"], mode="wave",
            trade_source={"kind": "validation", "record_id": "rec_cmp"},
            data_ranges=[env["rng"]])
        p = out["payload"]
        assert p["net_mean_pct"] == pytest.approx(6.5 / 3, rel=1e-12)
        assert p["win_rate"] == pytest.approx(1.0)
        assert p["n_for_ci"] == 3
        assert p["net_ci_pct"] is not None, "n=3 附着 CI"
        assert "init_cash" in p["net_caliber"], "口径声明含分母来源"

    def test_edge_window_frozen_default_1h(self, vr_db, tmp_path):
        """edge_window timeframe 冻结默认（tasks 5.3：1h=240/15m=480
        默认）：1h 轴不传 edge_window → 用 240 且全程 200 出结果。"""
        import numpy as np
        d = tmp_path / "data"
        d.mkdir()
        n = 600
        closes = np.linspace(100.0, 160.0, n)
        idx = pd.date_range("2025-12-22", periods=n, freq="1h", tz="UTC",
                            name="timestamp")
        pd.DataFrame({"open": closes, "high": closes + 0.3,
                      "low": closes - 0.3, "close": closes,
                      "volume": 1.0}, index=idx).to_parquet(
            d / "TESTUSDT_1h.parquet")
        rng = {"symbol": "TESTUSDT", "timeframe": "1h",
               "start": "2026-01-01", "end": "2026-01-10"}
        rows = [
            _mk_trade("2026-01-02 05:00:00+00:00",
                      "2026-01-03 05:00:00+00:00", "long", 101, 103, 2.0),
            _mk_trade("2026-01-04 05:00:00+00:00",
                      "2026-01-05 05:00:00+00:00", "long", 103, 105, 2.0),
            _mk_trade("2026-01-06 05:00:00+00:00",
                      "2026-01-07 05:00:00+00:00", "long", 105, 107, 2.0),
        ]
        _insert_record(vr_db, "rec_1h", rng, rows, init_cash=100.0)
        out = ruler_compare(
            vr_db, d, rng, mode="box",
            trade_source={"kind": "validation", "record_id": "rec_1h"},
            data_ranges=[rng])
        assert out["payload"]["box"]["edge_window"] == 240
        assert out["payload"]["box"]["n_trades"] == 3

    def test_edge_window_non_frozen_tf_rejected(self, vr_db, env):
        """非标轴（4h）缺 edge_window → 422 附「无冻结默认」与冻结表
        detail（frozen_defaults 含 1h=240/15m=480——15m 值经权威源
        detail 面承载）。"""
        self._seed_record(vr_db, env["rng"])
        with pytest.raises(ResearchError) as ei:
            ruler_compare(
                vr_db, env["data_dir"], env["rng"], mode="box",
                trade_source={"kind": "validation",
                              "record_id": "rec_cmp"},
                data_ranges=[env["rng"]])
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "无冻结默认" in ei.value.message
        assert ei.value.detail["param"] == "edge_window"
        assert ei.value.detail["frozen_defaults"] == {"1h": 240,
                                                      "15m": 480}

    def test_unknown_mode_rejected(self, vr_db, env):
        with pytest.raises(ResearchError) as ei:
            ruler_compare(
                vr_db, env["data_dir"], env["rng"], mode="both",
                trade_source={"kind": "validation", "record_id": "rec_cmp"},
                data_ranges=[env["rng"]])
        assert ei.value.detail["param"] == "mode"
        assert ei.value.detail["legal_range"] == ["wave", "box"]

    @pytest.mark.parametrize(
        "iw", [0, -0.3, 1.5, 2.0,
               pytest.param("0.3", id="字符串数字（CARD-04）"),
               pytest.param(True, id="bool（CARD-04）")])
    def test_identify_window_out_of_range_rejected(self, vr_db, env, iw):
        """identify_window 值域 (0,1]（CARD-04 迁 _require_float_range
        后：字符串数字/bool 按原始形一律 422——旧 float() 形制下
        "0.3" 静默放行、True 经 float 转 1.0 落域内放行）。"""
        self._seed_record(vr_db, env["rng"])
        with pytest.raises(ResearchError) as ei:
            ruler_compare(
                vr_db, env["data_dir"], env["rng"], mode="wave",
                trade_source={"kind": "validation",
                              "record_id": "rec_cmp"},
                data_ranges=[env["rng"]], identify_window=iw)
        assert ei.value.detail["param"] == "identify_window"
        assert ei.value.detail["legal_range"] == "(0,1]"

    @pytest.mark.parametrize(
        ("kw", "param"),
        [pytest.param({"buffer": True}, "buffer",
                      id="bool buffer（CARD-04）"),
         pytest.param({"buffer": "0.25"}, "buffer",
                      id="字符串数字 buffer（CARD-04）"),
         pytest.param({"edge_q_low": "0.1"}, "edge_q_low",
                      id="字符串数字 edge_q_low（CARD-04）"),
         pytest.param({"edge_window": 24.5}, "edge_window",
                      id="浮点 edge_window（CARD-04）")])
    def test_numeric_params_raw_form_rejected(self, vr_db, env, kw, param):
        """壳层数值参数原始形校验（CARD-04 迁 guard 单源）：bool/
        字符串数字/浮点 int 形一律 422（旧形制 buffer=True 走
        isinstance(int,float) 放行、edge_window=24.5 被 int 洗）。"""
        self._seed_record(vr_db, env["rng"])
        call_kw = {"edge_window": 30}  # 4h 轴无冻结默认，垫底合法值
        call_kw.update(kw)
        with pytest.raises(ResearchError) as ei:
            ruler_compare(
                vr_db, env["data_dir"], env["rng"], mode="box",
                trade_source={"kind": "validation",
                              "record_id": "rec_cmp"},
                data_ranges=[env["rng"]], **call_kw)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == param

    @pytest.mark.parametrize(
        ("b_or_s", "bad"),
        [pytest.param({"B": 3000.5}, 3000.5, id="非整 B"),
         pytest.param({"B": True}, True, id="bool B"),
         pytest.param({"B": 0}, 0, id="零 B"),
         pytest.param({"B": 100_001}, 100_001, id="超上限 B"),
         pytest.param({"seed": "x"}, "x", id="非整 seed"),
         pytest.param({"seed": True}, True, id="bool seed")],
    )
    def test_b_seed_precheck_rejected(self, vr_db, env, b_or_s, bad):
        """B/seed 前置 422（CARD-01）：detail 裸键 param="B"/"seed"
        （MCP 场景表面）——非整/bool/超上限在壳层拦截，不裸穿
        boot_ci/np 层。"""
        self._seed_record(vr_db, env["rng"])
        with pytest.raises(ResearchError) as ei:
            ruler_compare(
                vr_db, env["data_dir"], env["rng"], mode="wave",
                trade_source={"kind": "validation",
                              "record_id": "rec_cmp"},
                data_ranges=[env["rng"]], **b_or_s)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] in ("B", "seed")
        assert ei.value.detail.get("actual", bad) == bad

    def test_b_upper_bound_inclusive_full_run(self, vr_db, env):
        """B=BOOT_B_MAX（100_000）含等号合法：全链可跑（上限边界
        两侧——拒绝档在参数化「超上限」）。"""
        from core.scoring.statistics import BOOT_B_MAX
        self._seed_record(vr_db, env["rng"])
        out = ruler_compare(
            vr_db, env["data_dir"], env["rng"], mode="wave",
            trade_source={"kind": "validation", "record_id": "rec_cmp"},
            data_ranges=[env["rng"]], B=BOOT_B_MAX)
        assert out["payload"]["n_for_ci"] == 3
        assert out["confidence"]["B"] == BOOT_B_MAX
