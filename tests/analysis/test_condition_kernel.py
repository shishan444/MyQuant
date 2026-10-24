"""组合条件求值内核测试（add-research-analysis-tools 0.4.1 任务 1.5）。

S1 mask_for_all：交集独立对照（手动 pandas 逐条求值后 &——禁调被测
实现生成期望）、内核自洽锚（mask_for_all([c]) ≡ mask_for(c) 逐位
一致）、高周期混合交集（相位灵敏构造——期望按 asof_completed 语义
人工推导）、空数组内部不变量拒绝。
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_kernel import (
    PLUGIN_COLUMN_PREFIXES, collect_condition_plugin_columns,
    collect_condition_refs, mask_for, mask_for_all)
from core.analysis.hypothesis_service import ResearchError


def _make_df(n: int = 300, seed: int = 42, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    return pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 1000, n),
    }, index=idx)


class TestMaskForAll:
    def test_intersection_matches_manual_and(self) -> None:
        """独立对照：手动 pandas 表达式逐条求值后按位 &（期望不来自
        被测实现）。"""
        df = _make_df(300, seed=7)
        med = float(df["close"].median())
        hi = med + 0.3 * (float(df["close"].max()) - med)
        conds = [
            {"subject": "close", "action": "gt", "target": med},
            {"subject": "close", "action": "lt", "target": hi},
            {"subject": "volume", "action": "gt", "target": 550.0},
        ]
        expected = ((df["close"] > med) & (df["close"] < hi)
                    & (df["volume"] > 550.0))
        got = mask_for_all(df, conds)
        pd.testing.assert_series_equal(
            got, expected.astype(bool), check_names=False)
        assert int(got.sum()) == int(expected.sum())

    @pytest.mark.parametrize("n", [2, 5], ids=["最小2条", "上限5条"])
    def test_two_and_five_condition_intersection(self, n: int) -> None:
        """最小组合与上限条数均正确求 AND（条数域校验归路由层——内核
        只验证求值）。"""
        df = _make_df(300, seed=13)
        thr = float(df["volume"].quantile(0.8))
        conds = [{"subject": "volume", "action": "gt", "target": thr}] * n
        expected = df["volume"] > thr
        got = mask_for_all(df, conds)
        pd.testing.assert_series_equal(
            got, expected.astype(bool), check_names=False)

    def test_single_condition_identity(self) -> None:
        """内核自洽锚：mask_for_all([c]) 与 mask_for(c) 同 work df
        逐位一致（单条 AND 数学恒等——键形制判据 D9 的求值侧依据）。"""
        df = _make_df(250, seed=11)
        c = {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"},
             "action": "gt", "target": 55}
        single = mask_for(df.copy(), c)
        wrapped = mask_for_all(df.copy(), [c])
        pd.testing.assert_series_equal(wrapped, single, check_names=False)
        assert int(wrapped.sum()) == int(single.sum())

    def test_structured_subject_mixed_intersection(self) -> None:
        """结构化 subject 条 × 字符串 subject 条混合交集（独立对照——
        RSI 手算 via 指标库单独现算后与基础列条件 &）。"""
        from core.features.indicators import _compute_indicator
        df = _make_df(300, seed=17)
        c_rsi = {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"},
                 "action": "gt", "target": 50}
        c_close = {"subject": "close", "action": "gt",
                   "target": float(df["close"].median())}
        rsi = _compute_indicator(df, "RSI", {"period": 14})["rsi_14"]
        expected = (rsi > 50) & (df["close"] > df["close"].median())
        got = mask_for_all(df.copy(), [c_rsi, c_close])
        pd.testing.assert_series_equal(
            got, expected.fillna(False).astype(bool), check_names=False)

    def test_empty_conditions_internal_reject(self) -> None:
        """空数组到达内核=调用方接线缺陷——内部不变量 ValueError
        （非外部 422 语义；条数域校验归路由/校验层）。"""
        df = _make_df(50)
        with pytest.raises(ValueError, match="空 conditions"):
            mask_for_all(df, [])

    def test_higher_tf_mixed_intersection(self, tmp_path) -> None:
        """高周期混合交集（基准条 × tf 条）：期望按 asof_completed
        语义人工推导——True 4h bar（开盘 k、收盘 k+4h）投影到基准
        [k+4h, k+8h) 的 bar；构造唯一 True 高周期 bar（阈值=次大
        close → 恰一根越界），基准条恒 True——交集即投影窗自身。"""
        from core.analysis.condition_kernel import MtfEvalContext
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir()
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        high.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        second = float(high["close"].nlargest(2).iloc[1])
        tf_cond = {"subject": "close", "action": "gt", "target": second,
                   "timeframe": "4h"}
        assert int((high["close"] > second).sum()) == 1  # 恰一根 True
        k = high.index[high["close"] > second][0]
        done = k + pd.Timedelta(hours=4)
        expected = pd.Series(
            (base.index >= done) & (base.index < done + pd.Timedelta(hours=4)),
            index=base.index)

        base_cond = {"subject": "close", "action": "gt", "target": 0.0}
        ctx = MtfEvalContext(data_dir, "BTCUSDT", "1h")
        got = mask_for_all(base.copy(), [base_cond, tf_cond], mtf=ctx)
        pd.testing.assert_series_equal(got, expected, check_names=False)
        assert int(got.sum()) == 4  # 投影窗恰覆盖 4 根 1h bar


_BAD_DICT_SUBJECT = {
    "subject": {"indicator": "NOPE", "params": {"period": 14},
                "field": "rsi"},
    "action": "gt", "target": 55,
}


class TestCollectRefsSubjectDictTranslation:
    """CARD-03：dict subject 解析失败（ColumnRefError）转译
    RESEARCH_CONDITION_INVALID（原裸穿 ValueError 500——六链路公共
    底座在此单点锚）；strict=False 宽容面（str subject 不可解码静默
    跳过、target unknown 不并入）语义冻结。"""

    @pytest.mark.parametrize("strict", [True, False],
                             ids=["装载链 strict", "掩码链宽容"])
    def test_dict_subject_unknown_indicator_translated(self, strict):
        with pytest.raises(ResearchError) as ei:
            collect_condition_refs(_BAD_DICT_SUBJECT, strict=strict)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.message.startswith("condition.subject 非法")
        # ColumnRefError 原因透传：code 与 indicator 进 detail
        assert ei.value.detail["code"] == "PARAM_INVALID"
        assert ei.value.detail["indicator"] == "NOPE"
        assert ei.value.__cause__ is not None  # 原因链保留

    def test_lenient_face_frozen(self):
        """宽容面冻结锚：str subject 不可解码静默跳过（refs 空）；
        target unknown 在 strict=False 不并入（拒绝面归 strict=True）。"""
        refs = collect_condition_refs(
            {"subject": "no_such_column", "action": "gt", "target": "1"})
        assert refs == []
        refs2 = collect_condition_refs(
            {"subject": "close", "action": "gt", "target": "zzz_unknown"})
        assert refs2 == []


class TestConditionItemStrictness:
    """T6：条件形制严格化（unblock-research-validation 任务 5，GAP-08 防呆）。"""

    def test_t6a_subject_inner_timeframe_pointed_rejection(self):
        """T6a 主锚：subject 内 timeframe → 422 指路（「timeframe 是
        条件顶层键，不在 subject 内部」——RR-015 实测静默忽略防呆）。"""
        from api.schemas import ConditionItem, IndicatorRef
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as ei:
            IndicatorRef(**{"indicator": "RSI", "params": {"period": 14},
                            "field": "rsi", "timeframe": "1d"})
        assert "timeframe" in str(ei.value)
        with pytest.raises(ValidationError):
            ConditionItem(**{
                "subject": {"indicator": "RSI", "params": {"period": 14},
                            "timeframe": "1d"},
                "action": "lt", "target": 35})

    def test_t6b_condition_item_unknown_key(self):
        """T6b 条目未知顶层键 422（extra=forbid）。"""
        from api.schemas import ConditionItem
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ConditionItem(**{"subject": {"indicator": "RSI",
                                         "params": {"period": 14}},
                             "action": "lt", "target": 35,
                             "bogus_key": 1})

    def test_t6c_array_whitelist_with_keys_detail(self):
        """T6c 数组校验白名单：未知键附 {unknown_keys, legal_keys}+指路。"""
        from core.analysis.condition_kernel import validate_conditions_array
        from core.analysis.hypothesis_service import ResearchError
        with pytest.raises(ResearchError) as ei:
            validate_conditions_array([
                {"subject": {"indicator": "RSI", "params": {}},
                 "action": "lt", "target": 35, "inner_bogus": 1}])
        assert ei.value.detail["unknown_keys"] == ["inner_bogus"]
        assert set(ei.value.detail["legal_keys"]) == {
            "subject", "action", "target", "timeframe"}
        assert "顶层键" in ei.value.detail["hint"]

    def test_t6d_kernel_whitelist_matches_schemas(self):
        """T6d 对拍：kernel 白名单 == ConditionItem 声明面。"""
        from core.analysis.condition_kernel import _CONDITION_ITEM_KEYS
        from api.schemas import ConditionItem
        declared = set(ConditionItem.model_fields.keys())
        assert set(_CONDITION_ITEM_KEYS) == declared, \
            "白名单与声明面漂移（共享单点对拍锚）"

    def test_t6f_legal_request_zero_change(self):
        """T6f 合法请求零变化：白名单四键请求照常通过。"""
        from core.analysis.condition_kernel import validate_conditions_array
        validate_conditions_array([
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35, "timeframe": "1d"}])


class TestConditionItemStrictnessConsumers:
    """T6e 四消费者（tester 补——spec「全部条件消费者同语义」：未知
    键经共享单点白名单结构化拒绝）。参数化四模板入口各一例。

    现状警示（2026-10-15 实证）：regime_split/exit_sweep 拒绝 ✓；
    condition_stats/event_study 静默通过（未知键被忽略直接产出结果
    ——GAP-08 同型）——两例红为本偏差的失败证据，待修复转绿
    （DEV-4 已退回 developer）。"""

    @pytest.mark.parametrize("template", [
        "condition_stats", "event_study", "regime_split", "exit_sweep"])
    def test_t6e_unknown_key_rejected_per_consumer(self, template, tmp_path):
        from importlib import import_module
        from core.analysis.errors import ResearchError
        from tests.helpers.data_factory import make_enhanced_df
        df = make_enhanced_df(n=300, freq="4h", seed=3)
        rng = [{"symbol": "BTCUSDT", "timeframe": "4h",
                "start": "2023-01-01T00:00:00Z",
                "end": "2023-02-01T00:00:00Z"}]
        bad = {"subject": {"indicator": "RSI", "params": {"period": 14}},
               "action": "lt", "target": 35, "inner_bogus": 1}
        mod = import_module(f"core.analysis.templates.{template}")
        fn = getattr(mod, template)
        if template == "exit_sweep":
            # exit_sweep 形制：(db_path, data_dir, request_range, *, ...)
            from core.persistence.db_ext import init_db_ext
            db = tmp_path / "q.db"
            init_db_ext(db)
            data_dir = tmp_path / "market"
            data_dir.mkdir()
            make_enhanced_df(n=300, freq="4h", seed=3).to_parquet(
                data_dir / "BTCUSDT_4h.parquet")
            with pytest.raises(ResearchError) as ei:
                fn(db, data_dir, dict(rng[0]), condition=bad,
                   direction="long",
                   grid={"stop_loss": [0.01], "take_profit": [0.02]})
        else:
            kwargs = {"condition": bad, "data_ranges": rng}
            if template == "condition_stats":
                kwargs = {"conditions": [bad], "data_ranges": rng,
                          "forward_bars": 6}
            elif template == "regime_split":
                kwargs = {"conditions": [bad], "data_ranges": rng}
            with pytest.raises(ResearchError) as ei:
                fn(df, **kwargs)
        assert "inner_bogus" in str(ei.value.detail.get(
            "unknown_keys", [])) or "未知键" in ei.value.message


class TestPluginColumnCollection:
    """插件列前缀判定行为锚（fix-fullrun-baseline-4red 特征网：
    TD-CARD-02 常量收敛为单源 PLUGIN_COLUMN_PREFIXES 后，前缀判定
    根的现状记录——collect_condition_plugin_columns 是预检装载链
    （precheck/analysis 路由/case_inspection）的插件列收集入口，
    此前测试域零直接承载，行为等价仅靠消费域回归间接保证）。"""

    def test_prefixes_tuple_value(self) -> None:
        """常量值锚：fx_（公式插件）+model_（模型插件）两前缀。"""
        assert PLUGIN_COLUMN_PREFIXES == ("fx_", "model_")

    def test_collects_prefixed_subject_and_target(self) -> None:
        """subject/target 侧字符串前缀列均收集；保守宽面——无需
        registry 注册（可达性校验归调用方，收集面不收窄）。"""
        conds = [
            {"subject": "fx_alpha", "action": "gt", "target": "model_beta"},
            {"subject": "model_gamma", "action": "lt", "target": 0.5},
        ]
        assert collect_condition_plugin_columns(conds) == {
            "fx_alpha", "model_beta", "model_gamma"}

    def test_non_prefixed_and_structured_refs_skipped(self) -> None:
        """基础列 subject/数值 target/结构化 dict subject/非 dict
        条件项均不参与收集（纯基础列+数值条件集为空集）。"""
        conds = [
            {"subject": "close", "action": "gt", "target": 1.0},
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "value"}, "action": "gt", "target": 70},
            "not-a-dict",
        ]
        assert collect_condition_plugin_columns(conds) == set()
