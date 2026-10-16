"""结构类指标测试（add-session-structure-expression 任务 3/4/5）。

U3（SessionRange 四不变量+vwap 核算+往返）/U4（Pivot 确认延迟三锚）/
U5（Supertrend 文献公式核算+dir 二值）——期望全部人工核算构造
（合成已知序列），不以生产算法自证。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicators import _compute_indicator
from core.analysis.column_ref import (
    ColumnRefError, decode_column_ref, encode_column_ref, _resolve_indicator_ref,
)


def _df_from_rows(rows, freq="1h", start="2024-01-01"):
    """rows: [(hour, minute, high, low, close, volume)] → df。

    时间戳按行序递进 1 小时（日期自然推进——跨日场景不重复索引）；
    hour/minute 仅作语义标注（SessionRange 按 index 的实际时刻判定）。
    """
    t0 = pd.Timestamp(f"{start} 00:00:00", tz="UTC")
    idx = pd.DatetimeIndex(
        [t0 + pd.Timedelta(hours=i) for i in range(len(rows))],
        name="timestamp")
    return pd.DataFrame({
        "open": [r[4] for r in rows], "high": [r[2] for r in rows],
        "low": [r[3] for r in rows], "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows], "trades": 10,
    }, index=idx)


class TestSessionRange:
    """U3：SessionRange 四不变量+核算+往返。"""

    def _session_df(self):
        # 两天各 0:00-7:00 会话（0-420 分钟）——日1 全天 24 根
        # （会话 7 根 + 会话外 17 根）后接日2 会话 7 根
        rows = []
        # 日1 会话：high 递增 10..16, low 1..7, close=high-2, vol=1
        for h in range(7):
            rows.append((h, 0, 10 + h, 1 + h, 8 + h, 1))
        # 日1 会话外 hour 7-23（大波 100/90 不进会话列）
        for h in range(7, 24):
            rows.append((h, 0, 100, 90, 95, 100))
        # 日2 会话 hour 0-6（index 24-30）
        for h in range(7):
            rows.append((h, 0, 20 + h, 11 + h, 18 + h, 2))
        return _df_from_rows(rows)

    def test_u3a_four_invariants(self):
        """X1=U3a 四不变量：t 时刻输出只含截至 t-1 的会话数据；窗口
        结束后终值保持至 UTC 日界（D-3 裁决=方向 B：design 2.3 原文
        「窗口结束后输出固定值至日终」）；跨会话日界重置；窗口首根
        NaN。合成已知序列逐 bar 人工核算。"""
        df = self._session_df()
        out = _compute_indicator(df, "SessionRange",
                                 {"start_minute": 0, "end_minute": 420})
        sh = out["session_high_0_420"]
        # 窗口首根（t-1 无会话数据）→ NaN
        assert np.isnan(sh.iloc[0])
        # 窗口内逐 bar：可见上一根 high（进行中聚合不含当根）
        assert sh.iloc[1] == 10
        assert sh.iloc[6] == 15
        # 会话外：终值保持至日终（窗口最终聚合含末根 h6 的 16；
        # 会话外大波 100/90 不进会话列）
        assert sh.iloc[7] == 16
        assert sh.iloc[8] == 16 and sh.iloc[23] == 16, "终值保持至日终"
        # UTC 日界重置：日2 首根 NaN（新窗口 t-1 无数据）
        assert np.isnan(sh.iloc[24])
        # 日2 窗口内逐 bar 更新
        assert sh.iloc[25] == 20
        sl = out["session_low_0_420"]
        assert np.isnan(sl.iloc[0]) and sl.iloc[1] == 1
        assert sl.iloc[8] == 1 and sl.iloc[23] == 1, "low 终值同样保持"

    def test_u3a2_nonzero_start_boundary(self):
        """X1 补（D-3 方向 B 边界锚）：start_minute>0 时「窗口开始前
        NaN」与「窗口结束后终值保持」两段并存且互不误伤——日界后、
        窗口开始前段必须 NaN（保持至日终 ≠ 跨日延续）。"""
        df = self._session_df()
        out = _compute_indicator(df, "SessionRange",
                                 {"start_minute": 120, "end_minute": 300})
        sh = out["session_high_120_300"]
        # 日1 窗口开始前（h0-h1）与窗口首根（h2）NaN
        assert np.isnan(sh.iloc[0]) and np.isnan(sh.iloc[1]) \
            and np.isnan(sh.iloc[2])
        # 窗口内进行中累积（不含当根）：日1 h2/h3/h4 high=12/13/14
        assert sh.iloc[3] == 12 and sh.iloc[4] == 13
        # 会话外终值保持（含 h4 聚合=14）至日终
        assert sh.iloc[5] == 14 and sh.iloc[6] == 14 and sh.iloc[23] == 14
        # 关键：日2 窗口开始前（h0-h1）NaN——昨日终值 14 不得跨日延续
        assert np.isnan(sh.iloc[24]) and np.isnan(sh.iloc[25]), \
            "日界后窗口开始前段须 NaN（保持≠跨日延续）"
        # 日2 新窗口：首根 NaN→累积→保持（h2/h3/h4 high=22/23/24）
        assert np.isnan(sh.iloc[26])
        assert sh.iloc[27] == 22 and sh.iloc[28] == 23
        assert sh.iloc[29] == 24, "日2 终值（h2-h4 max=24）自 h5 保持"

    def test_u3b_vwap_manual(self):
        """U3b vwap 人工核算：日1 hour2 处 vwap = Σ(typ×vol)/Σvol
        （hour0/1 两根，vol=1）= mean((10+1+8)/3, (11+2+9)/3)。"""
        df = self._session_df()
        out = _compute_indicator(df, "SessionRange",
                                 {"start_minute": 0, "end_minute": 420})
        vwap = out["session_vwap_0_420"]
        typ0, typ1 = (10 + 1 + 8) / 3, (11 + 2 + 9) / 3
        expect = (typ0 * 1 + typ1 * 1) / 2
        assert vwap.iloc[2] == pytest.approx(expect)

    def test_u3c_roundtrip(self):
        """U3c 列名往返 session_high_0_420（encode/decode 对偶）。"""
        col = encode_column_ref("SessionRange",
                                {"start_minute": 0, "end_minute": 420},
                                "session_high")
        assert col == "session_high_0_420"
        assert decode_column_ref(col) == {
            "indicator": "SessionRange",
            "params": {"start_minute": 0, "end_minute": 420},
            "field": "session_high"}

    def test_u3d_defaults_and_u1a_cross(self):
        """U3d 默认参数两态（0/420 放行）+ 时段倒序拒（钩子实指标）。"""
        ok = _resolve_indicator_ref({
            "indicator": "SessionRange",
            "params": {"start_minute": 0, "end_minute": 420},
            "field": "session_high"})
        assert ok.params["end_minute"] == 420
        with pytest.raises(ColumnRefError) as ei:
            _resolve_indicator_ref({
                "indicator": "SessionRange",
                "params": {"start_minute": 500, "end_minute": 100},
                "field": "session_high"})
        assert ei.value.code == "PARAM_INVALID"

    def test_u3e_six_actions_available(self):
        """U3e 六动作枚举可用（三列 × gt/lt/ge/le/cross_above/cross_below）。"""
        from core.features.registry import INDICATOR_REGISTRY
        sc = INDICATOR_REGISTRY["SessionRange"].supported_conditions
        assert set(sc) >= {"gt", "lt", "ge", "le", "cross_above",
                           "cross_below"}


class TestPivot:
    """U4：Pivot 确认延迟。"""

    def _pivot_df(self):
        # 已知序列：index3 为明确峰（前 2 后 2 严格最高）；index8 为谷
        highs = [10, 11, 12, 20, 12, 11, 10, 5, 4, 5, 6, 7, 8]
        rows = [(i % 24, 0, h, h - 3, h - 1, 1)
                for i, h in enumerate(highs)]
        return _df_from_rows(rows)

    def test_u4a_confirmation_delay(self):
        """X2=U4a 确认延迟：峰在 j=3（left=2/right=2），确认落位
        j+right=5；t<5 处 NaN（首确认前不可见）；t=5 处值=20；
        t≥5 ffill 前值 20 延续。"""
        df = self._pivot_df()
        out = _compute_indicator(df, "Pivot", {"left": 2, "right": 2})
        ph = out["pivot_high_2_2"]
        for t in range(5):
            assert np.isnan(ph.iloc[t]), f"t={t} 确认前不可见"
        assert ph.iloc[5] == 20, "确认时刻 j+right 落位"
        assert ph.iloc[6] == 20 and ph.iloc[-1] == 20, "ffill 前值延续"

    def test_u4b_pivot_low_symmetric(self):
        """U4b 谷对称：谷 j=8，确认 t=10 值 4。"""
        df = self._pivot_df()
        out = _compute_indicator(df, "Pivot", {"left": 2, "right": 2})
        pl = out["pivot_low_2_2"]
        assert np.isnan(pl.iloc[9])
        # lows = high-3：谷 j=8 的 low = 4-3 = 1
        assert pl.iloc[10] == 1

    def test_u4c_param_domain_and_zero_rejected(self):
        """U4c 参数域：left/right min=1（0 拒）；确认根数 0 无意义拒。"""
        with pytest.raises(ColumnRefError):
            _resolve_indicator_ref({
                "indicator": "Pivot",
                "params": {"left": 0, "right": 2}, "field": "pivot_high"})
        with pytest.raises(ColumnRefError):
            _resolve_indicator_ref({
                "indicator": "Pivot",
                "params": {"left": 2, "right": 101},
                "field": "pivot_high"})

    def test_u4d_warmup_sum_anchor(self, tmp_path):
        """U4d warmup=left+right 求和锚（周期类参数 ×3 主式）。"""
        from core.analysis.registry_meta import derive_warmup_full
        w = derive_warmup_full(tmp_path / "q.db", ["Pivot"])
        # Pivot 无基因级周期 → 主式：lookback=left_max+right_max=200+200=400
        # ×3=1200？——注册表 max（保守）：100+100=200×3=600 vs floor 200 → 600
        assert w == max(200, (100 + 100) * 3), f"Pivot 主式 warmup: {w}"

    def test_u4e_roundtrip(self):
        """U4e 列名往返 pivot_high_5_5。"""
        col = encode_column_ref("Pivot", {"left": 5, "right": 5},
                                "pivot_high")
        assert col == "pivot_high_5_5"
        assert decode_column_ref(col) == {
            "indicator": "Pivot", "params": {"left": 5, "right": 5},
            "field": "pivot_high"}


class TestSupertrend:
    """U5：Supertrend 标准公式。"""

    def test_u5a_manual_small_sample(self):
        """U5a 文献标准公式人工核算小样本（F1 重写——可确立方向构造
        +带值锚）。

        构造与手算：前 12 根 O=H=L=C=100（TR=0 → Wilder ATR(min_periods=
        10)=0 → 上下轨=100）；第 13 根（i=12）H=110/L=100/C=110：hl2=105，
        TR=10，ATR[12]=0.9×0+0.1×10=1.0，上轨=105+3=108、下轨=105−3=102；
        close[11]=100 ≮ final_upper[11]=100 → final_upper[12]=108，close
        110>108 上穿确立 → dir[12]=+1，supertrend[12]=final_lower[12]=
        下轨 102（close[11]=100 不> final_lower[11]=100 → 不延续取新）。

        已知边界披露：dir 三态——0 为未定态（首翻转前），未定态下
        supertrend 列出 NaN（无可引用带值）；常数行情（零穿越事件）
        全程未定：st 全 NaN、dir 恒 0（原版断言 dropna().all() 空集
        恒真零检出力，故重写）。
        """
        rows = [(i % 24, 0, 100, 100, 100, 1) for i in range(12)]
        rows.append((12, 0, 110, 100, 110, 1))
        rows += [(13 + i, 0, 110, 110, 110, 1) for i in range(17)]
        df = _df_from_rows(rows)
        out = _compute_indicator(df, "Supertrend",
                                 {"atr_period": 10, "multiplier": 3.0})
        st, d = out["supertrend_10_3.0"], out["supertrend_dir_10_3.0"]
        # 带值锚：确立 bar 的 supertrend=下轨 102、dir=+1（手算链见
        # docstring）
        assert st.iloc[12] == 102.0, f"确立 bar 带值锚 102，got {st.iloc[12]}"
        assert d.iloc[12] == 1.0
        # 未定态：确立前 dir=0 且 st=NaN（未定不出值——非收敛缺陷）
        assert (d.iloc[:12] == 0).all()
        assert st.iloc[:12].isna().all()
        # 确立后延续多头：dir 恒 +1、st 取下轨（后续 ATR 收敛带上移，
        # 单调不减）
        assert (d.iloc[13:] == 1.0).all()
        later = st.iloc[13:].dropna()
        assert (later.diff().fillna(0) >= 0).all()

    def test_u5a2_flat_market_undetermined_state(self):
        """U5a2 常数行情未定态锚（F1 补——零翻转事件的契约行为）：
        dir 恒 0（未定）、st 全 NaN（未定态不出值——与 u5b 的翻转
        行情互补，锁「未定≠收敛缺陷」语义）。"""
        rows = [(i % 24, 0, 100, 100, 100, 1) for i in range(30)]
        df = _df_from_rows(rows)
        out = _compute_indicator(df, "Supertrend",
                                 {"atr_period": 10, "multiplier": 3.0})
        st, d = out["supertrend_10_3.0"], out["supertrend_dir_10_3.0"]
        assert (d == 0).all(), "常数行情零穿越 → dir 恒未定态 0"
        assert st.isna().all(), "未定态 supertrend 不出值"

    def test_u5b_dir_binary_and_flip(self):
        """U5b dir 二值 ±1 + 翻转=cross 0 表达锚：构造先跌后涨行情
        产生至少一次翻转。"""
        # 30 根下跌后 30 根上涨
        highs = list(np.linspace(100, 60, 30)) + list(np.linspace(60, 120, 30))
        rows = [(i % 24, 0, h, h - 5, h - 2, 1) for i, h in enumerate(highs)]
        df = _df_from_rows(rows)
        out = _compute_indicator(df, "Supertrend",
                                 {"atr_period": 10, "multiplier": 3.0})
        d = out["supertrend_dir_10_3.0"]
        vals = set(d.dropna().unique())
        assert vals <= {-1.0, 0.0, 1.0}, f"dir 二值：{vals}"
        flips = int((d.diff().fillna(0) != 0).sum())
        assert flips >= 1, "先跌后涨至少一次翻转"
        # 翻转=符号变化点（初始化 0→±1 非翻转；两类计数差恰为初始化）
        sign_changes = int(((d > 0) != (d.shift(1) > 0)).sum())
        assert sign_changes >= 1
        assert flips - sign_changes <= 1, "diff 计数至多多一次初始化跳变"

    def test_u5c_no_lookahead(self):
        """U5c 防前视回归：t 行 close 改值不影响 t-1 及更早的输出。"""
        rows = [(i % 24, 0, 100 + i, 95 + i, 98 + i, 1) for i in range(40)]
        df = _df_from_rows(rows)
        a = _compute_indicator(df.copy(), "Supertrend",
                               {"atr_period": 10, "multiplier": 3.0})
        df.iloc[39, df.columns.get_loc("close")] = 999  # 改末行
        b = _compute_indicator(df, "Supertrend",
                               {"atr_period": 10, "multiplier": 3.0})
        pd.testing.assert_series_equal(
            a["supertrend_10_3.0"].iloc[:39],
            b["supertrend_10_3.0"].iloc[:39], check_names=False)

    def test_u5d_domain_rejected(self):
        """U5d 参数域越界拒（multiplier 5.5 / atr_period 3）。"""
        for bad in ({"atr_period": 3, "multiplier": 3.0},
                    {"atr_period": 10, "multiplier": 5.5}):
            with pytest.raises(ColumnRefError):
                _resolve_indicator_ref({
                    "indicator": "Supertrend", "params": bad,
                    "field": "supertrend"})

    def test_u5e_float_roundtrip(self):
        """U5e float 编码往返（kc 同款 3.0 形）。"""
        for field in ("supertrend", "supertrend_dir"):
            col = encode_column_ref(
                "Supertrend", {"atr_period": 10, "multiplier": 3.0}, field)
            assert decode_column_ref(col) == {
                "indicator": "Supertrend",
                "params": {"atr_period": 10, "multiplier": 3.0},
                "field": field}
