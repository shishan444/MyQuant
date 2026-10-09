"""validation engine 条件求值边界锚（质量阶段 F-2 回归）。

F-2（30818ba）：弱类型入口（run_analysis 的 condition 为任意 dict）
下 SNR 动作数值键坏值经 _cond_number 结构化拒绝
（RESEARCH_PARAM_INVALID，经既有 research_error_to_http 转 422），
替代裸 float()/int() 穿 500；cast 保留原类型语义（int 对 '10.5'
拒绝不隐式截断）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.validation.engine import _evaluate_single_condition
from tests.helpers.data_factory import make_ohlcv


@pytest.fixture()
def df() -> pd.DataFrame:
    return make_ohlcv(n=60, freq="1h", start="2024-01-01", seed=3)


class TestSnrNumericKeyBoundaries:
    """SNR 动作（touch_bounce/role_reversal）数值键坏值四形与好值对照。"""

    @pytest.mark.parametrize("bad", ["abc", None, {"a": 1}],
                             ids=["非数值串", "None", "dict"])
    def test_touch_bounce_proximity坏值_结构化拒绝(self, df, bad):
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(df, {
                "subject": "close", "action": "touch_bounce",
                "target": 0, "proximity_pct": bad})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        detail = ei.value.detail or {}
        assert detail["param"] == "condition.proximity_pct"

    def test_role_reversal_lookback截断形_int语义不隐式截断(self, df):
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(df, {
                "subject": "close", "action": "role_reversal",
                "target": 0, "lookback": "10.5"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["param"] == "condition.lookback"

    def test_好值字符串数值_照常求值(self, df):
        out = _evaluate_single_condition(df, {
            "subject": "close", "action": "touch_bounce",
            "target": 0, "proximity_pct": "0.01"})
        assert isinstance(out, pd.Series) and out.dtype == bool

    def test_缺省数值键_走默认照常求值(self, df):
        out = _evaluate_single_condition(df, {
            "subject": "close", "action": "touch_bounce", "target": 0})
        assert isinstance(out, pd.Series) and out.dtype == bool


class TestPatternActionBoundaries:
    """pattern 族动作的 target 坏值兜底（count 回落 3 不炸）。

    _resolve_pattern 的 subject 列缺失分支（engine.py:485-486）与未知
    pattern 兜底（:509）不可达判定：subject_col 实参恒为
    ``s.name``（经 _resolve_subject 从 df 列解析，name 恒等于列名，
    engine.py:374-377、:408、:414-425 三条解析路径均返回 df[col]）——
    ``subject_col not in df.columns`` 恒假；pattern 实参来自 action_map
    硬编码四值（:374-377），无未知值入口。两分支仅防御 patterns 模块
    契约漂移（回归错误形态），无法以合法输入构造。
    """

    def test_consecutive_up_target列名形_默认count3与显式3一致(self, df):
        """target 为列名（经 _resolve_target 解析为 Series）→
        int(float(Series)) TypeError → count 回落默认 3（与显式 3 一致）。"""
        bad = _evaluate_single_condition(df, {
            "subject": "close", "action": "consecutive_up",
            "target": "close"})
        explicit = _evaluate_single_condition(df, {
            "subject": "close", "action": "consecutive_up",
            "target": 3})
        assert isinstance(bad, pd.Series) and bad.dtype == bool
        assert int(bad.sum()) > 0  # 显式 3 形确有产出（非退化空掩码）
        pd.testing.assert_series_equal(bad, explicit)

    def test_consecutive_down_target列名形_默认count3与显式3一致(self, df):
        bad = _evaluate_single_condition(df, {
            "subject": "close", "action": "consecutive_down",
            "target": "close"})
        explicit = _evaluate_single_condition(df, {
            "subject": "close", "action": "consecutive_down",
            "target": 3})
        assert isinstance(bad, pd.Series) and bad.dtype == bool
        pd.testing.assert_series_equal(bad, explicit)


class TestLookbackTargetBoundary:
    """0.3.7 TD-11：lookback 内层 target/inner_action 坏值结构化拒绝。

    原锚（F-2 期）：target 列名形坏 str → threshold=0 静默兜底——该
    行为正是 TD-11 裁决消灭的静默变形（坏值变 threshold=0 的失真
    比较），锚随规格改写为拒绝语义。"""

    def test_target坏str_结构化拒绝不归零(self, df):
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(df, {
                "subject": "close", "action": "lookback_any",
                "inner_action": "gt", "target": "close", "window": 3})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["param"] == "condition.target"
        assert (ei.value.detail or {})["value"] == "close"

    def test_inner_action非法值_结构化拒绝不缺省(self, df):
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(df, {
                "subject": "close", "action": "lookback_any",
                "inner_action": "cross_above", "target": 100.0,
                "window": 3})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["legal_values"] == [
            "ge", "gt", "le", "lt", "price_above"]

    def test_inner_action合法集对拍_防分支漂移(self):
        # 枚举对拍锚：期望硬编 vs 实现 frozenset（求值分支增删时此锚红）
        from core.validation.engine import _LOOKBACK_INNER_ACTIONS
        assert set(_LOOKBACK_INNER_ACTIONS) == {
            "gt", "lt", "ge", "le", "price_above"}

    def test_缺省inner_action_price_above_既有契约保持(self, df):
        # 红线①：缺省是既有契约——不传 inner_action 仍按 price_above
        # （close > close 逐位 False → any 窗口仍全 False）
        out = _evaluate_single_condition(df, {
            "subject": "close", "action": "lookback_any",
            "target": 0, "window": 3})
        assert isinstance(out, pd.Series) and out.dtype == bool
        assert not bool(out.iloc[3:].any())


class TestSpikeShrinkBoundaries:
    """spike/shrink 的 target 坏值（非列名 str）→ _resolve_target 归一
    None → 单条件层提前全 False 降级（不炸不 500）。

    _spike_condition/_shrink_condition 的 except 乘数兜底
    （engine.py:721-722/:731-732）不可达判定：实参 target 五类枚举——
    ① 坏 str 经 _resolve_target（:445-448 float 失败、:463 列未命中）
    归一 None，在 :356-359 提前拦截到不了动作；② 好 str 变 float 不抛；
    ③ int/float 直转不抛；④ 列名变 Series 走 isinstance 三元 else；
    ⑤ None 同①拦截——except 分支无输入路径，仅防御动作直调方违约。
    """

    def test_spike_target坏str_全False降级(self, df):
        out = _evaluate_single_condition(df, {
            "subject": "volume", "action": "spike", "target": "abc"})
        assert isinstance(out, pd.Series) and out.dtype == bool
        assert not out.any()

    def test_shrink_target坏str_全False降级(self, df):
        out = _evaluate_single_condition(df, {
            "subject": "volume", "action": "shrink", "target": "abc"})
        assert isinstance(out, pd.Series) and out.dtype == bool
        assert not out.any()

    def test_spike_shrink_显式乘数_正常产出(self, df):
        """好值对照：同条件显式乘数下确有非退化掩码（防上方降级断言
        空转——恒 False 实现也会通过 not any）。"""
        spike = _evaluate_single_condition(df, {
            "subject": "volume", "action": "spike", "target": 2.0})
        shrink = _evaluate_single_condition(df, {
            "subject": "volume", "action": "shrink", "target": 0.5})
        assert int(spike.sum()) > 0
        assert int(shrink.sum()) > 0


class TestCrossSeriesTargetUnresolvable:
    """cross_above_series 跨周期 target 列缺失 → 全 False 降级
    （_resolve_target 的「tf:indicator」后缀未命中→精确未命中→None 链）。"""

    def test_cross_above_series_跨周期列缺失_全False(self, df):
        out = _evaluate_single_condition(df, {
            "subject": "close", "action": "cross_above_series",
            "target": "4h:nope_col"})
        assert len(out) == len(df)
        assert not out.any()


class TestEvaluateConditionsEmptyFallbackUnreachable:
    """_evaluate_conditions 末尾兜底（engine.py:337）不可达判定。

    调用图依据：空 conditions 列表在 :315-316 已短路返回；非空列表经
    :327-328 首轮 ``result = mask`` 必赋值，:334 ``if result is not None``
    恒真先行返回——:337 不可达（防御分支，无合法输入路径）。
    """


class TestValidateHypothesisStatsBoundaries:
    """validate_hypothesis 统计装配的边界侧：区间参数缺省、零触发、
    同日数据（days_covered=0）——统计兜底字段空形契约。"""

    SYMBOL = "BTCUSDT"

    @staticmethod
    def _write_parquet(d, n=300, freq="4h", days_span=None):
        import numpy as np

        d.mkdir(parents=True, exist_ok=True)
        if days_span is None:
            idx = pd.date_range("2023-06-01", periods=n, freq=freq, tz="UTC",
                                name="timestamp")
        else:
            # 同日数据：全部 bar 落在同一自然日内（days_covered=0）
            idx = pd.date_range(days_span, periods=n, freq=freq, tz="UTC",
                                name="timestamp")
        rng = np.random.default_rng(11)
        close = 60000 + np.cumsum(rng.normal(0, 100, n))
        pd.DataFrame({"open": close, "high": close + 50, "low": close - 50,
                      "close": close, "volume": rng.uniform(100, 800, n),
                      "trades": 40}, index=idx).to_parquet(
            d / f"BTCUSDT_4h.parquet")

    def test_不带start_end_全量载入照常验证(self, tmp_path):
        """start/end 缺省（None）→ 跳过区间过滤直用全量（引擎统计链
        区间过滤分支的缺省侧）。"""
        from core.validation.engine import validate_hypothesis

        d = tmp_path / "mkt"
        self._write_parquet(d)
        res = validate_hypothesis(
            self.SYMBOL, "4h", None, None,
            when_conditions=[{"subject": "close", "action": "gt",
                              "target": 0}],
            then_conditions=[], then_window=4, data_dir=str(d))
        assert res.total_count > 0

    def test_零触发_统计兜底字段空形(self, tmp_path):
        """WHEN 恒假（gt 天文阈值）→ 零触发 → percentiles/concentration/
        extremes/signal_frequency 均空形（changes 空与 total 0 双兜底）。"""
        from core.validation.engine import validate_hypothesis

        d = tmp_path / "mkt"
        self._write_parquet(d)
        res = validate_hypothesis(
            self.SYMBOL, "4h", None, None,
            when_conditions=[{"subject": "close", "action": "gt",
                              "target": 1e12}],
            then_conditions=[], then_window=4, data_dir=str(d))
        assert res.total_count == 0
        assert res.match_rate == 0
        assert res.percentiles == {}
        assert res.concentration == {}
        assert res.extremes == []
        assert res.signal_frequency == {}

    def test_同日数据_days_covered为零_频率空形(self, tmp_path):
        """全部 bar 同日（days_covered=0）且确有触发 → 频率统计跳过
        （分母护栏），其余统计照常产出。"""
        from core.validation.engine import validate_hypothesis

        d = tmp_path / "mkt"
        self._write_parquet(d, n=6, days_span="2024-01-01")
        res = validate_hypothesis(
            self.SYMBOL, "4h", None, None,
            when_conditions=[{"subject": "close", "action": "gt",
                              "target": 0}],
            then_conditions=[], then_window=1, data_dir=str(d))
        assert res.total_count > 0
        assert res.signal_frequency == {}
        assert res.percentiles != {}


class TestEngineResolveHelperBoundaries:
    """engine 解析/求值辅助函数的边界侧锚（解析失败降级、缺省兜底、
    未知类型 False 形、坏值缺省回退——不炸不误报）。"""

    def test_resolve_target_无冒号字符串_精确列名命中(self, df):
        """无 ':' 的字符串 target 直奔精确列名分支（:463）。"""
        from core.validation.engine import _resolve_target
        out = _resolve_target(df, "close")
        pd.testing.assert_series_equal(out, df["close"])

    def test_resolve_pattern_subject列缺失_全False降级(self, df):
        """形态求值 subject 列不在 df → 全 False（卡3c 精确语义）。"""
        from core.validation.engine import _resolve_pattern
        out = _resolve_pattern(df, "consecutive_up", "no_such_col")
        assert len(out) == len(df)
        assert not out.any()

    def test_resolve_pattern_consecutive_target缺省_count3默认(self, df):
        """consecutive_up/down 的 target=None → count=3 缺省，
        与显式 3 逐位一致（缺省形契约）。"""
        from core.validation.engine import _resolve_pattern
        up_none = _resolve_pattern(df, "consecutive_up", "close", None)
        up_3 = _resolve_pattern(df, "consecutive_up", "close", 3)
        pd.testing.assert_series_equal(up_none.astype(bool), up_3.astype(bool))
        down_none = _resolve_pattern(df, "consecutive_down", "close", None)
        down_3 = _resolve_pattern(df, "consecutive_down", "close", 3)
        pd.testing.assert_series_equal(down_none.astype(bool), down_3.astype(bool))

    def test_resolve_pattern_未知形态_全False(self, df):
        from core.validation.engine import _resolve_pattern
        out = _resolve_pattern(df, "nonsense_pattern", "close")
        assert not out.any()

    def test_resolve_snr_pattern_subject列缺失_全False降级(self, df):
        from core.validation.engine import _resolve_snr_pattern
        out = _resolve_snr_pattern(df, "touch_bounce", "no_such_col", {})
        assert len(out) == len(df)
        assert not out.any()

    def test_resolve_snr_pattern_未知形态_全False(self, df):
        from core.validation.engine import _resolve_snr_pattern
        out = _resolve_snr_pattern(df, "nonsense_pattern", "close", {})
        assert not out.any()

    def test_spike_坏str乘数_float失败回退缺省2(self, df):
        """_spike_condition 直调坏 str target（引擎链路中会被
        _resolve_target 拦为 None，此处锚函数自身契约：float 失败 →
        2.0 缺省，与显式 2.0 逐位一致）。"""
        from core.validation.engine import _spike_condition
        out = _spike_condition(df["volume"], df, "garbage_str")
        base = _spike_condition(df["volume"], df, 2.0)
        pd.testing.assert_series_equal(out, base)

    def test_shrink_坏str乘数_float失败回退缺省05(self, df):
        from core.validation.engine import _shrink_condition
        out = _shrink_condition(df["volume"], df, "garbage_str")
        base = _shrink_condition(df["volume"], df, 0.5)
        pd.testing.assert_series_equal(out, base)

    def test_lookback_condition_mode_all_全窗达成形(self, df):
        """mode='all' 分支（引擎 action_map 仅注册 lookback_any，
        all 模式为 THEN 侧共享形制——函数级特征锚）。"""
        from core.validation.engine import _lookback_condition
        cond = {"inner_action": "gt", "target": 0, "window": 2}
        out = _lookback_condition(df, cond, df["close"], "all")
        assert len(out) == len(df)
        assert out.dtype == bool

    def test_single_condition_未知action且warnings为None_全False不炸(self, df):
        """未知 action + warnings=None：跳过 warning 记录直接
        全 False（warnings 可选形制）。"""
        out = _evaluate_single_condition(
            df, {"subject": "close", "action": "nope_action", "target": 1},
            warnings=None)
        assert not out.any()

    def test_then_conditions_pattern类action_循环空转兜底False(self, df):
        """THEN 条件为 pattern 类 action（非 drop/lt/le/rise/gt/ge 系）
        → 主核验循环空转、兜底段非 drop/rise → False。"""
        from core.validation.engine import _check_then_conditions
        window = df.iloc[1:9]
        out = _check_then_conditions(
            window, [{"action": "consecutive_up", "target": 3}],
            trigger_price=float(df["close"].iloc[0]))
        assert out is False


class TestStatsAssemblyEmptyChangesUnreachable:
    """validate_hypothesis 统计段空集合分支不可达判定
    （:246/:260/:269/:281 的 False 侧，即 changes/total 为空时跳过
    percentiles/concentration/signal_frequency/extremes）。

    调用图依据：changes 与 triggers 在逐触发循环内同步 append
    （:224/:232）；循环出口 :234-235「not triggers → 提前 return」，
    :181-182「零触发提前 return」，:117/:121/:130 数据缺失/区间空
    提前 return——控制流到达 :246 时 triggers 必非空，故 changes
    必非空、total_count = len(triggers) >= 1（:238）且 len(df) > 0
    （:120 已拦）。四个 False 侧分支均无合法输入路径（防御冗余）。
    """


class TestResolveTargetSplitGuardUnreachable:
    """_resolve_target 跨周期 split 长度守卫（:453 False 侧 → :463）
    不可达判定。

    调用图依据：:451 ``if ":" in target:`` 已保证含分隔符；
    ``target.split(":", 1)``（:452）对含 sep 的字符串恒返回 2 段
    （maxsplit=1 下 len 只能为 2）——:453 ``len(parts) == 2`` 恒真，
    False 侧无输入路径（防御分支）。
    """


class TestThenTargetBoundary:
    """0.3.7 TD-11 THEN 侧同规：坏 target 与 WHEN 侧同一拒绝语义。"""

    def test_空条件集_求值恒False(self, df):
        """阶段四盲区消解：_evaluate_conditions 空 conditions 头部
        早退（:323）→ 全 False 序列。尾部同名 return（:345）不可达：
        conditions 非空时循环至少一次、result 必被赋值（result is
        None → result = mask），循环出口 result is not None 恒真
        ——防御冗余，无输入路径。"""
        from core.validation.engine import _evaluate_conditions
        out = _evaluate_conditions(df, [], None)
        assert isinstance(out, pd.Series)
        assert len(out) == len(df)
        assert not out.any()

    def test_then_target坏str_同构拒绝(self, df):
        from core.validation.engine import _check_then_conditions
        with pytest.raises(ResearchError) as ei:
            _check_then_conditions(df, [
                {"action": "drop", "target": "close", "window": 4}],
                trigger_price=100.0)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["param"] == "condition.target"

    def test_then_target缺省0_既有契约保持(self, df):
        from core.validation.engine import _check_then_conditions
        # 缺省 0：drop change <= 0 触发（跌即匹配）
        out = _check_then_conditions(df, [{"action": "drop"}],
                                     trigger_price=10**9)
        assert isinstance(out, bool)

    def test_then_target坏str_window0截空_兜底块应同构拒绝(self, df):
        """CARD-02 转正：window=0 经 _cond_window 在循环体结构化拒绝
        （F-2 同码族，param=condition.window 先于 target 校验触发），
        不再静默截空落兜底块。REST 入站侧同步 ge=1 早拒。"""
        from core.validation.engine import _check_then_conditions
        with pytest.raises(ResearchError) as ei:
            _check_then_conditions(df, [
                {"action": "drop", "target": "close", "window": 0}],
                trigger_price=100.0)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["param"] == "condition.window"
