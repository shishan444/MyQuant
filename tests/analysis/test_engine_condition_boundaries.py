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

from core.analysis.errors import ResearchError
from core.analysis.condition_eval import _evaluate_single_condition
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
        from core.analysis.condition_eval import _LOOKBACK_INNER_ACTIONS
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


class TestEngineResolveHelperBoundaries:
    """engine 解析/求值辅助函数的边界侧锚（解析失败降级、缺省兜底、
    未知类型 False 形、坏值缺省回退——不炸不误报）。"""

    def test_resolve_target_无冒号字符串_精确列名命中(self, df):
        """无 ':' 的字符串 target 直奔精确列名分支（:463）。"""
        from core.analysis.condition_eval import _resolve_target
        out = _resolve_target(df, "close")
        pd.testing.assert_series_equal(out, df["close"])

    def test_resolve_pattern_subject列缺失_全False降级(self, df):
        """形态求值 subject 列不在 df → 全 False（卡3c 精确语义）。"""
        from core.analysis.condition_eval import _resolve_pattern
        out = _resolve_pattern(df, "consecutive_up", "no_such_col")
        assert len(out) == len(df)
        assert not out.any()

    def test_resolve_pattern_consecutive_target缺省_count3默认(self, df):
        """consecutive_up/down 的 target=None → count=3 缺省，
        与显式 3 逐位一致（缺省形契约）。"""
        from core.analysis.condition_eval import _resolve_pattern
        up_none = _resolve_pattern(df, "consecutive_up", "close", None)
        up_3 = _resolve_pattern(df, "consecutive_up", "close", 3)
        pd.testing.assert_series_equal(up_none.astype(bool), up_3.astype(bool))
        down_none = _resolve_pattern(df, "consecutive_down", "close", None)
        down_3 = _resolve_pattern(df, "consecutive_down", "close", 3)
        pd.testing.assert_series_equal(down_none.astype(bool), down_3.astype(bool))

    def test_resolve_pattern_未知形态_全False(self, df):
        from core.analysis.condition_eval import _resolve_pattern
        out = _resolve_pattern(df, "nonsense_pattern", "close")
        assert not out.any()

    def test_resolve_snr_pattern_subject列缺失_全False降级(self, df):
        from core.analysis.condition_eval import _resolve_snr_pattern
        out = _resolve_snr_pattern(df, "touch_bounce", "no_such_col", {})
        assert len(out) == len(df)
        assert not out.any()

    def test_resolve_snr_pattern_未知形态_全False(self, df):
        from core.analysis.condition_eval import _resolve_snr_pattern
        out = _resolve_snr_pattern(df, "nonsense_pattern", "close", {})
        assert not out.any()

    def test_spike_坏str乘数_float失败回退缺省2(self, df):
        """_spike_condition 直调坏 str target（引擎链路中会被
        _resolve_target 拦为 None，此处锚函数自身契约：float 失败 →
        2.0 缺省，与显式 2.0 逐位一致）。"""
        from core.analysis.condition_eval import _spike_condition
        out = _spike_condition(df["volume"], df, "garbage_str")
        base = _spike_condition(df["volume"], df, 2.0)
        pd.testing.assert_series_equal(out, base)

    def test_shrink_坏str乘数_float失败回退缺省05(self, df):
        from core.analysis.condition_eval import _shrink_condition
        out = _shrink_condition(df["volume"], df, "garbage_str")
        base = _shrink_condition(df["volume"], df, 0.5)
        pd.testing.assert_series_equal(out, base)

    def test_lookback_condition_mode_all_全窗达成形(self, df):
        """mode='all' 分支（引擎 action_map 仅注册 lookback_any，
        all 模式为 THEN 侧共享形制——函数级特征锚）。"""
        from core.analysis.condition_eval import _lookback_condition
        cond = {"inner_action": "gt", "target": 0, "window": 2}
        out = _lookback_condition(df, cond, df["close"], "all")
        assert len(out) == len(df)
        assert out.dtype == bool

    def test_single_condition_未知action_结构化拒绝(self, df):
        """fix-research-c2：未知 action 结构化拒绝（原全 False 静默
        掩码已消灭——warnings 可选形制不再影响该路径）。"""
        from core.analysis.errors import ResearchError
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(
                df, {"subject": "close", "action": "nope_action", "target": 1},
                warnings=None)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"


class TestWickTouchStatFace:
    """影线触碰统计面（阶段四②盲区消解——_wick_touch_condition 整函数
    零覆盖补强；语义与回测面 executor._eval_wick_touch 对齐锚）。
    line=subject 列解析序列（action_map 分派传 subject_series）。"""

    def test_above_branch(self, df):
        """direction=above：最高价近线（|high−line|≤|line|×1%）+收盘
        在线下——固定线列构造全覆盖触发。"""
        import pandas as pd
        line_val = 100.0
        staged = df.assign(
            bb_lower=pd.Series(line_val, index=df.index),
            high=line_val * 1.005,   # 距线 0.5%（1% 带内）
            close=line_val * 0.99)   # 收盘在线下
        out = _evaluate_single_condition(staged, {
            "subject": "bb_lower", "action": "wick_touch",
            "target": 0, "direction": "above"})
        assert int(out.sum()) == len(df), "above 全 bar 触发（带内+线下）"

    def test_below_branch(self, df):
        """direction=below：最低价近线+收盘在线上。"""
        import pandas as pd
        line_val = 100.0
        staged = df.assign(
            bb_lower=pd.Series(line_val, index=df.index),
            low=line_val * 0.995,
            close=line_val * 1.01)
        out = _evaluate_single_condition(staged, {
            "subject": "bb_lower", "action": "wick_touch",
            "target": 0, "direction": "below"})
        assert int(out.sum()) == len(df), "below 全 bar 触发（带内+线上）"

    def test_above_off_band_no_touch(self, df):
        """负向：高价距线超 1% 带（0.5%→2%）——触碰带外零触发。"""
        import pandas as pd
        line_val = 100.0
        staged = df.assign(
            bb_lower=pd.Series(line_val, index=df.index),
            high=line_val * 1.02, close=line_val * 0.99)
        out = _evaluate_single_condition(staged, {
            "subject": "bb_lower", "action": "wick_touch",
            "target": 0, "direction": "above"})
        assert int(out.sum()) == 0, "带外零触发（proximity 判定有效）"
