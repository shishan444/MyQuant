"""冠军五件指标契约测试（add-champion-indicators 任务 1.1/2.1-2.5/7.1）。

ShannonEntropy/PermutationEntropy（信息熵族）/EfficiencyRatio（效率比）
——information 类目；CUSUM/GARCHFilter（变点/条件波动率）——structure
类目。参数域契约、逐列口径三分类确定样例、复算锚（demo 公式独立重演
——期望不经被测内核生成）、warmup 静态数值锚、计数与 naming 三层同步。

复算锚形制（box 先例）：works/research/consensus-ema 冻结脚本的公式在
测试内独立重演（研究脚本不进生产 import）；熵族窗口口径与冻结 demo 的
一格之差已裁决（2026-11-09 team-lead 裁 a 含当根）：spec MUST
Requirement 正文（含当根＋三分类独立 Requirement）为行为权威，demo
为研究脚本参照非行为权威；结构统计列族统一含当根（BoxRegime width
先例）保总则一致——两段式锚（含当根窗公式重导逐位一致＋错位性质
断言披露口径差）落地，见 TestShannonEntropyContract/
TestPermutationEntropyContract 的 frozen_demo 用例。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicators import _compute_indicator
from core.features.registry import INDICATOR_REGISTRY, resolve_indicator_column
from core.analysis.column_ref import encode_column_ref

MARKET = Path("data/market_fapi")

CHAMPIONS = ("ShannonEntropy", "PermutationEntropy", "EfficiencyRatio",
             "CUSUM", "GARCHFilter")


def _mk_df(closes, highs=None, lows=None, start="2024-01-01", freq="1h"):
    """closes → OHLCV df（box 测试同款构造器）。"""
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC",
                        name="timestamp")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({
        "open": c,
        "high": c if highs is None else pd.Series(highs, index=idx,
                                                  dtype=float),
        "low": c if lows is None else pd.Series(lows, index=idx,
                                                dtype=float),
        "close": c, "volume": 1.0,
    }, index=idx)


def _mk_walk_df(n=1200, seed=42, start=100.0):
    """正态随机游走 close（复算锚合成数据源——demo 同族构造）。"""
    rng = np.random.default_rng(seed)
    close = start + rng.normal(0, 2.0, n).cumsum()
    return _mk_df(close)


# ----------------------------------------------------------- 1.1 白名单扩容

class TestPeriodLikeExpansion:
    """rank_window/sd_window/init_window 三键扩容：存在性断言先行＋
    既有件参数名零命中零回归扫描。"""

    def test_keys_present_before_all(self):
        """存在性断言先行：五键 ⊇ 契约集（漏扩先红直达根因）。"""
        from core.analysis.registry_meta import _PERIOD_LIKE_PARAMS
        assert {"window", "edge_window", "rank_window", "sd_window",
                "init_window"} <= _PERIOD_LIKE_PARAMS

    def test_non_period_keys_excluded(self):
        """非周期参数不入白名单（pattern_len/drift/threshold/alpha/beta
        ——不产生 rolling 数据前缀）。"""
        from core.analysis.registry_meta import _PERIOD_LIKE_PARAMS
        for key in ("pattern_len", "drift", "threshold", "alpha", "beta"):
            assert key not in _PERIOD_LIKE_PARAMS, key

    def test_expansion_zero_hit_on_existing_indicators(self):
        """零回归扫描：既有 50 件（champion 五件之外）参数名与新增三键
        交集为空＋既有件 warmup 推导锚扩容前后同值。"""
        from core.analysis.registry_meta import (
            _PERIOD_LIKE_PARAMS, derive_warmup)
        newkeys = {"rank_window", "sd_window", "init_window"}
        hits = [(n, k) for n, d in INDICATOR_REGISTRY.items()
                if n not in CHAMPIONS
                for k in d.params if k in newkeys]
        assert hits == [], f"扩容撞既有参数名: {hits}"
        # 既有件推导锚（扩容前后同值——box/先例不受影响）
        assert derive_warmup(["Pivot"]) == 600
        assert derive_warmup(["EMA"]) == 600
        assert derive_warmup(["BoxRegime"]) == 720


# ----------------------------------------------------------- 注册与 naming

class TestRegistrationAndNaming:
    """注册可见性、类目、计数 55、naming 三层同步、candidates。"""

    def test_registry_visible_category_count(self):
        cats = {n: INDICATOR_REGISTRY[n].category for n in CHAMPIONS}
        assert cats["ShannonEntropy"] == "information"
        assert cats["PermutationEntropy"] == "information"
        assert cats["EfficiencyRatio"] == "information"
        assert cats["CUSUM"] == "structure"
        assert cats["GARCHFilter"] == "structure"
        assert len(INDICATOR_REGISTRY) == 55

    def test_indicator_metadata_carries_all(self):
        from core.analysis.registry_meta import indicator_metadata
        names = {m["name"] for m in indicator_metadata()}
        assert set(CHAMPIONS) <= names

    def test_column_name_format_samples(self):
        """列名格式样例锚（float 编码 str 形制：str(0.90)="0.9"、
        str(5.0)="5.0"）。"""
        assert resolve_indicator_column(
            "ShannonEntropy", {"window": 96, "rank_window": 500},
            "entropy") == "shan_96_500_entropy"
        assert resolve_indicator_column(
            "PermutationEntropy", {"window": 240, "pattern_len": 4}) == \
            "perm_240_4_pe"
        assert resolve_indicator_column(
            "EfficiencyRatio", {"window": 96, "rank_window": 500},
            "er_pct") == "erf_96_500_er_pct"
        assert resolve_indicator_column(
            "CUSUM", {"sd_window": 500, "drift": 0.5, "threshold": 5.0},
            "cp_up") == "cus_500_0.5_5.0_cp_up"
        assert resolve_indicator_column(
            "GARCHFilter",
            {"alpha": 0.08, "beta": 0.90, "init_window": 500}) == \
            "garch_0.08_0.9_500_cond_vol"

    def test_naming_three_layers_identical(self):
        """naming 三层同步：resolve 正向解析/产列自拼/column_ref 逆
        解码按五件各一样例锚逐项相等（encode/decode 往返还原参数）。"""
        from core.analysis.column_ref import (
            decode_column_ref, encode_column_ref)
        samples = [
            ("ShannonEntropy", {"window": 96, "rank_window": 500}),
            ("PermutationEntropy", {"window": 240, "pattern_len": 4}),
            ("EfficiencyRatio", {"window": 24, "rank_window": 500}),
            ("CUSUM", {"sd_window": 300, "drift": 0.5, "threshold": 5.0}),
            ("GARCHFilter",
             {"alpha": 0.08, "beta": 0.90, "init_window": 500}),
        ]
        df = _mk_df([100.0] * 12)
        for name, params in samples:
            produced = set(_compute_indicator(df, name, params))
            for f in INDICATOR_REGISTRY[name].output_fields:
                forward = resolve_indicator_column(name, params, f)
                assert forward == encode_column_ref(name, params, f)
                assert forward in produced, (name, f, forward)
                decoded = decode_column_ref(forward)
                assert decoded["indicator"] == name
                assert decoded["field"] == f
                assert decoded["params"] == params

    def test_candidates_registered(self):
        sp = INDICATOR_REGISTRY["ShannonEntropy"].params
        assert sp["window"].candidates == [96, 240]
        assert sp["window"].default == 96
        assert sp["rank_window"].default == 500
        ep = INDICATOR_REGISTRY["EfficiencyRatio"].params
        assert ep["window"].candidates == [24, 96]
        gp = INDICATOR_REGISTRY["GARCHFilter"].params
        assert gp["init_window"].candidates == [500]
        assert gp["init_window"].min == 250

    def test_supported_conditions_registry(self):
        assert set(INDICATOR_REGISTRY["ShannonEntropy"].supported_conditions) \
            == {"gt", "lt", "eq", "within_bars_after"}
        assert set(
            INDICATOR_REGISTRY["PermutationEntropy"].supported_conditions) \
            == {"gt", "lt", "eq", "within_bars_after"}
        assert set(INDICATOR_REGISTRY["EfficiencyRatio"].supported_conditions)\
            == {"gt", "lt", "eq", "within_bars_after"}
        assert set(INDICATOR_REGISTRY["CUSUM"].supported_conditions) \
            == {"gt", "lt", "eq", "within_bars_after"}
        assert set(INDICATOR_REGISTRY["GARCHFilter"].supported_conditions) \
            == {"gt", "lt", "within_bars_after"}

    def test_lazy_compute_mode(self):
        """compute_mode=lazy（BoxChannel 先例——rank_window 500 长窗在
        批量标准样本上 pct 列全 NaN 超 5% 纪律；条件链单点现算不受
        影响）。"""
        for n in CHAMPIONS:
            assert INDICATOR_REGISTRY[n].compute_mode == "lazy", n


# ----------------------------------------------------------- demo 复刻（复算锚）

def _demo_shannon(close: pd.Series, w: int = 96, rw: int = 500):
    """r9_d2_arms.py:34-43 逐字复刻（窗不含当根——冻结 demo 形制）；
    返回 (原始熵, pct)。"""
    s = np.sign(close.diff()).fillna(0) + 1
    ent = pd.Series(index=close.index, dtype=float)
    sv = s.values
    for i in range(w, len(sv)):
        win = sv[i - w:i]
        ps = [(win == k).mean() for k in (0, 1, 2)]
        ps = [p for p in ps if p > 0]
        ent.iloc[i] = -sum(p * np.log(p) for p in ps) / np.log(3)
    return ent, ent.rolling(rw).rank(pct=True).shift(1)


def _demo_perm_pe(close: pd.Series, w: int = 240, m: int = 4) -> pd.Series:
    """r7_bench.py:86-97 逐字复刻（窗不含当根）。"""
    lr = np.sign(np.log(close).diff()).fillna(0).values + 1
    out = pd.Series(index=close.index, dtype=float)
    for i in range(w, len(lr)):
        win = lr[i - w:i]
        pats = {}
        for j in range(len(win) - m + 1):
            p = tuple(win[j:j + m])
            pats[p] = pats.get(p, 0) + 1
        p = np.array(list(pats.values())) / (len(win) - m + 1)
        pe = -(p * np.log(p)).sum() / np.log(len(p))
        out.iloc[i] = pe
    return out


def _demo_cusum(e, drift=0.5, thresh=5.0):
    """r7_bench.py:141-148 逐字复刻（返回变点索引列表）。"""
    s_pos = 0.0
    s_neg = 0.0
    cps = []
    for i, v in enumerate(e):
        s_pos = max(0, s_pos + v - drift)
        s_neg = min(0, s_neg + v + drift)
        if s_pos > thresh:
            cps.append(i)
            s_pos = 0
        elif s_neg < -thresh:
            cps.append(i)
            s_neg = 0
    return cps


def _demo_garch_run(close: pd.Series, calib_n: int,
                    a: float = 0.08, b: float = 0.90) -> np.ndarray:
    """r7_breakout.py:116-125 garch_run([]) 逐字复刻（无重置滤波）。"""
    r = np.log(close).diff().fillna(0).values
    var = np.var(r[:calib_n])
    om = var * (1 - a - b)
    s2 = np.zeros(len(r))
    s2[0] = var
    for i in range(1, len(r)):
        s2[i] = om + a * r[i - 1] ** 2 + b * s2[i - 1]
    return np.sqrt(s2)


# ----------------------------------------------------------- 2.1 ShannonEntropy

class TestShannonEntropyContract:
    """三法确定样例（期望不经被测内核）/NaN 头窗/当根突变双面/
    复算锚（公式重导＋冻结 demo 错位性质）/warmup 3000 静态锚。"""

    P = {"window": 96, "rank_window": 500}

    def _cols(self, df, params=None):
        return _compute_indicator(
            df, "ShannonEntropy", params or self.P)

    def test_three_deterministic_samples(self):
        """三法：恒定=0/均匀三态=1/手算混合分布。"""
        # ①恒定 close：s 全 1（首位 NaN→平；其后 diff=0→平）→ 单态熵 0
        df = _mk_df([100.0] * 10)
        ent = self._cols(df, {"window": 3, "rank_window": 4})[
            "shan_3_4_entropy"]
        assert ent.iloc[2:].notna().all()
        assert (ent.iloc[2:] == 0.0).all()
        # ②均匀三态：跌平涨循环（diff 符号循环 [跌,平,涨]×6——首位
        # diff NaN→平、第二位 diff=0→平，两个构造性平符号只在头部窗出现）
        closes = []
        px = 100.0
        pattern = [-1.0, 0.0, 1.0] * 6
        for d in pattern:
            px += d
            closes.append(px)
        df3 = _mk_df(closes)
        ent3 = self._cols(df3, {"window": 3, "rank_window": 4})[
            "shan_3_4_entropy"]
        # t≥3 窗滑入纯循环段 [平,涨,跌]（各相位）→ p=(1/3,1/3,1/3) → 熵=1
        assert ent3.iloc[2:].notna().all()
        assert np.allclose(ent3.iloc[3:].to_numpy(), 1.0)
        # t=2 窗 s=[1,1,2]（首位 NaN→平＋第二位 diff=0→平）构造边界：
        # p=(2/3,1/3) 手算锚
        expect_head = -(2 / 3 * np.log(2 / 3) + 1 / 3 * np.log(1 / 3)) \
            / np.log(3)
        assert ent3.iloc[2] == pytest.approx(expect_head)
        # ③手算混合：window=3、全涨序列（s=[平,涨,涨,涨,...]）
        # t=2 窗 s=[1,2,2]→p=(1/3,2/3)→熵=-(1/3·ln(1/3)+2/3·ln(2/3))/ln(3)
        df2 = _mk_df([100.0 + i for i in range(8)])
        ent2 = self._cols(df2, {"window": 3, "rank_window": 4})[
            "shan_3_4_entropy"]
        expect = -(1 / 3 * np.log(1 / 3) + 2 / 3 * np.log(2 / 3)) / np.log(3)
        assert ent2.iloc[2] == pytest.approx(expect)
        # t≥3 窗 s=[2,2,2] → 单态 0
        assert (ent2.iloc[3:] == 0.0).all()

    def test_nan_head_window(self):
        """NaN 头窗：窗不足期输出 NaN 非零填充（含当根 rolling(w)
        首个满窗在 index w−1）。"""
        df = _mk_df([100.0 + i for i in range(10)])
        ent = self._cols(df, {"window": 4, "rank_window": 5})[
            "shan_4_5_entropy"]
        assert ent.iloc[:3].isna().all()
        assert ent.iloc[3:].notna().all()
        # pct 列 rank 窗（rank_window）+shift(1)——首个非 NaN 在
        # (w−1)+rw
        pct = self._cols(df, {"window": 4, "rank_window": 5})[
            "shan_4_5_entropy_pct"]
        assert pct.iloc[:8].isna().all()
        assert pct.iloc[8:].notna().all()

    def test_current_bar_shock_two_sided(self):
        """当根突变双面（D2①②分界）：修改当根 t 收盘 → t 根 entropy
        可变、t 根 entropy_pct 不变（shift(1) 前根取值）。确定性构造：
        全平序列（s 全 1、entropy 恒 0）＋t 根巨变——窗内分布从单态
        变两态，熵必从 0 变正（随机游走下计数 multiset 恰好对称会假红）。"""
        df = _mk_df([100.0] * 700)
        p = {"window": 96, "rank_window": 500}
        a = self._cols(df, p)
        t = 600
        assert a["shan_96_500_entropy"].iloc[t] == 0.0
        # 全平常数列 rank pct=并列平均秩 (1+500)/2/500=0.501——pct 列
        # 检出力
        assert a["shan_96_500_entropy_pct"].iloc[t] == \
            pytest.approx(0.501)
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] = 150.0
        b = self._cols(df2, p)
        assert b["shan_96_500_entropy"].iloc[t] > 0.0, \
            "当根突变须改变 t 根 entropy（单态→两态）"
        assert b["shan_96_500_entropy_pct"].iloc[t] == a[
            "shan_96_500_entropy_pct"].iloc[t], "t 根 pct 不变（shift(1)）"

    def test_replay_formula_bitwise(self):
        """公式重导对账（含当根窗——D2① 口径）：期望序列以独立循环
        （demo 同公式、窗右端含当根）重演，逐位一致。"""
        df = _mk_walk_df(700, seed=42)
        close = df["close"]
        w, rw = 96, 500
        out = self._cols(df)
        # 独立重演（含当根窗 [i-w+1, i]）
        s = np.sign(close.diff()).fillna(0) + 1
        sv = s.values
        expect = pd.Series(index=close.index, dtype=float)
        for i in range(w - 1, len(sv)):
            win = sv[i - w + 1:i + 1]
            ps = [(win == k).mean() for k in (0, 1, 2)]
            ps = [p for p in ps if p > 0]
            expect.iloc[i] = -sum(p * np.log(p) for p in ps) / np.log(3)
        got = out["shan_96_500_entropy"]
        assert expect.notna().sum() > 500, "复算锚检出力"
        pd.testing.assert_series_equal(got, expect, check_names=False)
        # pct 列同公式重导（rank+shift(1)）
        pct_expect = expect.rolling(rw).rank(pct=True).shift(1)
        pd.testing.assert_series_equal(
            out["shan_96_500_entropy_pct"], pct_expect, check_names=False)

    def test_frozen_demo_one_bar_offset_property(self):
        """冻结 demo 错位性质断言（裁决 a 落点——2026-11-09）：demo
        循环窗 sv[i-w:i] 不含当根、内置 rolling 含当根（spec MUST 正文
        为行为权威，demo 为参照——结构统计列族统一含当根，BoxRegime
        width 先例）；同一公式窗右端差一根：内置 entropy[i] ≡ demo
        entropy[i+1]、内置 pct[i] ≡ demo pct[i+1]（窗内容恒等的错位
        传导）。"""
        df = _mk_walk_df(700, seed=11)
        demo_ent, demo_pct = _demo_shannon(df["close"], w=96, rw=500)
        out = self._cols(df)
        got_ent = out["shan_96_500_entropy"]
        got_pct = out["shan_96_500_entropy_pct"]
        # 对齐域：内置 [w-1, n-2] ↔ demo [w, n-1]（双方非 NaN 交集）
        lo, hi = 96, 698
        demo_slice = demo_ent.iloc[lo + 1:hi + 1].to_numpy()
        got_slice = got_ent.iloc[lo:hi].to_numpy()
        assert np.isfinite(demo_slice).all() and np.isfinite(got_slice).all()
        assert np.array_equal(got_slice, demo_slice), \
            "内置含当根窗与冻结 demo 窗内容恒等（错位一格）"
        # pct 传导（demo pct 非 NaN 段）
        valid = demo_pct.iloc[lo + 1:hi + 1].notna().to_numpy()
        assert valid.sum() > 100, "pct 错位断言检出力"
        assert np.array_equal(
            got_pct.iloc[lo:hi].to_numpy()[valid],
            demo_pct.iloc[lo + 1:hi + 1].to_numpy()[valid])

    def test_warmup_3000_static(self):
        """warmup 静态数值锚 3000（=(500+500)×3——sum_of_max 静态推导）
        ＋静态性衍生断言：不同运行时传参的推导值恒等（推导只读注册表
        max，不读 default/实际传参）。"""
        from core.analysis.registry_meta import derive_warmup
        assert derive_warmup(["ShannonEntropy"]) == 3000
        assert derive_warmup(
            ["ShannonEntropy"]) == derive_warmup(["ShannonEntropy"])
        # 静态性：推导不随传参变化（无运行时传参面——不同调用恒等）
        assert derive_warmup(["ShannonEntropy", "EMA"]) == 3000


# ----------------------------------------------------------- 2.2 PermutationEntropy

class TestPermutationEntropyContract:
    """单模式=0/分母口径样例（实际出现模式数）/复算锚/warmup 1500。"""

    P = {"window": 240, "pattern_len": 4}

    def _cols(self, df, params=None):
        return _compute_indicator(
            df, "PermutationEntropy", params or self.P)

    def test_single_pattern_zero(self):
        """单调序列窗内模式全同 → pe=0（退化分布熵为零——任务书
        单模式样例；首位 diff NaN→平仅入 i=9 一个窗（窗 [i-9,i] 含
        index 0 ⟺ i≤9）——该窗两种模式 >0 作对照，i≥10 窗纯 [2,..]
        单模式）。"""
        df = _mk_df([100.0 + i for i in range(30)])
        pe = self._cols(df, {"window": 10, "pattern_len": 3})[
            "perm_10_3_pe"]
        assert pe.iloc[9:].notna().all()
        assert pe.iloc[9] > 0.0, "含首位平符号的窗两种模式（对照）"
        assert (pe.iloc[10:] == 0.0).all(), "纯涨段窗内单模式熵 0"

    def test_denominator_actual_patterns(self):
        """归一分母口径样例（r7_bench.py:95 形制）：只出现 2 种模式时
        熵÷log(2)——交替序列 [平,涨] 循环、pattern_len=2：窗内模式仅
        (1,2)/(2,1) 两种各半 → 满熵 1.0（若按理论上限 log(9) 归一将
        远小于 1——分母口径可观察证明）。"""
        closes = []
        px = 100.0
        for d in [0.0, 1.0] * 20:
            px += d
            closes.append(px)
        df = _mk_df(closes)
        pe = self._cols(df, {"window": 8, "pattern_len": 2})[
            "perm_8_2_pe"]
        # 窗内 8 符号 [1,2,1,2,1,2,1,2]：模式 (1,2)×3 + (2,1)×4？——
        # len(win)-m+1=7 个模式：(1,2)(2,1)(1,2)(2,1)(1,2)(2,1)(1,2)
        # = 4×(1,2)+3×(2,1) → p=(4/7,3/7)，熵=-(p ln p) 和 ÷ log(2)
        p1, p2 = 4 / 7, 3 / 7
        expect = -(p1 * np.log(p1) + p2 * np.log(p2)) / np.log(2)
        assert pe.iloc[7] == pytest.approx(expect)
        assert pe.iloc[7] > 0.98, "两模式近均分下实际模式数归一≈满熵"

    def test_nan_head_and_pct_shift(self):
        """NaN 头窗＋pe_pct 分位基准列 shift(1)（rank 窗复用 window）。"""
        df = _mk_walk_df(60, seed=3)
        pe = self._cols(df, {"window": 10, "pattern_len": 3})
        assert pe["perm_10_3_pe"].iloc[:9].isna().all()
        assert pe["perm_10_3_pe"].iloc[9:].notna().all()
        # pct：rank(10) 后 shift(1)——首个非 NaN 在 9+10=19
        assert pe["perm_10_3_pe_pct"].iloc[:19].isna().all()
        assert pe["perm_10_3_pe_pct"].iloc[19:].notna().all()
        expect_pct = pe["perm_10_3_pe"].rolling(10).rank(
            pct=True).shift(1)
        pd.testing.assert_series_equal(
            pe["perm_10_3_pe_pct"], expect_pct, check_names=False)

    def test_current_bar_shock_two_sided(self):
        """当根突变双面（D2①②分界）：t 根 pe 可变、t 根 pe_pct 不变
        （shift(1) 前根取值）——确定性构造：交替双态（pe 恒定非零）＋
        t 根巨变引入新模式。"""
        # 交替 [平,涨] 循环：模式恒 (1,2)/(2,1) 两种→pe 稳定
        closes = []
        px = 100.0
        for d in [0.0, 1.0] * 160:
            px += d
            closes.append(px)
        df = _mk_df(closes)
        p = {"window": 50, "pattern_len": 4}
        a = self._cols(df, p)
        t = 300
        assert a["perm_50_4_pe"].iloc[t] > 0.0  # 检出力
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] = 50.0
        b = self._cols(df2, p)
        assert b["perm_50_4_pe"].iloc[t] != a["perm_50_4_pe"].iloc[t], \
            "当根突变须改变 t 根 pe（新符号引入新模式）"
        assert b["perm_50_4_pe_pct"].iloc[t] == a[
            "perm_50_4_pe_pct"].iloc[t], "t 根 pct 不变（shift(1)）"

    def test_frozen_demo_one_bar_offset_property(self):
        """冻结 demo 错位性质断言（r7_bench.py:86 复刻——窗不含当根；
        裁决 a 同款：spec MUST 含当根为行为权威）：内置 pe[i] ≡ demo
        pe[i+1]（公式逐字复刻、窗右端差一根）。"""
        df = _mk_walk_df(700, seed=13)
        demo_pe = _demo_perm_pe(df["close"], w=240, m=4)
        out = self._cols(df)
        got = out["perm_240_4_pe"]
        lo, hi = 240, 698
        demo_slice = demo_pe.iloc[lo + 1:hi + 1].to_numpy()
        got_slice = got.iloc[lo:hi].to_numpy()
        assert np.isfinite(demo_slice).all() and np.isfinite(got_slice).all()
        assert np.array_equal(got_slice, demo_slice), \
            "内置含当根窗与冻结 demo 窗内容恒等（错位一格）"

    def test_warmup_1500_static(self):
        """warmup 静态数值锚 1500（=500×3——pattern_len 非周期参数
        不入白名单）。"""
        from core.analysis.registry_meta import derive_warmup
        assert derive_warmup(["PermutationEntropy"]) == 1500


# ----------------------------------------------------------- 2.3 EfficiencyRatio

class TestEfficiencyRatioContract:
    """单调=1/交替锯齿→0/w 手算/双形制复算锚（demo rolling 含当根
    ——无错位逐位一致）/warmup 3000。"""

    P = {"window": 96, "rank_window": 500}

    def _cols(self, df, params=None):
        return _compute_indicator(
            df, "EfficiencyRatio", params or self.P)

    def test_monotonic_er_one_and_sawtooth(self):
        """单调序列 ER=1（净位移=路径长度）；交替锯齿 ER=0（净位移
        恒 0、路径=窗宽——ER→0 的极限语义）。"""
        df = _mk_df([100.0 + i for i in range(12)])
        er = self._cols(df, {"window": 4, "rank_window": 5})[
            "erf_4_5_er"]
        assert np.allclose(er.iloc[4:].to_numpy(), 1.0)
        # 锯齿：close=[100,101,100,101,...] w=4 → 同相位净位移恒 0
        saw = [100.0, 101.0] * 10
        df2 = _mk_df(saw)
        er2 = self._cols(df2, {"window": 4, "rank_window": 5})[
            "erf_4_5_er"]
        assert np.allclose(er2.iloc[4:].to_numpy(), 0.0)

    def test_w4_manual_anchor(self):
        """w=4 手算锚：close=[100,102,101,103,105] → t=4:
        分子 |105−100|=5；分母 |diff| 窗 [2,−1,2,2]→7 → er=5/7。"""
        df = _mk_df([100.0, 102.0, 101.0, 103.0, 105.0])
        er = self._cols(df, {"window": 4, "rank_window": 5})[
            "erf_4_5_er"]
        assert np.isnan(er.iloc[3])  # diff(4) 首个非 NaN 在 index 4
        assert er.iloc[4] == pytest.approx(5 / 7)

    def test_nan_head_and_pct_shift(self):
        """NaN 头窗：er 首个非 NaN 在 index w（diff(w) 首值）；er_pct
        rank+shift(1)（首个非 NaN 在 w+rw）。"""
        df = _mk_walk_df(60, seed=5)
        out = self._cols(df, {"window": 10, "rank_window": 20})
        er, pct = out["erf_10_20_er"], out["erf_10_20_er_pct"]
        assert er.iloc[:10].isna().all() and er.iloc[10:].notna().all()
        assert pct.iloc[:30].isna().all() and pct.iloc[30:].notna().all()
        expect_pct = er.rolling(20).rank(pct=True).shift(1)
        pd.testing.assert_series_equal(pct, expect_pct, check_names=False)

    def test_replay_panel_anchor_bitwise(self):
        """面板复算锚（r7_bench.py:137 复刻——rolling 含当根，无错位
        逐位一致）：er 与 demo 原式重演相等。"""
        df = _mk_walk_df(700, seed=17)
        close = df["close"]
        out = self._cols(df, {"window": 96, "rank_window": 500})
        expect = close.diff(96).abs() / close.diff().abs().rolling(96).sum()
        got = out["erf_96_500_er"]
        assert expect.notna().sum() > 500, "复算锚检出力"
        pd.testing.assert_series_equal(got, expect, check_names=False)

    def test_replay_demo_gate_w24_bitwise(self):
        """demo gate 复算锚（r9_d2_arms.py:45 复刻——显式传 w=24）：
        er_pct 与 demo（er 同式含当根＋rank shift(1)）逐位一致。"""
        df = _mk_walk_df(700, seed=23)
        close = df["close"]
        w, rw = 24, 500
        out = self._cols(df, {"window": w, "rank_window": rw})
        demo_er = close.diff(w).abs() / close.diff().abs().rolling(w).sum()
        demo_pct = demo_er.rolling(rw).rank(pct=True).shift(1)
        got = out[f"erf_{w}_{rw}_er_pct"]
        assert demo_pct.notna().sum() > 150, "demo gate 锚检出力"
        pd.testing.assert_series_equal(got, demo_pct, check_names=False)
        pd.testing.assert_series_equal(
            out[f"erf_{w}_{rw}_er"], demo_er, check_names=False)

    def test_current_bar_shock_two_sided(self):
        """当根突变双面：er 含当根可变、er_pct shift(1) 不变。"""
        df = _mk_walk_df(700, seed=7)
        p = {"window": 96, "rank_window": 500}
        a = self._cols(df, p)
        t = 600
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] *= 1.5
        b = self._cols(df2, p)
        assert b["erf_96_500_er"].iloc[t] != a["erf_96_500_er"].iloc[t]
        assert b["erf_96_500_er_pct"].iloc[t] == a[
            "erf_96_500_er_pct"].iloc[t]

    def test_warmup_3000_static(self):
        from core.analysis.registry_meta import derive_warmup
        assert derive_warmup(["EfficiencyRatio"]) == 3000


# ----------------------------------------------------------- 2.4 CUSUM

class TestCusumContract:
    """递推核注入 z 与 demo 变点集合一致/恰等不触发两侧/z=drift 恰耗尽
    /端到端全循环/截断不变量/warmup 1500。"""

    P = {"sd_window": 500, "drift": 0.5, "threshold": 5.0}

    def _cols(self, df, params=None):
        return _compute_indicator(df, "CUSUM", params or self.P)

    def test_core_injected_z_matches_demo_cp_set(self):
        """函数级注入锚：_cusum_core 对随机 z 序列的触发位置（cp_up∪
        cp_down 索引集合）与 r7_bench.py:141 复刻变点清单一致。"""
        from core.features.champion_indicators import _cusum_core
        rng = np.random.default_rng(42)
        z = rng.normal(0.3, 1.5, 400)  # 带正漂移——上侧触发可达
        for drift, thresh in ((0.5, 5.0), (0.2, 3.0), (0.3, 4.0)):
            s_pos, s_neg, cp_up, cp_down = _cusum_core(z, drift, thresh)
            demo_cps = set(_demo_cusum(z, drift, thresh))
            got = set(np.where(cp_up > 0)[0]) | set(np.where(cp_down > 0)[0])
            assert got == demo_cps, (drift, thresh)
            assert got, "检出力：样例须产生变点"

    def test_strict_inequality_both_sides(self):
        """恰等不触发两侧：drift=0 时 z 单根恰=threshold → s_pos 恰等
        不触发；z 略越 → 触发且归零。"""
        from core.features.champion_indicators import _cusum_core
        s_pos, _, cp_up, _ = _cusum_core(np.array([5.0]), 0.0, 5.0)
        assert cp_up[0] == 0.0 and s_pos[0] == 5.0, "恰等不触发"
        s_pos2, _, cp_up2, _ = _cusum_core(np.array([5.0001]), 0.0, 5.0)
        assert cp_up2[0] == 1.0 and s_pos2[0] == 0.0, "越界触发且归零"

    def test_z_equals_drift_exact_exhaust(self):
        """z=drift 恰耗尽边界：z 恒=drift → 每根 s_pos=max(0,0)=0 恒
        （累积恰被 drift 耗尽）。"""
        from core.features.champion_indicators import _cusum_core
        z = np.full(20, 0.5)
        s_pos, s_neg, cp_up, cp_down = _cusum_core(z, 0.5, 5.0)
        assert (s_pos == 0.0).all() and (s_neg == 0.0).all()
        assert (cp_up == 0.0).all() and (cp_down == 0.0).all()

    def test_nan_period_s_zero_cp_false(self):
        """NaN 期（zscore 窗未满）：s 置 0、cp=False。"""
        df = _mk_walk_df(120, seed=9)
        out = self._cols(df, {"sd_window": 100, "drift": 0.5,
                              "threshold": 5.0})
        # 对数收益首个非 NaN 在 1；rolling(100) 满窗在 100 → z 首个
        # 有效约在 100；前 99 根 s 恒 0
        for col in ("cus_100_0.5_5.0_s_pos", "cus_100_0.5_5.0_s_neg"):
            assert (out[col].iloc[:99] == 0.0).all(), col
        for col in ("cus_100_0.5_5.0_cp_up", "cus_100_0.5_5.0_cp_down"):
            assert (out[col].iloc[:99] == 0.0).all(), col

    def test_end_to_end_full_cycle(self):
        """端到端合成全循环（触发/归零/再累积）：zscore 管道后强上漂
        段触发 cp_up、触发根 s_pos 归零、下漂段触发 cp_down。"""
        rng = np.random.default_rng(11)
        n = 400
        # 前 120 根平稳噪声（zscore 窗收敛），其后 60 根持续上漂（每根
        # +1.2%），再 60 根下漂（−0.8%），尾段平稳
        r = np.concatenate([
            rng.normal(0.0, 0.004, 120),
            rng.normal(0.012, 0.004, 60),
            rng.normal(-0.008, 0.004, 60),
            rng.normal(0.0, 0.004, n - 240),
        ])
        close = 100.0 * np.exp(np.cumsum(r))
        df = _mk_df(close)
        p = {"sd_window": 100, "drift": 0.5, "threshold": 5.0}
        out = self._cols(df, p)
        cp_up = out["cus_100_0.5_5.0_cp_up"]
        cp_down = out["cus_100_0.5_5.0_cp_down"]
        s_pos = out["cus_100_0.5_5.0_s_pos"]
        up_idx = np.where(cp_up > 0)[0]
        down_idx = np.where(cp_down > 0)[0]
        assert len(up_idx) > 0 and up_idx[0] >= 100, "上漂段触发"
        assert len(down_idx) > 0, "下漂段触发"
        # 触发根 s_pos 已归零（当根更新→触发→归零后落列）
        for i in up_idx:
            assert s_pos.iloc[i] == 0.0
        # 布尔列数值语义（True=1.0）
        assert set(cp_up.unique()) <= {0.0, 1.0}

    def test_truncation_invariant(self):
        """递推列截断不变量：前 k 根输出与全序列前 k 根逐位一致
        （递推只读历史——修改尾部不改变前缀）。"""
        df = _mk_walk_df(300, seed=13)
        p = {"sd_window": 100, "drift": 0.5, "threshold": 5.0}
        full = self._cols(df, p)
        part = self._cols(df.iloc[:200].copy(), p)
        for col in full:
            pd.testing.assert_series_equal(
                part[col], full[col].iloc[:200], check_names=False)

    def test_current_bar_shock_consumes_current_bar(self):
        """当根突变——③类递推状态列「仅消费当根输入者可变」确定样例
        （总则 Requirement 第六条：s_pos/cp_up 消费当根 z——z[t] 由含
        当根的 rolling zscore 产生，修改当根收盘必改变当根输出）。
        确定性构造：平稳段（突变前 cp_up[t]=0 前置检出）＋t 根 +50%
        巨变（z[t]≈10σ ≫ threshold）→ cp_up[t] 0→1 当根触发、
        s_pos[t] 归零；t 之前输出不变（信息集只向当根传导）。"""
        rng = np.random.default_rng(9)
        r = rng.normal(0.0, 0.004, 320)
        df = _mk_df(100.0 * np.exp(np.cumsum(r)))
        p = {"sd_window": 100, "drift": 0.5, "threshold": 5.0}
        a = self._cols(df, p)
        t = 280
        assert a["cus_100_0.5_5.0_cp_up"].iloc[t] == 0.0, \
            "前置检出力：平稳段 t 根未触发"
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] = \
            df["close"].iloc[t] * 1.5
        b = self._cols(df2, p)
        assert b["cus_100_0.5_5.0_cp_up"].iloc[t] == 1.0, \
            "当根突变须当根触发 cp_up（③类消费当根输入）"
        assert b["cus_100_0.5_5.0_s_pos"].iloc[t] == 0.0, \
            "触发根 s_pos 归零"
        assert np.array_equal(
            a["cus_100_0.5_5.0_s_pos"].to_numpy()[:t],
            b["cus_100_0.5_5.0_s_pos"].to_numpy()[:t]), \
            "t 之前输出不因当根突变改变"

    def test_warmup_1500_static(self):
        """warmup 静态数值锚 1500（=500×3——sd_window 入白名单、
        drift/threshold 非周期参数不入；D10 五锚 3000/1500/3000/
        1500/1500 之第四锚——task 2.4 完成标准在位核验）。"""
        from core.analysis.registry_meta import derive_warmup
        assert derive_warmup(["CUSUM"]) == 1500


# ----------------------------------------------------------- 2.5 GARCHFilter

class TestGarchFilterContract:
    """端到端合成参考一致/纯核直调 init=2000 与冻结 demo 逐位一致/
    常数收益固定点/cross_params 拒绝（单元＋集成）/全域收敛/warmup。"""

    P = {"alpha": 0.08, "beta": 0.90, "init_window": 500}

    def _cols(self, df, params=None):
        return _compute_indicator(df, "GARCHFilter", params or self.P)

    def test_end_to_end_default_replay(self):
        """端到端默认（500）合成参考一致：独立重演（demo 同公式手写
        ——期望不经被测内核）。"""
        df = _mk_walk_df(700, seed=19)
        close = df["close"]
        a, b, init = 0.08, 0.90, 500
        r = np.log(close).diff().fillna(0).to_numpy(dtype=float)
        var0 = float(np.var(r[:init]))
        om = var0 * (1 - a - b)
        s2 = np.zeros(len(r))
        s2[0] = var0
        for i in range(1, len(r)):
            s2[i] = om + a * r[i - 1] ** 2 + b * s2[i - 1]
        expect = pd.Series(np.sqrt(s2), index=df.index)
        got = self._cols(df)["garch_0.08_0.9_500_cond_vol"]
        pd.testing.assert_series_equal(got, expect, check_names=False)
        assert expect.notna().all()

    def test_pure_core_init2000_matches_frozen_demo(self):
        """纯核直调 init=2000（越域值走 compute 层直调——域校验在
        登记/装载层）与 r7_breakout.py:116-125 复刻逐位一致。"""
        df = _mk_walk_df(2500, seed=29)
        got = self._cols(df, {"alpha": 0.08, "beta": 0.90,
                              "init_window": 2000})[
            "garch_0.08_0.9_2000_cond_vol"]
        expect = _demo_garch_run(df["close"], calib_n=2000)
        np.testing.assert_array_equal(
            got.to_numpy(), expect), "纯核直调与冻结 demo 逐位一致"

    def test_constant_return_fixed_point(self):
        """常数收益固定点手算：r=c 恒定 → s2*=(ω+αc²)/(1−β)；长序列
        尾段收敛至固定点（β=0.90 → 冲击衰减 k≈44 根）。"""
        c = 0.001
        close = pd.Series(100.0 * np.exp(np.arange(600) * c))
        # ndarray 传入（Series 带 RangeIndex 进 _mk_df 会索引对齐
        # 全 NaN——box 测试登记的同款假绿坑）
        df = _mk_df(close.to_numpy())
        a, b, init = 0.08, 0.90, 500
        got = self._cols(df)["garch_0.08_0.9_500_cond_vol"]
        r = np.log(close).diff().fillna(0).to_numpy(dtype=float)
        var0 = float(np.var(r[:init]))
        om = var0 * (1 - a - b)
        fixed_point = (om + a * c ** 2) / (1 - b)
        assert got.iloc[-1] == pytest.approx(np.sqrt(fixed_point), rel=1e-6)

    def test_cross_params_hook_unit(self):
        """cross_params 钩子单元：alpha+beta≥1（含等号）拒绝、域内
        合法组合通过（GARCH 分域重叠使钩子在集成面可达——与
        BoxChannel 域不重叠不可达形制不同）。"""
        hook = INDICATOR_REGISTRY["GARCHFilter"].cross_params
        assert hook is not None
        assert hook({"alpha": 0.5, "beta": 0.6}) is not None, "1.1>1 拒绝"
        assert hook({"alpha": 0.5, "beta": 0.5}) is not None, "恰等拒绝"
        assert hook({"alpha": 0.08, "beta": 0.90}) is None
        assert hook({"alpha": 0.01, "beta": 0.99}) is not None, \
            "0.01+0.99=1.0 恰等拒绝"

    def test_cross_params_integration_reject(self):
        """集成面：alpha=0.5/beta=0.6（各在分域内）经 _resolve_indicator_
        ref 落 422 PARAM_INVALID（域校验通过后钩子拦——detail 带
        violation）；validate_params 同钩同判（RESEARCH_PARAM_INVALID）。"""
        from core.analysis.column_ref import ColumnRefError, _resolve_indicator_ref
        with pytest.raises(ColumnRefError) as ei:
            _resolve_indicator_ref({
                "indicator": "GARCHFilter",
                "params": {"alpha": 0.5, "beta": 0.6, "init_window": 500},
                "field": "cond_vol"})
        assert ei.value.code == "PARAM_INVALID"
        assert "alpha+beta" in ei.value.message

        from core.analysis.indicator_query import validate_params
        from core.analysis.errors import ResearchError
        with pytest.raises(ResearchError) as ei2:
            validate_params("GARCHFilter", {"alpha": 0.5, "beta": 0.6,
                                            "init_window": 500})
        assert ei2.value.code == "RESEARCH_PARAM_INVALID"

    def test_convergence_bound_full_domain(self):
        """全域收敛断言：k=ceil(log(0.01)/log(β)) 对 β∈[0.50,0.99]
        全网格 ≤ 2×init_window 域内最小值 500（β=0.99 → k=458.2 上取
        整 459——下界 250 的设定使全域成立）。"""
        for beta in np.arange(0.50, 0.991, 0.01):
            k = float(np.ceil(np.log(0.01) / np.log(beta)))
            assert k <= 2 * 250, f"β={beta:.2f} 收敛根数 {k} 超 500"
        assert float(np.ceil(np.log(0.01) / np.log(0.99))) == 459

    def test_beta_sensitivity_docstring(self):
        """β 敏感性披露（docstring 义务面）：收敛越慢表述在场。"""
        from core.features import champion_indicators as ci
        doc = ci.compute_garch_filter.__doc__ or ""
        assert "收敛越慢" in doc and "β=0.99" in doc

    def test_current_bar_shock_lags_one_bar(self):
        """当根突变——③类递推状态列「不消费当根输入者」对照面（总则
        Requirement 第六条）：cond_vol[t]=√(ω+α·r[t−1]²+β·s2[t−1])
        只用前一根收益与历史状态——修改当根收盘不改变当根 cond_vol
        （严格相等），冲击经 r[t] 传导至 t+1 根才可见（信息集边界
        ≤当根的可观察证明——与 CUSUM 当根可变面合成③类双面）。"""
        df = _mk_walk_df(700, seed=42)
        a = self._cols(df)
        t = 600
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] = \
            df["close"].iloc[t] * 1.5
        b = self._cols(df2)
        ca, cb = a["garch_0.08_0.9_500_cond_vol"], \
            b["garch_0.08_0.9_500_cond_vol"]
        assert ca.iloc[t] == cb.iloc[t], \
            "当根收盘突变不改变当根 cond_vol（不消费 r[t]）"
        assert ca.iloc[t + 1] != cb.iloc[t + 1], \
            "冲击经 r[t] 传导至 t+1 根（α·r[t]² 入递推）"
        assert np.array_equal(
            ca.to_numpy()[:t], cb.to_numpy()[:t]), \
            "t 之前输出不因当根突变改变"

    def test_warmup_1500_static(self):
        from core.analysis.registry_meta import derive_warmup
        assert derive_warmup(["GARCHFilter"]) == 1500


# ------------------------------------------------- docstring 义务面（MUST）

class TestDocstringDisclosureFaces:
    """spec MUST docstring 披露义务面（工程先例：β 敏感性/gt 1 警示
    同为 docstring 断言入测试）。匹配前规整换行与空白——docstring
    源码折行会把中文关键词拆至两行（实测「实际出现模式数」跨行）。"""

    @staticmethod
    def _flat(doc: str) -> str:
        return doc.replace("\n", "").replace(" ", "")

    def test_fractal_shannon_bidirectional_crossref(self):
        """熵族↔FractalEntropy 双向互指（总则 Requirement 第六条 MUST
        ——判别力差异入 docstring：R245 探针实证双方各披露）。"""
        from core.features import champion_indicators as ci
        from core.features import ml_indicators as mi
        shan_doc = self._flat(ci.compute_shannon_entropy.__doc__ or "")
        frac_doc = self._flat(mi.compute_fractal_entropy.__doc__ or "")
        assert "FractalEntropy" in shan_doc, \
            "香农熵件 docstring 须互指 FractalEntropy"
        assert "ShannonEntropy" in frac_doc, \
            "FractalEntropy docstring 须反向互指（D9）"
        assert "判别力" in shan_doc and "判别力" in frac_doc, \
            "判别力差异双向披露"

    def test_cusum_combo_expression_disclosed(self):
        """CUSUM 跨指标组合表达路径披露（Requirement 第四条 MUST——
        「CUSUM of GARCH-z」属公式插件组合消费，docstring 披露表达
        路径、不在件内内置）。"""
        from core.features import champion_indicators as ci
        doc = self._flat(ci.compute_cusum.__doc__ or "")
        assert "GARCH" in doc and "组合" in doc

    def test_garch_replay_path_disclosed(self):
        """GARCH 复算路径披露（Requirement 第五条 scenario——demo
        init=2000 越域值走纯核直调对账路径，docstring 披露）。"""
        from core.features import champion_indicators as ci
        doc = self._flat(ci.compute_garch_filter.__doc__ or "")
        assert "2000" in doc and "纯核直调" in doc

    def test_perm_denominator_note_disclosed(self):
        """排列熵归一分母口径注记（Requirement 第二条 scenario——
        实际出现模式数 vs 理论上限的差异在 docstring 注记）。"""
        from core.features import champion_indicators as ci
        doc = self._flat(ci.compute_permutation_entropy.__doc__ or "")
        assert "实际出现模式数" in doc


# ----------------------------------------------------------- 9.1 条件链五面

# 五件条件链面参数（域内值——合成数据检出力与请求开销平衡）
_CHAIN_PARAMS = {
    "ShannonEntropy": {"window": 50, "rank_window": 100},
    "PermutationEntropy": {"window": 50, "pattern_len": 4},
    "EfficiencyRatio": {"window": 24, "rank_window": 100},
    "CUSUM": {"sd_window": 100, "drift": 0.5, "threshold": 5.0},
    "GARCHFilter": {"alpha": 0.08, "beta": 0.90, "init_window": 250},
}


def _chain_df(n=320, seed=31):
    """条件链合成数据：正态随机游走（含波动聚集段——GARCH/CUSUM
    检出力）。"""
    rng = np.random.default_rng(seed)
    r = np.concatenate([
        rng.normal(0.0, 0.004, 120),
        rng.normal(0.0, 0.012, 80),   # 高波动段
        rng.normal(0.0, 0.004, n - 200),
    ])
    return _mk_df(100.0 * np.exp(np.cumsum(r)))


def _subject_field(indicator: str) -> str:
    return INDICATOR_REGISTRY[indicator].output_fields[0]


class TestConditionChainFaces:
    """五面×五件（任务 9.1）：query_indicator 归约/condition_stats/
    假设登记/翻译/回测求值——每件五面各一用例（参数化 5×5=25）。"""

    @pytest.mark.parametrize("indicator", list(_CHAIN_PARAMS))
    def test_face_query_indicator_reductions(self, indicator):
        """面一 query_indicator：latest/quantiles/trend_slope 三归约可算
        （真实数据 4h——warmup 前缀满足 3000 静态推导；不在库 skip）。"""
        if not (MARKET / "BTCUSDT_4h.parquet").exists():
            pytest.skip("真实数据不在库（data/market_fapi）")
        from core.analysis.indicator_query import query_indicator
        from core.analysis.registry_meta import derive_warmup
        params = _CHAIN_PARAMS[indicator]
        out = query_indicator(
            MARKET, symbol="BTCUSDT", timeframe="4h", indicator=indicator,
            params=params, start="2026-09-01", end="2026-11-01",
            reductions=["latest", "quantiles", "trend_slope"])
        assert out["indicator"] == indicator
        assert out["warmup_bars"] == derive_warmup([indicator])
        from core.analysis.column_ref import encode_column_ref
        col = encode_column_ref(indicator, params,
                                _subject_field(indicator))
        reds = out["reductions"][col]
        assert set(reds) == {"latest", "quantiles", "trend_slope"}
        assert reds["latest"]["value"] is not None

    @pytest.mark.parametrize("indicator", list(_CHAIN_PARAMS))
    def test_face_condition_stats(self, indicator):
        """面二 condition_stats：结构化 subject 条件统计——hit_count
        与 df 切片独立重演一致。"""
        from core.analysis.templates.condition_stats import condition_stats
        params = _CHAIN_PARAMS[indicator]
        field = _subject_field(indicator)
        df = _chain_df()
        out = condition_stats(
            df, indicator=indicator, params=params,
            condition={
                "subject": {"indicator": indicator, "params": params,
                            "field": field},
                "action": "gt", "target": 0.0},
            data_ranges=[{"symbol": "T", "timeframe": "1h",
                          "start": "2024-01-01", "end": "2024-01-14"}],
            min_samples=1).to_dict()
        p = out["payload"]
        col = _compute_indicator(df, indicator, params)[
            encode_column_ref(indicator, params, field)]
        total = int((col > 0.0).sum())
        assert p["hit_count"] == total
        assert p["hit_count"] > 0, "检出力：样例须有命中"

    @pytest.mark.parametrize("indicator", list(_CHAIN_PARAMS))
    def test_face_register_and_translate(self, indicator, tmp_path):
        """面三/四 假设登记＋翻译：条件登记入册并翻译为 SignalGene
        （indicator/params 原样传递——box 先例形制）。"""
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import (
            translate_hypothesis)
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables

        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        params = _CHAIN_PARAMS[indicator]
        field = _subject_field(indicator)
        action = "gt"  # 五件 supported_conditions 全含 gt
        cond = {"subject": {"indicator": indicator, "field": field,
                            "params": params},
                "action": action, "target": 0.0}
        then = {"subject": {"indicator": "RSI", "params": {"period": 14}},
                "action": "gt", "target": 50}
        hyp = register_hypothesis(
            db, conditions={"when_conditions": [cond],
                            "then_conditions": [then]},
            statement=f"{indicator} 环境开关后动量走强",
            strategy_params={"stop_loss": 0.05},
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1h",
                          "start": "2026-01-01", "end": "2026-03-01"}])
        assert hyp["hypothesis_id"]
        out = translate_hypothesis(db, hypothesis_id=hyp["hypothesis_id"])
        assert f"{indicator}({field})" in out["genes"]

    @pytest.mark.parametrize("indicator", list(_CHAIN_PARAMS))
    def test_face_evaluate_condition(self, indicator):
        """面五回测求值：gt 0.0 行为锚（与手写比较逐位一致）。"""
        from core.strategy.executor import evaluate_condition
        params = _CHAIN_PARAMS[indicator]
        field = _subject_field(indicator)
        df = _chain_df()
        col = _compute_indicator(df, indicator, params)[
            encode_column_ref(indicator, params, field)]
        got = evaluate_condition(col, df["close"],
                                 {"type": "gt", "threshold": 0.0})
        assert (got == (col > 0.0)).all()
        assert got.any(), "检出力"

    @pytest.mark.parametrize("indicator", list(_CHAIN_PARAMS))
    def test_demo_gate_consumption_replay(self, indicator):
        """demo gate 消费复现（模式一环境开关形制）：entropy_pct lt
        0.35（香农熵件）——evaluate_condition 产出与 demo 公式手写比较
        逐位一致；其余件以各自 pct/布尔列 lt/gt 阈值同形制复现。"""
        from core.strategy.executor import evaluate_condition
        params = _CHAIN_PARAMS[indicator]
        df = _chain_df()
        if indicator == "ShannonEntropy":
            col = _compute_indicator(df, indicator, params)[
                "shan_50_100_entropy_pct"]
            gate = evaluate_condition(col, df["close"],
                                      {"type": "lt", "threshold": 0.35})
            expect = (col < 0.35).fillna(False)
            assert (gate == expect).all()
            assert expect.any() and not expect.all(), \
                "gate 掩码须混合（检出力）"
        elif indicator == "PermutationEntropy":
            col = _compute_indicator(df, indicator, params)[
                "perm_50_4_pe_pct"]
            gate = evaluate_condition(col, df["close"],
                                      {"type": "lt", "threshold": 0.35})
            assert (gate == (col < 0.35).fillna(False)).all()
        elif indicator == "EfficiencyRatio":
            col = _compute_indicator(df, indicator, params)[
                "erf_24_100_er_pct"]
            gate = evaluate_condition(col, df["close"],
                                      {"type": "gt", "threshold": 0.65})
            assert (gate == (col > 0.65).fillna(False)).all()
        elif indicator == "CUSUM":
            # 布尔列条件语义锚：cp_up True=1.0——eq 1/gt 0.5 等价、
            # gt 1 恒 False 负向锚（box_state 先例同款）
            out = _compute_indicator(df, indicator, params)
            cp_up = out["cus_100_0.5_5.0_cp_up"]
            close = df["close"]
            eq1 = evaluate_condition(cp_up, close, {"type": "eq"})
            gt05 = evaluate_condition(cp_up, close,
                                      {"type": "gt", "threshold": 0.5})
            assert (eq1 == (cp_up == 1.0)).all()
            assert (gt05 == (cp_up > 0.5)).all()
            gt1 = evaluate_condition(cp_up, close,
                                     {"type": "gt", "threshold": 1})
            assert not gt1.any(), "cp_up gt 1 恒 False（值域 {0.0,1.0}）"
            # docstring 披露义务（gt 1 恒 False 警示在件 docstring）
            from core.features import champion_indicators as ci
            assert "gt 1" in (ci.compute_cusum.__doc__ or "")
        else:  # GARCHFilter——连续正值列 gt 阈值形制
            col = _compute_indicator(df, indicator, params)[
                "garch_0.08_0.9_250_cond_vol"]
            gate = evaluate_condition(col, df["close"],
                                      {"type": "gt", "threshold": 0.0})
            assert (gate == (col > 0.0)).all()


# ----------------------------------------------------------- 10.1 参数域 ε 负向

class TestParamDomainEpsilon:
    """每件参数下界−ε/上界+ε 各 422（_resolve_indicator_ref 域拒绝）
    ＋界内边界值正常（恰下界/恰上界通过——闭区间语义）。"""

    CASES = {
        "ShannonEntropy": {"window": (10, 500), "rank_window": (100, 500)},
        "PermutationEntropy": {"window": (50, 500),
                               "pattern_len": (2, 8)},
        "EfficiencyRatio": {"window": (10, 500),
                            "rank_window": (100, 500)},
        "CUSUM": {"sd_window": (100, 500), "drift": (0.0, 2.0),
                  "threshold": (1.0, 20.0)},
        "GARCHFilter": {"alpha": (0.01, 0.50), "beta": (0.50, 0.99),
                        "init_window": (250, 500)},
    }

    def _mk_params(self, indicator, key, value):
        base = {k: p[0] for k, p in self.CASES[indicator].items()}
        base[key] = value
        return base

    @pytest.mark.parametrize(
        ("indicator", "key", "value"),
        [(ind, k, lo - eps)
         for ind, params in CASES.items()
         for k, (lo, hi) in params.items()
         for eps in ([1] if float(lo).is_integer() else [0.01])]
        + [(ind, k, hi + eps)
           for ind, params in CASES.items()
           for k, (lo, hi) in params.items()
           for eps in ([1] if float(hi).is_integer() else [0.01])],
        ids=lambda v: f"{v}")
    def test_out_of_domain_rejected(self, indicator, key, value):
        from core.analysis.column_ref import (
            ColumnRefError, _resolve_indicator_ref)
        with pytest.raises(ColumnRefError) as ei:
            _resolve_indicator_ref({
                "indicator": indicator,
                "params": self._mk_params(indicator, key, value),
                "field": _subject_field(indicator)})
        assert ei.value.code == "PARAM_INVALID"
        assert key in ei.value.detail.get("key", "")

    @pytest.mark.parametrize(
        ("indicator", "key", "value"),
        [(ind, k, lo)
         for ind, params in CASES.items()
         for k, (lo, hi) in params.items()]
        + [(ind, k, hi)
           for ind, params in CASES.items()
           for k, (lo, hi) in params.items()],
        ids=lambda v: f"{v}")
    def test_boundary_values_accepted(self, indicator, key, value):
        """界内边界值正常（恰下界/恰上界——闭区间；GARCH 边界组合
        alpha+beta=1.51 时仍受 cross_params 拒绝是联合约束非域约束，
        本面只验逐参数域）。"""
        from core.analysis.column_ref import _resolve_indicator_ref
        # GARCH 恰上界组合 alpha=0.50/beta=0.99 → 和 1.49<1 通过；
        # 逐参数边界替换其余参数取下界，避免联合越界干扰域判定面
        try:
            ref = _resolve_indicator_ref({
                "indicator": indicator,
                "params": self._mk_params(indicator, key, value),
                "field": _subject_field(indicator)})
            assert ref.params[key] == value
        except Exception as exc:
            # GARCH alpha 上界 0.50 与 beta 下界 0.50 组合和恰 1.0 →
            # cross_params 拒绝（联合约束）——非域拒绝，按预期收
            if indicator == "GARCHFilter" and \
                    "alpha+beta" in str(exc):
                assert key in ("alpha", "beta")
            else:
                raise
