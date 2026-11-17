"""条件求值内核（core/analysis/condition_eval）特征测试网。

retire-legacy 10.4 分拣：本文件原为 core/validation/ 特征测试网
（修复期临时安全网，非 scene 部分），core/validation 壳整体退役后
按守护对象存废分拣：

- 删除（守护对象已消失）：mtf 全部（get_timeframe_minutes/
  load_mtf_data 及两引擎装载异常面）、engine 主流程
  （validate_hypothesis/_evaluate_conditions/_check_then_conditions）、
  rule_engine（evaluate_rules 及共享组合锚）、/api/validate 的
  MTF 装载出站面；
- 保留（求值内核 D-1 自 core/validation/engine.py 纯搬家至
  core.analysis.condition_eval，行为逐字节不变）：
  _evaluate_single_condition 解析三分支与动作派发、
  _resolve_target/_resolve_subject 跨周期与 registry 解析、
  CARD-05 window 坏值结构化拒绝、spike/shrink Series target 兜底
  ——用例内 import 改指 condition_eval，断言零改动。

语义约定（阶段四 V3 化）：锚定行为已是团队确认的规格契约；断言值
经真实执行钉住，预期依据随断言注释。数据确定性构造。

Run with: venv/bin/python -m pytest tests/validation/test_validation_feature_net.py -v
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]


# ===========================================================================
# 1. _evaluate_single_condition（解析三分支 + 动作派发）
# ===========================================================================

class TestEvaluateSingleCondition:
    """单条件求值特征锚：解析失败降级与动作派发。"""

    @staticmethod
    def _df():
        return pd.DataFrame(
            {"close": [100.0, 90.0, 110.0, 95.0],
             "ema_fast": [99.0, 91.0, 109.0, 96.0],
             "ema_slow": [100.0, 90.0, 105.0, 97.0]},
            index=pd.date_range("2024-01-01", periods=4, freq="4h"),
        )

    def test_unknown_subject_warns_and_all_false(self):
        from core.analysis.condition_eval import _evaluate_single_condition
        warnings: list = []
        mask = _evaluate_single_condition(
            self._df(), {"subject": "no_such_col", "action": "gt", "target": 1},
            warnings,
        )
        assert mask.tolist() == [False] * 4
        assert any("not found in data columns" in w for w in warnings)

    def test_unresolvable_target_warns_and_all_false(self):
        from core.analysis.condition_eval import _evaluate_single_condition
        warnings: list = []
        mask = _evaluate_single_condition(
            self._df(), {"subject": "close", "action": "gt", "target": "not_a_col_x"},
            warnings,
        )
        assert mask.tolist() == [False] * 4
        assert any("could not be resolved" in w for w in warnings)

    def test_unknown_action_structured_reject(self):
        """fix-research-c2：未知 action 从 warnings+全 False 掩码改为
        结构化拒绝（422 携 legal_values）——静默降级使研究者误读为
        「条件从未发生」（缺陷三 fr_cfbcab214136）。"""
        from core.analysis.errors import ResearchError
        from core.analysis.condition_eval import _evaluate_single_condition
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(
                self._df(), {"subject": "close", "action": "teleport", "target": 100},
                None,
            )
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_cross_above_series_detects_cross(self):
        from core.analysis.condition_eval import _evaluate_single_condition
        df = self._df()
        # ema_fast 与 ema_slow：bar0 fast<slow、bar1 fast>slow → 穿越@1
        cond = {"subject": "ema_fast", "action": "cross_above_series", "target": "ema_slow"}
        mask = _evaluate_single_condition(df, cond)
        assert mask.tolist() == [False, True, False, False]

    def test_cross_below_series_detects_cross(self):
        from core.analysis.condition_eval import _evaluate_single_condition
        df = self._df()
        # bar2 fast=109>slow=105、bar3 fast=96<slow=97 → 下穿@3
        cond = {"subject": "ema_fast", "action": "cross_below_series", "target": "ema_slow"}
        mask = _evaluate_single_condition(df, cond)
        assert mask.tolist() == [False, False, False, True]

    def test_cross_above_series_missing_target_all_false(self):
        """现状记录：target 数值串可过外层解析，但动作内部按 subject
        解析不到列时全 False（L493-495）。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        cond = {"subject": "ema_fast", "action": "cross_above_series", "target": "95"}
        mask = _evaluate_single_condition(self._df(), cond)
        assert mask.tolist() == [False] * 4

    def test_cross_below_series_missing_target_all_false(self):
        from core.analysis.condition_eval import _evaluate_single_condition
        cond = {"subject": "ema_fast", "action": "cross_below_series", "target": "95"}
        mask = _evaluate_single_condition(self._df(), cond)
        assert mask.tolist() == [False] * 4

    def test_cross_below_with_series_target(self):
        """现状记录：breakdown/cross_below 对 Series 型 target 的下穿判定。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        df = self._df()
        # ema_fast 与 ema_slow：bar2 fast(109)>slow(105)、bar3 fast(96)<slow(97)
        cond = {"subject": "ema_fast", "action": "breakdown", "target": "ema_slow"}
        mask = _evaluate_single_condition(df, cond)
        assert mask.tolist() == [False, False, False, True]

    @pytest.mark.parametrize(
        ("inner_action", "expected"),
        [
            # 逐 bar 信号 gt/ge95=[T,F,T,T]、lt95=[F,T,F,F]、le95=[F,T,F,T]，
            # rolling(2).apply(any) 窗口滑移后的现状值：
            pytest.param("gt", [True, True, True, True], id="lookback_gt"),
            pytest.param("lt", [False, True, True, False], id="lookback_lt"),
            pytest.param("ge", [True, True, True, True], id="lookback_ge等于"),
            pytest.param("le", [False, True, True, True], id="lookback_le等于"),
        ],
    )
    def test_lookback_any_inner_comparisons(self, inner_action, expected):
        from core.analysis.condition_eval import _evaluate_single_condition
        df = self._df()
        cond = {
            "subject": "close", "action": "lookback_any",
            "inner_action": inner_action, "target": 95, "window": 2,
        }
        mask = _evaluate_single_condition(df, cond)
        assert mask.tolist() == expected

    def test_lookback_any_default_price_above(self):
        """现状记录：非比较型 inner_action 走 close>subject 默认语义。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        df = self._df()
        cond = {
            "subject": "ema_slow", "action": "lookback_any",
            "inner_action": "price_above", "target": 0, "window": 2,
        }
        mask = _evaluate_single_condition(df, cond)
        # close>ema_slow 逐 bar [F,F,T,F]，滑移窗 any → [F,F,T,T]
        assert mask.tolist() == [False, False, True, True]

    def test_spike_and_shrink_actions(self):
        from core.analysis.condition_eval import _evaluate_single_condition
        n = 30
        volume = np.full(n, 1000.0)
        volume[25] = 5000.0
        volume[10] = 100.0
        df = pd.DataFrame(
            {"close": np.full(n, 100.0), "volume": volume},
            index=pd.date_range("2024-01-01", periods=n, freq="4h"),
        )
        spike_mask = _evaluate_single_condition(
            df, {"subject": "volume", "action": "spike", "target": 2.0},
        )
        assert spike_mask.tolist()[25] is True or spike_mask.tolist()[25] == True  # noqa: E712
        shrink_mask = _evaluate_single_condition(
            df, {"subject": "volume", "action": "shrink", "target": 0.5},
        )
        assert bool(shrink_mask.tolist()[10])

    def test_divergence_and_consecutive_dispatch(self):
        """现状记录：divergence/consecutive 动作经 patterns 模块派发可求值。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        n = 40
        dates = pd.date_range("2024-01-01", periods=n, freq="4h")
        closes = 100 + 10 * np.sin(np.arange(n) * 2 * np.pi / 30)
        df = pd.DataFrame(
            {"close": closes, "volume": np.full(n, 1000.0),
             "rsi_14": 50 + 20 * np.sin(np.arange(n) * 2 * np.pi / 30 + 1.0)},
            index=dates,
        )
        for action, kwargs in [
            ("divergence_top", {"target": 30}),
            ("divergence_bottom", {"target": 30}),
            ("consecutive_up", {"target": 3}),
            ("consecutive_down", {"target": 3}),
        ]:
            cond = {"subject": "rsi_14", "action": action, **kwargs}
            mask = _evaluate_single_condition(df, cond)
            assert len(mask) == n  # 可求值不抛错（现状锚：掩码长度一致）
            assert mask.dtype == bool

    def test_touch_bounce_bad_param_raises_research_error(self):
        """现状记录：SNR 动作数值键坏值结构化拒绝（ResearchError）。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        from core.analysis.errors import ResearchError
        df = pd.DataFrame(
            {"close": [100.0, 99.0, 101.0], "volume": [1.0, 1.0, 1.0]},
            index=pd.date_range("2024-01-01", periods=3, freq="4h"),
        )
        cond = {
            "subject": "close", "action": "touch_bounce",
            "target": 0,  # target 须可解析，否则在解析层提前全 False
            "direction": "support",
            "proximity_pct": "not-a-number",
        }
        with pytest.raises(ResearchError, match="proximity_pct 须为数值"):
            _evaluate_single_condition(df, cond)

    def test_role_reversal_dispatch_evaluates(self):
        """阶段四盲区消解：target 须可解析才到达派发（缺键会被
        _resolve_target 前置拦截成全 False——阶段四建网期构造缺陷）。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        n = 40
        closes = 100 + 8 * np.sin(np.arange(n) * 2 * np.pi / 25)
        df = pd.DataFrame(
            {"close": closes, "volume": np.full(n, 1000.0)},
            index=pd.date_range("2024-01-01", periods=n, freq="4h"),
        )
        cond = {
            "subject": "close", "action": "role_reversal",
            "target": 0, "role": "resistance", "lookback": 10,
        }
        mask = _evaluate_single_condition(df, cond)
        assert len(mask) == n
        assert mask.dtype == bool

    def test_snr_subject_missing_column_false(self):
        """0.3.2 卡3c 后行为：SNR 动作 subject 列缺失 → 全 False 降级
        （锚随缺陷裁定改写，原锚 test_snr_subject_fuzzy_column_match）。
        精确口径（CARD-01 探针复核）：本 cond 无 target 键 → target=""
        经 _resolve_target None 拦截 + subject 缺列经 :336 前置拦截，
        False 来自前置拦截路径（修复前后同形）；_resolve_snr_pattern
        被删模糊段物理不可达（s.name 恒在列、s=None 到不了分派），
        本锚钉的是「缺列绝不模糊命中」的行为面。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        n = 30
        closes = 100 + 8 * np.sin(np.arange(n) * 2 * np.pi / 20)
        df = pd.DataFrame(
            {"my_close_price": closes, "volume": np.full(n, 1000.0)},
            index=pd.date_range("2024-01-01", periods=n, freq="4h"),
        )
        cond = {
            "subject": "close", "action": "touch_bounce",
            "direction": "support", "proximity_pct": 0.01, "bounce_pct": 0.005,
        }
        mask = _evaluate_single_condition(df, cond)  # close 列缺失，无模糊
        assert len(mask) == n
        assert not mask.any()


# ===========================================================================
# 2. _resolve_target / _resolve_subject（跨周期与 registry 解析）
# ===========================================================================

class TestResolve:
    """解析层特征锚。"""

    @staticmethod
    def _df():
        return pd.DataFrame(
            {"close": [1.0, 2.0], "ema_20_4h": [10.0, 20.0],
             "my_close_price": [5.0, 6.0]},
            index=pd.date_range("2024-01-01", periods=2, freq="4h"),
        )

    def test_resolve_target_numeric_passthrough(self):
        from core.analysis.condition_eval import _resolve_target
        assert _resolve_target(self._df(), 3.5) == 3.5
        assert _resolve_target(self._df(), "2.5") == 2.5

    def test_resolve_target_empty_returns_none(self):
        from core.analysis.condition_eval import _resolve_target
        assert _resolve_target(self._df(), "") is None
        assert _resolve_target(self._df(), None) is None

    def test_resolve_target_cross_tf_direct_suffix(self):
        from core.analysis.condition_eval import _resolve_target
        series = _resolve_target(self._df(), "4h:ema_20")
        assert series is not None
        assert series.tolist() == [10.0, 20.0]

    def test_resolve_target_cross_tf_exact_only(self):
        """0.3.2 卡3c 后行为：跨周期形制仅精确列名命中（"4h:ema_20"
        只查 ema_20_4h），后缀模糊兜底已删——非精确命名返回 None
        （原锚 test_resolve_target_cross_tf_fuzzy_suffix 随缺陷裁定改写）。"""
        from core.analysis.condition_eval import _resolve_target
        df = pd.DataFrame(
            {"prefix_ema_20_4h": [10.0, 20.0],
             "ema_20_4h": [1.0, 2.0]},
            index=pd.date_range("2024-01-01", periods=2, freq="4h"),
        )
        assert _resolve_target(df, "4h:ema_20").tolist() == [1.0, 2.0]
        df2 = pd.DataFrame(
            {"prefix_ema_20_4h": [10.0, 20.0]},
            index=pd.date_range("2024-01-01", periods=2, freq="4h"),
        )
        assert _resolve_target(df2, "4h:ema_20") is None

    def test_resolve_target_exact_column(self):
        from core.analysis.condition_eval import _resolve_target
        series = _resolve_target(self._df(), "close")
        assert series.tolist() == [1.0, 2.0]

    def test_resolve_target_unknown_column_none(self):
        """0.3.2 卡3c 后行为：非精确列名 → None（归一化子串兜底已删，
        原锚 test_resolve_target_fuzzy_column 随缺陷裁定改写）。"""
        from core.analysis.condition_eval import _resolve_target
        assert _resolve_target(self._df(), "closeprice") is None

    def test_resolve_subject_registry_prefix(self):
        """现状记录：subject 为注册表 output_field 名时还原默认参数列。"""
        from core.analysis.condition_eval import _resolve_subject
        df = pd.DataFrame(
            {"rsi_14": [55.0, 45.0], "close": [1.0, 2.0]},
            index=pd.date_range("2024-01-01", periods=2, freq="4h"),
        )
        series = _resolve_subject(df, "rsi")
        assert series is not None
        assert series.tolist() == [55.0, 45.0]

    def test_resolve_subject_alias_map(self):
        from core.analysis.condition_eval import _resolve_subject
        df = pd.DataFrame({"close": [1.0, 2.0]}, index=[0, 1])
        assert _resolve_subject(df, "price").tolist() == [1.0, 2.0]

    def test_resolve_subject_missing_returns_none(self):
        from core.analysis.condition_eval import _resolve_subject
        df = pd.DataFrame({"close": [1.0]}, index=[0])
        assert _resolve_subject(df, "no_such") is None

    def test_resolve_subject_registry_hit_column_missing_returns_none(self):
        """现状记录：注册表前缀名命中但默认参数列不在 df → None（L387-389）。"""
        from core.analysis.condition_eval import _resolve_subject
        df = pd.DataFrame({"close": [1.0]}, index=[0])  # 无 rsi_14 列
        assert _resolve_subject(df, "rsi") is None


# ===========================================================================
# 3. CARD-05：_lookback_condition window 坏值/零/负结构化拒绝
# ===========================================================================

class TestLookbackWindowValidation:
    """CARD-05 锚：window 裸穿 rolling(window=0) 的 500 改 ResearchError。"""

    @staticmethod
    def _cond(window):
        return {
            "subject": "close", "action": "lookback_any",
            "inner_action": "gt", "target": 95, "window": window,
        }

    @pytest.mark.parametrize(
        "bad_window",
        [
            pytest.param(0, id="零窗口"),
            pytest.param(-1, id="负窗口"),
            pytest.param("abc", id="非数值字符串"),
            # SEC-08 增量：int(inf) OverflowError 裸穿收口；int(nan) ValueError
            pytest.param(float("inf"), id="inf窗口"),
            pytest.param(float("-inf"), id="负inf窗口"),
            pytest.param(float("nan"), id="nan窗口"),
        ],
    )
    def test_bad_window_rejected(self, bad_window):
        from core.analysis.errors import ResearchError
        from core.analysis.condition_eval import _evaluate_single_condition
        df = pd.DataFrame(
            {"close": [100.0, 90.0, 110.0]},
            index=pd.date_range("2024-01-01", periods=3, freq="4h"),
        )
        with pytest.raises(ResearchError, match="condition.window"):
            _evaluate_single_condition(df, self._cond(bad_window))

    def test_valid_window_still_works(self):
        from core.analysis.condition_eval import _evaluate_single_condition
        df = pd.DataFrame(
            {"close": [100.0, 90.0, 110.0]},
            index=pd.date_range("2024-01-01", periods=3, freq="4h"),
        )
        mask = _evaluate_single_condition(df, self._cond(2))
        assert len(mask) == 3


# ===========================================================================
# 4. 阶段四盲区消解补锚（spike/shrink Series target 兜底分支）
# ===========================================================================

class TestStage4BlindSpotFill:
    """阶段四盲区消解补锚：判定路径上被前置拦截掩盖的兜底分支。

    retire-legacy 10.4：原 test_then_window_zero_rise_form_rejected
    （_check_then_conditions 守护）已随 core/validation 退役删除。
    """

    def test_spike_series_target_falls_back_default_multiplier(self):
        """现状记录：target 为列（Series）时 float() 抛 TypeError →
        multiplier 按 2.0 兜底（数值串/数值不会到达此分支）。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        n = 30
        volume = np.full(n, 1000.0)
        volume[25] = 5000.0  # 5000/1000=5 >= 2.0 兜底阈值 → 触发
        df = pd.DataFrame(
            {"close": np.full(n, 100.0), "volume": volume,
             "baseline": np.full(n, 999.0)},  # baseline 仅作 Series target 载体
            index=pd.date_range("2024-01-01", periods=n, freq="4h"),
        )
        cond = {"subject": "volume", "action": "spike", "target": "baseline"}
        mask = _evaluate_single_condition(df, cond)
        assert bool(mask.tolist()[25])

    def test_shrink_series_target_falls_back_default_multiplier(self):
        """现状记录：shrink 对 Series target 的 0.5 兜底。"""
        from core.analysis.condition_eval import _evaluate_single_condition
        n = 30
        volume = np.full(n, 1000.0)
        volume[10] = 100.0  # 100/1000=0.1 <= 0.5 兜底阈值 → 触发
        df = pd.DataFrame(
            {"close": np.full(n, 100.0), "volume": volume,
             "baseline": np.full(n, 999.0)},
            index=pd.date_range("2024-01-01", periods=n, freq="4h"),
        )
        cond = {"subject": "volume", "action": "shrink", "target": "baseline"}
        mask = _evaluate_single_condition(df, cond)
        assert bool(mask.tolist()[10])
