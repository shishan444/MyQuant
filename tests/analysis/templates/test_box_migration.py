"""箱体能力单源迁移锚（add-box-capability 任务 3.1/3.2/3.3）。

迁移契约（D3 装载层消费）的行为/结构/文本三层锚：
- 结构锚：消费边界（box_inventory 段提取只消费 box_state 禁用
  box_established；compare_box 消费列集合=upper/lower/break 族不含
  channel_width）；
- 文本锚：守门三句在位（compare_box 零独立公式——无 quantile 字样；
  模板核心无独立计算段）；
- 行为锚：M1 序列级等价（装载路输出 vs 迁移前模板核心公式独立重演
  逐位一致）、M4 timeframe 感知承载（装载调用参数选择）、demo 锚
  一体验收（浪锚零变化＋对照三分类逐位一致 all 夹具 379 笔）。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.templates.box_inventory import (
    box_inventory, compute_box_segments)
from core.analysis.templates.ruler_compare import compare_box
from core.data.fapi.rest import default_data_dir

MARKET = default_data_dir()
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures/evaluation_ruler"


def _mk_trade(entry, exit_, direction, seq=0):
    def _ts(v):
        t = pd.Timestamp(v)
        return t.tz_localize("UTC") if t.tzinfo is None \
            else t.tz_convert("UTC")
    return {"seq": (seq, 0), "entry_time": _ts(entry),
            "exit_time": _ts(exit_) if exit_ else None,
            "entry_price": 100.0, "exit_price": 100.0, "size": 1.0,
            "direction": direction, "pnl": 0.0}


def _mk_spread_df(spreads, close=100.0):
    idx = pd.date_range("2024-01-01", periods=len(spreads), freq="1h",
                        tz="UTC", name="timestamp")
    s = pd.Series(spreads, index=idx, dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + s, "low": close - s,
        "close": float(close), "volume": 1.0,
    }, index=idx)


# ----------------------------------------------------------- 3.1 结构与文本锚

class TestBoxInventoryMigrationAnchors:
    """box_inventory 装载层消费的结构/文本/行为锚。"""

    def test_consumes_only_box_state_not_established(self):
        """消费边界：段提取消费 box_state、禁用 box_established
        （established 段边界后移 min_len−1 根与 demo 箱锚不符——源码
        文本锚，docstring 披露义务同款形制）。"""
        src = inspect.getsource(compute_box_segments)
        assert "box_state" in src
        assert '"box_established"' not in src, \
            "段提取禁用 box_established（消费边界——spec 盘点条款）"

    def test_no_independent_width_formula(self):
        """零独立计算段：模板核心不再自算 hi/lo/mid/width（公式单源在
        BoxRegime 指标列——代码体无 .rolling( 调用，docstring 描述
        文本不在此锚范围）。"""
        src = inspect.getsource(compute_box_segments)
        assert ".rolling(" not in src, \
            "段提取核心不得残留独立 rolling 公式"

    def test_m1_serial_equivalence_vs_pre_migration_formula(self):
        """M1 序列级锚：装载路输出与迁移前模板核心公式（独立重演）逐位
        一致——随机数据上段清单（start/end/dur/height/truncated）全等。"""
        import numpy as np
        rng = np.random.default_rng(11)
        n = 200
        close = 100 + rng.normal(0, 0.4, n).cumsum()
        spread = rng.uniform(1.5, 4.5, n)
        df = pd.DataFrame({
            "open": close, "high": close + spread, "low": close - spread,
            "close": close, "volume": 1.0,
        }, index=pd.date_range("2024-01-01", periods=n, freq="1h",
                               tz="UTC", name="timestamp"))
        w, ml, thr = 20, 10, 0.08
        got = compute_box_segments(df, request_start=None, window=w,
                                   min_len=ml, width_thr=thr)
        # 迁移前模板核心独立重演（box_inventory 迁移前五行公式）
        hi = df["high"].rolling(w).max()
        lo = df["low"].rolling(w).min()
        mid = df["close"].rolling(w).mean()
        width = (hi - lo) / mid
        state = (width <= thr).fillna(False)
        sv, idx = state.values, state.index
        expect = []
        i, m = 0, len(sv)
        while i < m:
            if sv[i]:
                j = i
                while j < m and sv[j]:
                    j += 1
                if j - i >= ml:
                    expect.append({"start": idx[i], "end": idx[j - 1],
                                   "dur": j - i,
                                   "box_height": float(
                                       width.iloc[i:j].mean()),
                                   "truncated_start": False})
                i = j
            else:
                i += 1
        assert got == expect, "装载路段清单与迁移前公式逐位一致（M1）"

    def test_m4_timeframe_aware_load_params(self, monkeypatch):
        """M4 timeframe 感知承载：装载调用处参数选择 1h→120、15m→240
        （捕获 _compute_indicator 调用参数——冻结默认解析链保持）。"""
        from core.features import indicators as ind_mod

        captured = {}
        real = ind_mod._compute_indicator

        def spy(df, name, params):
            captured[name] = dict(params)
            return real(df, name, params)

        # compute_box_segments 内局部 import 每次调用从源模块取属性
        monkeypatch.setattr(ind_mod, "_compute_indicator", spy)
        for tf, w, ml in (("1h", 120, 48), ("15m", 240, 96)):
            df = _mk_spread_df([4.0] * (w + ml + 3))
            box_inventory(df, data_ranges=[{"symbol": "T",
                                            "timeframe": tf,
                                            "start": "2024-01-01",
                                            "end": "2024-03-01"}],
                          timeframe=tf, request_start=None)
            assert captured.get("BoxRegime", {}).get("window") == w, tf


# ----------------------------------------------------------- 3.2 结构与文本锚

class TestRulerCompareMigrationAnchors:
    """compare_box 装载层消费的守门三句与消费列集合锚。"""

    def test_consumes_upper_lower_break_not_channel_width(self):
        """对照消费列集合结构断言：=upper/lower/break 族、不含
        channel_width（资格宽不进对照三分类——spec 边界条款）。"""
        src = inspect.getsource(compare_box)
        for f in ("upper", "lower", "break_low", "break_high"):
            assert f'"{f}"' in src, f"消费列缺 {f}"
        assert "channel_width" not in src, \
            "对照三分类不消费 channel_width（消费列集合边界）"

    def test_gatekeeper_three_clauses_in_place(self):
        """守门三句在位（文本锚）：①公式单源——零独立 quantile 公式；
        ②取值 e−1 根列值（break 列 e_i−1 取根）；③冻结语义——判定窗
        内 sl_line 常数（循环体内无 break 列滚动取值）。"""
        src = inspect.getsource(compare_box)
        assert "quantile" not in src, \
            "compare_box 零独立分位公式（守门句①——公式单源在指标列）"
        assert "e_i - 1" in src and "break" in src, \
            "取值 e−1 根 break 列值（守门句②）"
        # 守门句③：判定窗循环体内只比较 sl_line 常数（冻结），无逐 bar
        # 消费 break 滚动列值——循环体内不含 bl_s/bh_s 取值
        loop_body = src.split("for j in range(e_i, x_i):")[1].split(
            "if reason")[0]
        assert "bl_s" not in loop_body and "bh_s" not in loop_body, \
            "判定窗内禁逐 bar 消费 break 滚动列值（守门句③——冻结语义）"

    def test_sl_line_from_break_columns_semantics(self):
        """守门三句行为锚（迁移后语义不变）：e−1 冻结、减法/加法镜像、
        既有手算族全绿由 test_ruler_compare 承载——此处补 break 列值
        直证（W=1：break_low[e−1]=close[e−2]，与原式 q10−buffer×box_h
        在 box_h=0 时等值）。"""
        idx = pd.date_range("2024-01-01", periods=6, freq="1h",
                            tz="UTC", name="timestamp")
        df = pd.DataFrame({
            "open": [100.0] * 6, "high": [100.0] * 6, "low": [100.0] * 6,
            "close": [100.0, 200.0, 150.0, 150.0, 150.0, 150.0],
            "volume": 1.0,
        }, index=idx)
        from core.features.indicators import _compute_indicator
        out = _compute_indicator(
            df, "BoxChannel",
            {"edge_window": 1, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25})
        bl = out["boxch_1_0.1_0.9_0.25_break_low"]
        bh = out["boxch_1_0.1_0.9_0.25_break_high"]
        # W=1 box_h=0：break_low[1]=break_high[1]=close[0]=100
        # （原式 q10−0.25×0=q10 同值）
        assert bl.iloc[1] == pytest.approx(100.0)
        assert bh.iloc[1] == pytest.approx(100.0)


# ----------------------------------------------------------- 3.3 demo 锚一体验收

class TestWaveAnchorZeroChange:
    """浪锚零变化（迁移回归硬门②）：浪模式未迁移，4h 全历史浪底账
    与 demo r9_d1_out.txt 冻结数字逐数一致（BTC 79=40+39 /
    ETH 159=82+77；右端 2026-11-07）。"""

    @pytest.mark.parametrize(
        ("sym", "total", "n_long"),
        [("BTCUSDT", 79, 40), ("ETHUSDT", 159, 82)])
    def test_wave_counts_frozen(self, sym, total, n_long):
        from core.analysis.templates.wave_inventory import (
            compute_zigzag_waves)
        path = MARKET / f"{sym}_4h.parquet"
        if not path.exists():
            pytest.skip(f"真实数据 {sym} 4h 不在库（data/market_fapi）")
        df = pd.read_parquet(path)
        df = df[df.index <= pd.Timestamp("2026-11-07", tz="UTC")]
        waves = compute_zigzag_waves(df["close"], threshold=0.05)
        nl = sum(1 for w in waves if w["direction"] == "long")
        assert len(waves) == total
        assert nl == n_long
        assert total - nl == total - n_long  # 空腿=总数−多腿


class TestD3AllFixtureThreeWayBitwise:
    """对照三分类逐位一致（迁移回归硬门③）：r9_d3_trades_all.csv
    379 笔（两品种 calib+lock 全量，sym×tf 四组）逐笔三分类与 demo
    引擎 reason 映射一致（对侧→opposite、破箱→stop_line、时间/
    段末→other——判定窗与 demo 引擎同构，见 TestD3BoxReconciliation
    注记）。冻结消费语义三句为前置检验位：三句任缺则本锚必红
    （对侧率/分类翻转）。"""

    REASON_MAP = {"对侧": "opposite", "破箱": "stop_line",
                  "时间": "other", "段末": "other"}

    @pytest.mark.parametrize(
        ("sym", "tf", "edge_window"),
        [("BTCUSDT", "1h", 240), ("BTCUSDT", "15m", 480),
         ("ETHUSDT", "1h", 240), ("ETHUSDT", "15m", 480)])
    def test_three_way_classification_bitwise(self, sym, tf, edge_window):
        path = MARKET / f"{sym}_{tf}.parquet"
        if not path.exists() or not (
                FIXTURE_DIR / "r9_d3_trades_all.csv").exists():
            pytest.skip("真实数据或夹具不在库")
        df = pd.read_parquet(path)
        df = df[df.index <= pd.Timestamp("2026-11-09", tz="UTC")]
        t = pd.read_csv(FIXTURE_DIR / "r9_d3_trades_all.csv")
        sub = t[(t.sym == sym) & (t.tf == tf)
                & (t.seg.isin(("calib", "lock")))]
        n_ok = 0
        for r in sub.itertuples():
            trade = _mk_trade(r.entry_t, r.exit_t,
                              "long" if r.dir == "多" else "short")
            out = compare_box([trade], df, edge_window=edge_window)
            got = ("opposite" if out["n_opposite"] == 1
                   else "stop_line" if out["n_stop_line"] == 1 else "other")
            expect = self.REASON_MAP[r.reason]
            assert got == expect, (
                f"{tf} seq 行 {r.entry_t} {r.dir} {r.reason}: "
                f"got {got}")
            n_ok += 1
        assert n_ok == len(sub) and n_ok > 0

    def test_all_fixture_row_count(self):
        """夹具完整性：all 夹具 379 行（calib 293＋lock 86）与
        demo r9_d3 生成口径一致。"""
        if not (FIXTURE_DIR / "r9_d3_trades_all.csv").exists():
            pytest.skip("夹具不在库")
        t = pd.read_csv(FIXTURE_DIR / "r9_d3_trades_all.csv")
        assert len(t) == 379
        assert (t.seg == "calib").sum() == 293
        assert (t.seg == "lock").sum() == 86


class TestWarmupBudgetEquivalence:
    """M5 warmup 预算等价性质断言：derive_warmup 两件（720/1440）≥
    模板原前扩需求（window 上限 240 / edge_window 上限 480）。"""

    def test_budget_covers_template_warmup(self):
        from core.analysis.registry_meta import derive_warmup
        from core.features.registry import INDICATOR_REGISTRY
        reg_max = INDICATOR_REGISTRY["BoxRegime"].params["window"].max
        ch_max = INDICATOR_REGISTRY["BoxChannel"].params["edge_window"].max
        assert derive_warmup(["BoxRegime"]) == 720 >= reg_max
        assert derive_warmup(["BoxChannel"]) == 1440 >= ch_max
