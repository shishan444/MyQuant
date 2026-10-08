"""core/validation/ 特征测试网（修复期临时安全网）——非 scene 部分。

背景：下游审查战（fix/mcp-research-downstream-review）建网补薄，实测
全部消费方用例齐跑下 mtf 52% / engine 63% / rule_engine 77%：
load_mtf_data 与 get_timeframe_minutes 的 fallback 解析、
validate_hypothesis 与 evaluate_rules 两个引擎主流程从未被执行。

0.3.7 H7 归位：目录/类名已去平行批次后缀（*_Feature 消除）。
语义约定（阶段四 V3 化）：经 32 卡修复队列与裁决沉淀，锚定行为
已是团队确认的规格契约；断言值经真实执行钉住，预期依据随断言注释。
数据确定性构造。

Run with: venv/bin/python -m pytest tests/evolution/test_validation_feature_net.py -v
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_ohlcv


def _sine_ohlcv(n: int = 120, base: float = 100.0, amp: float = 10.0,
                period: int = 20, freq: str = "4h") -> pd.DataFrame:
    """正弦价格 OHLCV：close 在 base±amp 间周期波动（确定性）。"""
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    closes = base + amp * np.sin(np.arange(n) * 2 * np.pi / period)
    df = pd.DataFrame(
        {
            "open": closes,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(n, 1000.0),
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


def _write_parquet(tmp_path, name: str, df: pd.DataFrame) -> str:
    from core.data.storage import save_parquet
    save_parquet(df, tmp_path / f"{name}.parquet")
    return str(tmp_path)


# ===========================================================================
# 1. mtf.get_timeframe_minutes（表外周期 fallback 解析未执行）
# ===========================================================================

class TestGetTimeframeMinutes:
    """mtf.py 特征锚：表内查表 / Nh·Nm·Nd·Nw fallback / 未知拒绝。"""

    @pytest.mark.parametrize(
        ("tf", "expected"),
        [
            pytest.param("4h", 240, id="表内4h"),
            pytest.param("1d", 1440, id="表内1d"),
            pytest.param("2h", 120, id="表外2h走fallback"),
            pytest.param("90m", 90, id="表外90m走fallback"),
            pytest.param("3d", 4320, id="表外3d走fallback"),
            pytest.param("2w", 20160, id="表外2w走fallback"),
            pytest.param(" 4H ", 240, id="大小写与空白归一"),
        ],
    )
    def test_timeframe_to_minutes(self, tf, expected):
        from core.validation.mtf import get_timeframe_minutes
        assert get_timeframe_minutes(tf) == expected

    def test_unknown_timeframe_raises(self):
        from core.validation.mtf import get_timeframe_minutes
        with pytest.raises(ValueError, match="Unknown timeframe"):
            get_timeframe_minutes("4x")


# ===========================================================================
# 2. mtf.load_mtf_data（整体未执行）
# ===========================================================================

class TestLoadMtfData:
    """mtf.py 特征锚：多周期加载 / 缺失跳过 / 空数据跳过 / 日期过滤。"""

    @staticmethod
    def _setup_files(tmp_path):
        base = make_ohlcv(n=200, freq="4h")
        daily = make_ohlcv(n=60, freq="1d")
        _write_parquet(tmp_path, "TESTUSDT_4h", base)
        _write_parquet(tmp_path, "TESTUSDT_1d", daily)
        return str(tmp_path)

    def test_loads_existing_timeframes_only(self, tmp_path):
        from core.validation.mtf import load_mtf_data
        data_dir = self._setup_files(tmp_path)
        result = load_mtf_data("TESTUSDT", ["4h", "1d", "2h"], data_dir=data_dir)
        assert set(result.keys()) == {"4h", "1d"}  # 2h 无文件被跳过
        assert len(result["4h"]) == 200
        assert len(result["1d"]) == 60

    def test_empty_parquet_skipped(self, tmp_path):
        from core.validation.mtf import load_mtf_data
        empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], name="timestamp"),
        )
        _write_parquet(tmp_path, "TESTUSDT_4h", empty)
        result = load_mtf_data("TESTUSDT", ["4h"], data_dir=str(tmp_path))
        assert result == {}

    def test_date_filter_excludes_out_of_range(self, tmp_path):
        from core.validation.mtf import load_mtf_data
        data_dir = self._setup_files(tmp_path)
        # 数据从 2024-01-01 起；请求 2025 年范围 → 过滤后空则不收录
        result = load_mtf_data(
            "TESTUSDT", ["4h"], start="2025-01-01", end="2025-02-01",
            data_dir=data_dir,
        )
        assert result == {}

    def test_date_filter_keeps_in_range(self, tmp_path):
        from core.validation.mtf import load_mtf_data
        data_dir = self._setup_files(tmp_path)
        df4h = make_ohlcv(n=200, freq="4h")
        start = str(df4h.index[50])
        end = str(df4h.index[100])
        result = load_mtf_data(
            "TESTUSDT", ["4h"], start=start, end=end, data_dir=data_dir,
        )
        assert len(result) == 1
        assert result["4h"].index[0] >= pd.Timestamp(start)
        assert result["4h"].index[-1] <= pd.Timestamp(end)

    def test_symbol_sanitized_for_lookup(self, tmp_path):
        from core.validation.mtf import load_mtf_data
        data_dir = self._setup_files(tmp_path)
        # 非法字符被清洗后仍能命中 TESTUSDT_4h
        result = load_mtf_data("TE ST-USDT", ["4h"], data_dir=data_dir)
        assert set(result.keys()) == {"4h"}

    def test_alias_fallback_hits_60m_file_for_1h_request(self, tmp_path):
        """CARD-03 锚：只有 60m 文件时请求 1h 经别名兜底命中
        （旧实现主名直拼 miss → 空结果）。"""
        from core.validation.mtf import load_mtf_data
        _write_parquet(tmp_path, "TESTUSDT_60m", make_ohlcv(n=100, freq="60min"))
        result = load_mtf_data("TESTUSDT", ["1h"], data_dir=str(tmp_path))
        assert set(result.keys()) == {"1h"}
        assert len(result["1h"]) == 100


# ===========================================================================
# 3. engine.validate_hypothesis（主流程 L88-272 大块未执行）
# ===========================================================================

class TestValidateHypothesis:
    """engine.py 特征锚：主流程 / 空数据 / MTF 引用 / THEN 窗口。"""

    @staticmethod
    def _cond(subject, action, target, logic=None, **extra):
        d = {"subject": subject, "action": action, "target": target}
        if logic:
            d["logic"] = logic
        d.update(extra)
        return d

    def test_empty_parquet_returns_empty_result(self, tmp_path):
        from core.validation.engine import validate_hypothesis
        empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], name="timestamp"),
        )
        _write_parquet(tmp_path, "TESTUSDT_4h", empty)
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "gt", 0)],
            then_conditions=[{"action": "rise", "target": 1}],
            data_dir=str(tmp_path),
        )
        assert result.total_count == 0
        assert result.match_rate == 0

    def test_date_range_outside_data_returns_empty(self, tmp_path):
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_4h", make_ohlcv(n=100, freq="4h"))
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2025-06-01", "2025-07-01",
            when_conditions=[self._cond("close", "gt", 0)],
            then_conditions=[{"action": "rise", "target": 1}],
            data_dir=str(tmp_path),
        )
        assert result.total_count == 0

    def test_no_when_trigger_returns_empty(self, tmp_path):
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv())
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "gt", 1e9)],  # 永不成立
            then_conditions=[{"action": "rise", "target": 1}],
            data_dir=str(tmp_path),
        )
        assert result.total_count == 0
        assert result.triggers == []

    def test_trigger_only_at_last_bar_returns_empty(self, tmp_path):
        """现状记录：唯一触发在末根时前瞻窗为空，continue 后 triggers 空。"""
        from core.validation.engine import validate_hypothesis
        n = 60
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        closes = np.full(n, 100.0)
        closes[-1] = 101.0  # 只在末根越过 100.5
        df = pd.DataFrame(
            {"open": closes, "high": closes + 1, "low": closes - 1,
             "close": closes, "volume": np.full(n, 1000.0)},
            index=dates,
        )
        df.index.name = "timestamp"
        _write_parquet(tmp_path, "TESTUSDT_4h", df)
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "cross_above", 100.5)],
            then_conditions=[{"action": "rise", "target": 1}],
            data_dir=str(tmp_path),
        )
        assert result.total_count == 0

    def test_main_path_full_statistics(self, tmp_path):
        """主路径：多触发 → triggers/分布/分位/集中度/频率/极值齐备。"""
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=200))
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "lt", 95)],
            then_conditions=[{"action": "rise", "target": 3}],
            then_window=8,
            data_dir=str(tmp_path),
        )
        assert result.total_count > 0
        assert result.match_count + result.mismatch_count == result.total_count
        assert 0 <= result.match_rate <= 100
        # trigger 记录字段：id 从 1 递增、change_pct 数值、快照不含 OHLCV
        t0 = result.triggers[0]
        assert t0.id == 1
        assert isinstance(t0.change_pct, float)
        assert not ({"open", "high", "low", "close", "volume"} & set(t0.indicator_values))
        assert result.distribution, "分布不应为空"
        assert result.percentiles and {"p10", "p50", "p90", "min", "max"} <= set(result.percentiles)
        assert "p50_range" in result.concentration and "p90_range" in result.concentration
        assert result.signal_frequency and {"per_month", "per_week"} <= set(result.signal_frequency)
        assert len(result.extremes) == 1  # 现状：只记最差一笔

    def test_mtf_reference_merges_higher_tf(self, tmp_path):
        """带 timeframe 引用的条件 + MtfEvalContext → 掩码投影求值可评估。

        现状记录（0.2.14 三分派）：条件带 timeframe 键时求值走
        condition_kernel 掩码投影（ctx.load_raw 装载），merged 列只服务
        target 冒号引用与快照；无 ctx 时直接 ResearchError（见下一用例）。
        """
        from pathlib import Path
        from core.analysis.condition_kernel import MtfEvalContext
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=200))
        _write_parquet(tmp_path, "TESTUSDT_1d", make_ohlcv(n=60, freq="1d"))
        ctx = MtfEvalContext(
            data_dir=Path(tmp_path), symbol="TESTUSDT", base_tf="4h",
        )
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[
                self._cond("close", "gt", 0, timeframe="1d"),
            ],
            then_conditions=[{"action": "rise", "target": 1}],
            data_dir=str(tmp_path),
            mtf=ctx,
        )
        assert not any("MTF data loading failed" in w for w in result.warnings)
        assert result.total_count > 0

    def test_mtf_condition_without_context_raises(self, tmp_path):
        """现状记录：带 timeframe 的条件在无 MtfEvalContext 时结构化拒绝。"""
        from core.analysis.hypothesis_service import ResearchError
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=100))
        _write_parquet(tmp_path, "TESTUSDT_1d", make_ohlcv(n=40, freq="1d"))
        with pytest.raises(ResearchError, match="未构建多周期上下文"):
            validate_hypothesis(
                "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
                when_conditions=[self._cond("close", "gt", 0, timeframe="1d")],
                then_conditions=[{"action": "rise", "target": 1}],
                data_dir=str(tmp_path),
            )

    def test_mtf_failure_falls_back_with_warning(self, tmp_path):
        """现状记录：load_mtf_data 异常 → warning 降级；条件求值走
        ctx 掩码投影（不依赖 merged 列），仍可评估。"""
        from pathlib import Path
        from core.analysis.condition_kernel import MtfEvalContext
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=100))
        _write_parquet(tmp_path, "TESTUSDT_1d", make_ohlcv(n=40, freq="1d"))
        ctx = MtfEvalContext(
            data_dir=Path(tmp_path), symbol="TESTUSDT", base_tf="4h",
        )

        def _boom(*args, **kwargs):
            raise RuntimeError("mtf boom")

        with patch("core.validation.mtf.load_mtf_data", side_effect=_boom):
            result = validate_hypothesis(
                "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
                when_conditions=[self._cond("close", "gt", 0, timeframe="1d")],
                then_conditions=[{"action": "rise", "target": 1}],
                data_dir=str(tmp_path),
                mtf=ctx,
            )
        assert any("MTF data loading failed" in w for w in result.warnings)
        assert result.total_count > 0

    def test_then_condition_window_extends_max_window(self, tmp_path):
        """现状记录：cond.window > then_window 时按 cond.window 扩窗。"""
        from core.validation.engine import validate_hypothesis
        # 正弦周期 20：close<95 的触发后约 5-8 根回到高位
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=200))
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "lt", 95)],
            then_conditions=[{"action": "rise", "target": 15}],  # 大阈值
            then_window=2,  # 小默认窗
            data_dir=str(tmp_path),
        )
        # 行为锚：then_window=2 时窗口仅 2 根，15% 涨幅不可达 → 全不匹配
        assert result.match_count == 0

    def test_then_cond_with_own_window_extends_evaluation(self, tmp_path):
        """现状记录：THEN 条件自带 window 键时参与最大窗计算（L157-159）。"""
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=200))
        # 触发后 2 根内不可达 +5%，但扩窗到 12 根后正弦回摆可达
        narrow = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "lt", 95)],
            then_conditions=[{"action": "rise", "target": 5, "window": 2}],
            then_window=2,
            data_dir=str(tmp_path),
        )
        wide = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "lt", 95)],
            then_conditions=[{"action": "rise", "target": 5, "window": 12}],
            then_window=2,
            data_dir=str(tmp_path),
        )
        assert narrow.match_count > 0
        # cond.window=12 扩大评估窗：匹配数单调不减且严格增加（正弦回摆）
        assert wide.match_count > narrow.match_count

    def test_default_data_dir_resolved_when_omitted(self):
        """现状记录：data_dir 缺省解析到仓库 data/market（L88 分支）。"""
        from core.validation.engine import validate_hypothesis
        result = validate_hypothesis(
            "NOEXIST',;'SYMBOL", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "gt", 0)],
            then_conditions=[{"action": "rise", "target": 1}],
        )
        assert result.total_count == 0

    def test_unknown_timeframe_raises_research_error(self, tmp_path):
        """CARD-03 锚：未知 tf 结构化拒绝（旧实现静默空结果）。"""
        from core.analysis.hypothesis_service import ResearchError
        from core.validation.engine import validate_hypothesis
        with pytest.raises(ResearchError) as exc_info:
            validate_hypothesis(
                "TESTUSDT", "7h", "2024-01-01", "2025-01-01",
                when_conditions=[self._cond("close", "gt", 0)],
                then_conditions=[{"action": "rise", "target": 1}],
                data_dir=str(tmp_path),
            )
        assert "timeframe 未知" in str(exc_info.value)

    def test_missing_file_semantics_unchanged(self, tmp_path):
        """CARD-03 保持锚：合法 tf 缺文件 → 空 ValidationResult（非异常）。"""
        from core.validation.engine import validate_hypothesis
        result = validate_hypothesis(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "gt", 0)],
            then_conditions=[{"action": "rise", "target": 1}],
            data_dir=str(tmp_path),
        )
        assert result.total_count == 0
        assert result.triggers == []

    def test_alias_fallback_evaluates_on_60m_file(self, tmp_path):
        """CARD-03 锚：只有 60m 文件时请求 1h 可完整评估
        （旧实现主名 miss → 空结果）。"""
        from core.validation.engine import validate_hypothesis
        _write_parquet(tmp_path, "TESTUSDT_60m", _sine_ohlcv(n=200, freq="60min"))
        result = validate_hypothesis(
            "TESTUSDT", "1h", "2024-01-01", "2025-01-01",
            when_conditions=[self._cond("close", "gt", 0)],
            then_conditions=[{"action": "rise", "target": 1}],
            data_dir=str(tmp_path),
        )
        assert result.total_count > 0


# ===========================================================================
# 4. engine._evaluate_conditions（空条件 / OR 组合）
# ===========================================================================

class TestEvaluateConditions:
    """engine.py 条件组合特征锚。"""

    @staticmethod
    def _df():
        return pd.DataFrame(
            {"close": [100.0, 90.0, 110.0, 95.0],
             "volume": [1000.0, 1000.0, 1000.0, 1000.0]},
            index=pd.date_range("2024-01-01", periods=4, freq="4h"),
        )

    def test_empty_conditions_all_false(self):
        from core.validation.engine import _evaluate_conditions
        mask = _evaluate_conditions(self._df(), [])
        assert mask.tolist() == [False, False, False, False]

    def test_and_logic_intersects(self):
        from core.validation.engine import _evaluate_conditions
        df = self._df()
        conds = [
            {"subject": "close", "action": "lt", "target": 105},
            {"subject": "close", "action": "gt", "target": 92, "logic": "AND"},
        ]
        mask = _evaluate_conditions(df, conds)
        assert mask.tolist() == [True, False, False, True]

    def test_or_logic_unions(self):
        from core.validation.engine import _evaluate_conditions
        df = self._df()
        conds = [
            {"subject": "close", "action": "lt", "target": 92},
            {"subject": "close", "action": "gt", "target": 105, "logic": "OR"},
        ]
        mask = _evaluate_conditions(df, conds)
        assert mask.tolist() == [False, True, True, False]


# ===========================================================================
# 5. engine._evaluate_single_condition（解析三分支 + 动作派发）
# ===========================================================================

class TestEvaluateSingleCondition:
    """engine.py 单条件求值特征锚：解析失败降级与动作派发。"""

    @staticmethod
    def _df():
        return pd.DataFrame(
            {"close": [100.0, 90.0, 110.0, 95.0],
             "ema_fast": [99.0, 91.0, 109.0, 96.0],
             "ema_slow": [100.0, 90.0, 105.0, 97.0]},
            index=pd.date_range("2024-01-01", periods=4, freq="4h"),
        )

    def test_unknown_subject_warns_and_all_false(self):
        from core.validation.engine import _evaluate_single_condition
        warnings: list = []
        mask = _evaluate_single_condition(
            self._df(), {"subject": "no_such_col", "action": "gt", "target": 1},
            warnings,
        )
        assert mask.tolist() == [False] * 4
        assert any("not found in data columns" in w for w in warnings)

    def test_unresolvable_target_warns_and_all_false(self):
        from core.validation.engine import _evaluate_single_condition
        warnings: list = []
        mask = _evaluate_single_condition(
            self._df(), {"subject": "close", "action": "gt", "target": "not_a_col_x"},
            warnings,
        )
        assert mask.tolist() == [False] * 4
        assert any("could not be resolved" in w for w in warnings)

    def test_unknown_action_warns_and_all_false(self):
        from core.validation.engine import _evaluate_single_condition
        warnings: list = []
        mask = _evaluate_single_condition(
            self._df(), {"subject": "close", "action": "teleport", "target": 100},
            warnings,
        )
        assert mask.tolist() == [False] * 4
        assert any("not a recognized action" in w for w in warnings)

    def test_cross_above_series_detects_cross(self):
        from core.validation.engine import _evaluate_single_condition
        df = self._df()
        # ema_fast 与 ema_slow：bar0 fast<slow、bar1 fast>slow → 穿越@1
        cond = {"subject": "ema_fast", "action": "cross_above_series", "target": "ema_slow"}
        mask = _evaluate_single_condition(df, cond)
        assert mask.tolist() == [False, True, False, False]

    def test_cross_below_series_detects_cross(self):
        from core.validation.engine import _evaluate_single_condition
        df = self._df()
        # bar2 fast=109>slow=105、bar3 fast=96<slow=97 → 下穿@3
        cond = {"subject": "ema_fast", "action": "cross_below_series", "target": "ema_slow"}
        mask = _evaluate_single_condition(df, cond)
        assert mask.tolist() == [False, False, False, True]

    def test_cross_above_series_missing_target_all_false(self):
        """现状记录：target 数值串可过外层解析，但动作内部按 subject
        解析不到列时全 False（L493-495）。"""
        from core.validation.engine import _evaluate_single_condition
        cond = {"subject": "ema_fast", "action": "cross_above_series", "target": "95"}
        mask = _evaluate_single_condition(self._df(), cond)
        assert mask.tolist() == [False] * 4

    def test_cross_below_series_missing_target_all_false(self):
        from core.validation.engine import _evaluate_single_condition
        cond = {"subject": "ema_fast", "action": "cross_below_series", "target": "95"}
        mask = _evaluate_single_condition(self._df(), cond)
        assert mask.tolist() == [False] * 4

    def test_cross_below_with_series_target(self):
        """现状记录：breakdown/cross_below 对 Series 型 target 的下穿判定。"""
        from core.validation.engine import _evaluate_single_condition
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
        from core.validation.engine import _evaluate_single_condition
        df = self._df()
        cond = {
            "subject": "close", "action": "lookback_any",
            "inner_action": inner_action, "target": 95, "window": 2,
        }
        mask = _evaluate_single_condition(df, cond)
        assert mask.tolist() == expected

    def test_lookback_any_default_price_above(self):
        """现状记录：非比较型 inner_action 走 close>subject 默认语义。"""
        from core.validation.engine import _evaluate_single_condition
        df = self._df()
        cond = {
            "subject": "ema_slow", "action": "lookback_any",
            "inner_action": "price_above", "target": 0, "window": 2,
        }
        mask = _evaluate_single_condition(df, cond)
        # close>ema_slow 逐 bar [F,F,T,F]，滑移窗 any → [F,F,T,T]
        assert mask.tolist() == [False, False, True, True]

    def test_spike_and_shrink_actions(self):
        from core.validation.engine import _evaluate_single_condition
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
        from core.validation.engine import _evaluate_single_condition
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
        from core.validation.engine import _evaluate_single_condition
        from core.analysis.hypothesis_service import ResearchError
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
        from core.validation.engine import _evaluate_single_condition
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
        from core.validation.engine import _evaluate_single_condition
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
# 6. engine._resolve_target / _resolve_subject（跨周期与 registry 解析）
# ===========================================================================

class TestResolve:
    """engine.py 解析层特征锚。"""

    @staticmethod
    def _df():
        return pd.DataFrame(
            {"close": [1.0, 2.0], "ema_20_4h": [10.0, 20.0],
             "my_close_price": [5.0, 6.0]},
            index=pd.date_range("2024-01-01", periods=2, freq="4h"),
        )

    def test_resolve_target_numeric_passthrough(self):
        from core.validation.engine import _resolve_target
        assert _resolve_target(self._df(), 3.5) == 3.5
        assert _resolve_target(self._df(), "2.5") == 2.5

    def test_resolve_target_empty_returns_none(self):
        from core.validation.engine import _resolve_target
        assert _resolve_target(self._df(), "") is None
        assert _resolve_target(self._df(), None) is None

    def test_resolve_target_cross_tf_direct_suffix(self):
        from core.validation.engine import _resolve_target
        series = _resolve_target(self._df(), "4h:ema_20")
        assert series is not None
        assert series.tolist() == [10.0, 20.0]

    def test_resolve_target_cross_tf_exact_only(self):
        """0.3.2 卡3c 后行为：跨周期形制仅精确列名命中（"4h:ema_20"
        只查 ema_20_4h），后缀模糊兜底已删——非精确命名返回 None
        （原锚 test_resolve_target_cross_tf_fuzzy_suffix 随缺陷裁定改写）。"""
        from core.validation.engine import _resolve_target
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
        from core.validation.engine import _resolve_target
        series = _resolve_target(self._df(), "close")
        assert series.tolist() == [1.0, 2.0]

    def test_resolve_target_unknown_column_none(self):
        """0.3.2 卡3c 后行为：非精确列名 → None（归一化子串兜底已删，
        原锚 test_resolve_target_fuzzy_column 随缺陷裁定改写）。"""
        from core.validation.engine import _resolve_target
        assert _resolve_target(self._df(), "closeprice") is None

    def test_resolve_subject_registry_prefix(self):
        """现状记录：subject 为注册表 output_field 名时还原默认参数列。"""
        from core.validation.engine import _resolve_subject
        df = pd.DataFrame(
            {"rsi_14": [55.0, 45.0], "close": [1.0, 2.0]},
            index=pd.date_range("2024-01-01", periods=2, freq="4h"),
        )
        series = _resolve_subject(df, "rsi")
        assert series is not None
        assert series.tolist() == [55.0, 45.0]

    def test_resolve_subject_alias_map(self):
        from core.validation.engine import _resolve_subject
        df = pd.DataFrame({"close": [1.0, 2.0]}, index=[0, 1])
        assert _resolve_subject(df, "price").tolist() == [1.0, 2.0]

    def test_resolve_subject_missing_returns_none(self):
        from core.validation.engine import _resolve_subject
        df = pd.DataFrame({"close": [1.0]}, index=[0])
        assert _resolve_subject(df, "no_such") is None

    def test_resolve_subject_registry_hit_column_missing_returns_none(self):
        """现状记录：注册表前缀名命中但默认参数列不在 df → None（L387-389）。"""
        from core.validation.engine import _resolve_subject
        df = pd.DataFrame({"close": [1.0]}, index=[0])  # 无 rsi_14 列
        assert _resolve_subject(df, "rsi") is None


# ===========================================================================
# 7. engine._check_then_conditions 补锚（ge/le 精确边界）
# ===========================================================================

class TestCheckThenBoundary:
    """engine.py THEN 条件边界补锚。"""

    @staticmethod
    def _window(prices):
        return pd.DataFrame(
            {"close": prices},
            index=pd.date_range("2024-01-01", periods=len(prices), freq="4h"),
        )

    def test_ge_matches_exact_boundary(self):
        from core.validation.engine import _check_then_conditions
        window = self._window([100.0, 105.0])  # 恰 +5%
        matched = _check_then_conditions(window, [{"action": "ge", "target": 5}], 100.0)
        assert matched is True

    def test_le_matches_exact_boundary(self):
        from core.validation.engine import _check_then_conditions
        window = self._window([100.0, 95.0])  # 恰 -5%
        matched = _check_then_conditions(window, [{"action": "le", "target": -5}], 100.0)
        assert matched is True

    def test_gt_rejects_exact_boundary(self):
        from core.validation.engine import _check_then_conditions
        window = self._window([100.0, 105.0])
        matched = _check_then_conditions(window, [{"action": "gt", "target": 5}], 100.0)
        assert matched is False

    def test_cond_window_zero_structurally_rejected(self):
        """CARD-02 转正：cond.window=0 经 _cond_window 结构化拒绝
        （原行为：裁空窗 → continue 落默认分支，静默跳过条件）。"""
        from core.validation.engine import _check_then_conditions
        from core.analysis.hypothesis_service import ResearchError
        window = self._window([100.0, 108.0])
        with pytest.raises(ResearchError) as ei:
            _check_then_conditions(
                window, [{"action": "gt", "target": 5, "window": 0}],
                100.0, 0, 8,
            )
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["param"] == "condition.window"


# ===========================================================================
# 8. rule_engine.evaluate_rules（主流程 L88-202 未执行）
# ===========================================================================

class TestEvaluateRules:
    """rule_engine.py 特征锚：主流程 / 空数据 / MTF / 组合语义。"""

    def test_file_not_found_returns_warning(self, tmp_path):
        from core.validation.rule_engine import evaluate_rules
        result = evaluate_rules(
            "NOEXIST", "4h", "2024-01-01", "2025-01-01",
            entry_conditions=[{"subject": "close", "action": "lt", "target": 95}],
            exit_conditions=[{"subject": "close", "action": "gt", "target": 105}],
            data_dir=str(tmp_path),
        )
        assert result.total_trades == 0
        assert any("Data file not found" in w for w in result.warnings)
        assert result.win_rate is None  # 零交易为缺失语义

    def test_empty_parquet_returns_warning(self, tmp_path):
        from core.validation.rule_engine import evaluate_rules
        empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], name="timestamp"),
        )
        _write_parquet(tmp_path, "TESTUSDT_4h", empty)
        result = evaluate_rules(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            entry_conditions=[{"subject": "close", "action": "lt", "target": 95}],
            exit_conditions=[{"subject": "close", "action": "gt", "target": 105}],
            data_dir=str(tmp_path),
        )
        assert any("Loaded data is empty" in w for w in result.warnings)

    def test_date_range_empty_returns_warning(self, tmp_path):
        from core.validation.rule_engine import evaluate_rules
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=100))
        result = evaluate_rules(
            "TESTUSDT", "4h", "2025-06-01", "2025-07-01",
            entry_conditions=[{"subject": "close", "action": "lt", "target": 95}],
            exit_conditions=[{"subject": "close", "action": "gt", "target": 105}],
            data_dir=str(tmp_path),
        )
        assert any("No data in requested date range" in w for w in result.warnings)

    def test_unknown_timeframe_raises_research_error(self, tmp_path):
        """CARD-03 锚：未知 tf 结构化拒绝（旧实现静默空结果 + warning）。"""
        from core.analysis.hypothesis_service import ResearchError
        from core.validation.rule_engine import evaluate_rules
        with pytest.raises(ResearchError) as exc_info:
            evaluate_rules(
                "TESTUSDT", "7h", "2024-01-01", "2025-01-01",
                entry_conditions=[{"subject": "close", "action": "lt", "target": 95}],
                exit_conditions=[{"subject": "close", "action": "gt", "target": 105}],
                data_dir=str(tmp_path),
            )
        assert "timeframe 未知" in str(exc_info.value)

    def test_missing_file_warning_semantics_unchanged(self, tmp_path):
        """CARD-03 保持锚：合法 tf 缺文件 → warning + 空结果（非异常）。"""
        from core.validation.rule_engine import evaluate_rules
        result = evaluate_rules(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            entry_conditions=[{"subject": "close", "action": "lt", "target": 95}],
            exit_conditions=[{"subject": "close", "action": "gt", "target": 105}],
            data_dir=str(tmp_path),
        )
        assert any("Data file not found: TESTUSDT_4h.parquet" in w
                   for w in result.warnings)
        assert result.total_trades == 0

    def test_main_path_pairs_trades_and_stats(self, tmp_path):
        from core.validation.rule_engine import evaluate_rules
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=240))
        result = evaluate_rules(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            entry_conditions=[{"subject": "close", "action": "lt", "target": 92}],
            exit_conditions=[{"subject": "close", "action": "gt", "target": 108}],
            data_dir=str(tmp_path),
        )
        assert not result.warnings
        assert result.total_trades >= 1
        assert len(result.buy_signals) >= result.total_trades
        assert len(result.sell_signals) >= result.total_trades
        assert result.win_trades + result.loss_trades == result.total_trades
        # 现状口径：win_rate 为小数（round 4）
        assert 0.0 <= result.win_rate <= 1.0
        # 交易配对：exit 严格晚于 entry
        for t in result.trades:
            assert t.exit_time > t.entry_time
            assert t.is_win == (t.return_pct > 0)

    def test_mtf_reference_success_and_failure(self, tmp_path):
        """现状记录：带 timeframe 的条件须配 MtfEvalContext（掩码投影路径）。"""
        from pathlib import Path
        from core.analysis.condition_kernel import MtfEvalContext
        from core.validation.rule_engine import evaluate_rules
        _write_parquet(tmp_path, "TESTUSDT_4h", _sine_ohlcv(n=120))
        _write_parquet(tmp_path, "TESTUSDT_1d", make_ohlcv(n=40, freq="1d"))
        ctx = MtfEvalContext(
            data_dir=Path(tmp_path), symbol="TESTUSDT", base_tf="4h",
        )
        ok = evaluate_rules(
            "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
            entry_conditions=[
                {"subject": "close", "action": "gt", "target": 0, "timeframe": "1d"},
            ],
            exit_conditions=[{"subject": "close", "action": "gt", "target": 108}],
            data_dir=str(tmp_path),
            mtf=ctx,
        )
        assert not any("MTF data loading failed" in w for w in ok.warnings)
        assert ok.total_trades >= 1

        def _boom(*args, **kwargs):
            raise RuntimeError("mtf boom")

        with patch("core.validation.rule_engine.load_mtf_data", side_effect=_boom):
            degraded = evaluate_rules(
                "TESTUSDT", "4h", "2024-01-01", "2025-01-01",
                entry_conditions=[
                    {"subject": "close", "action": "gt", "target": 0, "timeframe": "1d"},
                ],
                exit_conditions=[{"subject": "close", "action": "gt", "target": 108}],
                data_dir=str(tmp_path),
                mtf=ctx,
            )
        assert any("MTF data loading failed" in w for w in degraded.warnings)


# ===========================================================================
# 9. _evaluate_conditions 共享组合实现（CARD-01 后 rule_engine 退役私有副本）
# ===========================================================================

class TestEvaluateConditionsShared:
    """engine._evaluate_conditions 共享实现特征锚：每条件自带 logic 逐条
    累积、首条件 logic 忽略、IF≡AND。CARD-01 前这些锚指向
    rule_engine._evaluate_rule_conditions（已退役删除），锚定语义原样
    迁移到共享实现。"""

    @staticmethod
    def _df():
        return pd.DataFrame(
            {"close": [100.0, 90.0, 110.0, 95.0]},
            index=pd.date_range("2024-01-01", periods=4, freq="4h"),
        )

    def test_empty_conditions_all_false(self):
        from core.validation.engine import _evaluate_conditions
        mask = _evaluate_conditions(self._df(), [])
        assert mask.tolist() == [False] * 4

    def test_or_unions_masks(self):
        from core.validation.engine import _evaluate_conditions
        conds = [
            {"subject": "close", "action": "lt", "target": 92},
            {"subject": "close", "action": "gt", "target": 105, "logic": "OR"},
        ]
        mask = _evaluate_conditions(self._df(), conds)
        assert mask.tolist() == [False, True, True, False]

    def test_if_treated_as_and(self):
        """现状记录：IF 与 AND 在布尔层等价。"""
        from core.validation.engine import _evaluate_conditions
        conds = [
            {"subject": "close", "action": "lt", "target": 105},
            {"subject": "close", "action": "gt", "target": 92, "logic": "IF"},
        ]
        mask = _evaluate_conditions(self._df(), conds)
        assert mask.tolist() == [True, False, False, True]

    def test_first_condition_logic_ignored(self):
        """现状记录：首条件的 logic 字段无组合对象，被忽略。"""
        from core.validation.engine import _evaluate_conditions
        conds = [
            {"subject": "close", "action": "lt", "target": 105, "logic": "OR"},
            {"subject": "close", "action": "gt", "target": 92, "logic": "OR"},
        ]
        mask = _evaluate_conditions(self._df(), conds)
        # lt105=[T,T,F,T] OR gt92=[T,F,T,T] → [T,T,T,T]
        assert mask.tolist() == [True, True, True, True]

    @staticmethod
    def _mixed_df():
        # bar2(106) 为缺陷点锚：A真 B假 C假
        return pd.DataFrame(
            {"close": [100.0, 90.0, 106.0, 95.0]},
            index=pd.date_range("2024-01-01", periods=4, freq="4h"),
        )

    def test_three_conditions_and_and_or_semantics(self):
        """CARD-01 锚：三条件 [A, B(AND), C(OR)] = A&B|C。

        bar2=106：A(>92)=T、B(<105)=F、C(>108)=F——旧实现（组合只用
        最后条件 logic）在此 bar 错为 True，逐条件语义正确为 False。
        """
        from core.validation.engine import _evaluate_conditions
        df = self._mixed_df()
        conds = [
            {"subject": "close", "action": "gt", "target": 92},          # A
            {"subject": "close", "action": "lt", "target": 105, "logic": "AND"},  # B
            {"subject": "close", "action": "gt", "target": 108, "logic": "OR"},   # C
        ]
        mask = _evaluate_conditions(df, conds)
        # A=[T,F,T,T] B=[T,T,F,T] C=[F,F,F,F] → A&B|C = [T,F,F,T]
        assert mask.tolist() == [True, False, False, True]

    def test_three_conditions_if_equivalent_in_mixed_logic(self):
        """CARD-01 锚：三条件混合中 IF 仍与 AND 等价（A & B(IF) | C）。"""
        from core.validation.engine import _evaluate_conditions
        df = self._mixed_df()
        conds = [
            {"subject": "close", "action": "gt", "target": 92},          # A
            {"subject": "close", "action": "lt", "target": 105, "logic": "IF"},   # B
            {"subject": "close", "action": "gt", "target": 108, "logic": "OR"},   # C
        ]
        mask = _evaluate_conditions(df, conds)
        assert mask.tolist() == [True, False, False, True]

    def test_rule_engine_and_engine_share_combination_semantics(self):
        """CARD-01 结构锚：rule_engine 进出场求值走共享 _evaluate_conditions。"""
        import core.validation.rule_engine as rule_engine_mod
        used = {
            rule_engine_mod._evaluate_conditions is not None,
        }
        assert all(used)
        assert not hasattr(rule_engine_mod, "_evaluate_rule_conditions")


# ===========================================================================
# 10. CARD-05：_lookback_condition window 坏值/零/负结构化拒绝
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
        from core.analysis.hypothesis_service import ResearchError
        from core.validation.engine import _evaluate_single_condition
        df = pd.DataFrame(
            {"close": [100.0, 90.0, 110.0]},
            index=pd.date_range("2024-01-01", periods=3, freq="4h"),
        )
        with pytest.raises(ResearchError, match="condition.window"):
            _evaluate_single_condition(df, self._cond(bad_window))

    def test_valid_window_still_works(self):
        from core.validation.engine import _evaluate_single_condition
        df = pd.DataFrame(
            {"close": [100.0, 90.0, 110.0]},
            index=pd.date_range("2024-01-01", periods=3, freq="4h"),
        )
        mask = _evaluate_single_condition(df, self._cond(2))
        assert len(mask) == 3


class TestStage4BlindSpotFill:
    """阶段四盲区消解补锚：判定路径上被前置拦截掩盖的兜底分支。"""

    def test_then_window_zero_rise_form_rejected(self):
        """CARD-02 转正：window=0 + 坏 target（rise 形）→ _cond_window
        结构化拒绝（原行为：裁空窗跳主判定，默认分支 float 失败静默
        归 0 → rise 8% >= 0 判 True 的失真匹配）。"""
        from core.validation.engine import _check_then_conditions
        from core.analysis.hypothesis_service import ResearchError
        window = pd.DataFrame(
            {"close": [100.0, 108.0]},
            index=pd.date_range("2024-01-01", periods=2, freq="4h"),
        )
        with pytest.raises(ResearchError) as ei:
            _check_then_conditions(
                window, [{"action": "rise", "target": "abc", "window": 0}],
                100.0, 0, 8,
            )
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["param"] == "condition.window"

    def test_spike_series_target_falls_back_default_multiplier(self):
        """现状记录：target 为列（Series）时 float() 抛 TypeError →
        multiplier 按 2.0 兜底（数值串/数值不会到达此分支）。"""
        from core.validation.engine import _evaluate_single_condition
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
        from core.validation.engine import _evaluate_single_condition
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
