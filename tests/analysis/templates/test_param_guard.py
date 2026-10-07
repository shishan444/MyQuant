"""模板入参守卫测试（clear-analysis-tds tasks 2.1/2.2 验证锚）。

两层分层（tasks 2.1 划界）：TestGuardPure=param_guard 纯函数单测；
TestTemplateEntry=五模板函数入口契约（四补齐+condition_stats——
直调，422 在计算前拦截，合法含端跑通）。specs 两场景（数值参数非法
结构化拒绝/枚举参数封闭拒绝）+评估补三例（min_samples=0 放行/
transform 三枚举合法/无上界大值合法）的对账锚。
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.param_guard import (
    _require_enum,
    _require_int_range,
)

INVALID_FORMS = [True, False, "24", 2.5, None, -1]


class TestGuardPure:
    """2.1：guard 纯函数单测（合法/各非法形/lo=0 放行/无界侧/枚举）。"""

    def test_int_range_legal(self) -> None:
        _require_int_range(5, name="x", lo=1, hi=10)       # 界内
        _require_int_range(1, name="x", lo=1, hi=10)       # 含下端
        _require_int_range(10, name="x", lo=1, hi=10)      # 含上端

    @pytest.mark.parametrize("bad", INVALID_FORMS)
    def test_int_range_illegal(self, bad) -> None:
        with pytest.raises(ResearchError) as ei:
            _require_int_range(bad, name="x", lo=1, hi=10)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "x"

    def test_int_range_upper_bound(self) -> None:
        with pytest.raises(ResearchError):
            _require_int_range(11, name="x", lo=1, hi=10)

    def test_lo_zero_allows_zero(self) -> None:
        """lo=0 放行 0（阈值类禁用检查的既有合法语义）。"""
        _require_int_range(0, name="min_samples", lo=0)
        with pytest.raises(ResearchError):   # 负数仍拒
            _require_int_range(-1, name="min_samples", lo=0)

    def test_none_bound_is_unbounded(self) -> None:
        """hi=None=该侧无界——大值放行（二次评估补例：防误实现 hi=0）。"""
        _require_int_range(999_999_999, name="min_samples", lo=0)
        _require_int_range(10**12, name="n_neighbors", lo=1)

    def test_enum_three_legal(self) -> None:
        for v in ("returns", "log_returns", "raw"):
            _require_enum(v, name="transform",
                          choices=("returns", "log_returns", "raw"))

    def test_enum_illegal(self) -> None:
        with pytest.raises(ResearchError) as ei:
            _require_enum("return", name="transform",
                          choices=("returns", "log_returns", "raw"))
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "returns" in ei.value.detail["legal_range"]


class TestTemplateEntry:
    """模板函数入口契约（422 计算前拦截+合法含端跑通）——五段：
    correlation_scan/event_study/distribution_compare/pattern_mining
    （TD-2 四补齐）+condition_stats（CARD-01 min_samples 段）。"""

    @pytest.fixture()
    def df(self):
        import pandas as pd
        idx = pd.date_range("2024-01-01", periods=60, freq="1h",
                            tz="UTC", name="timestamp")
        close = 100.0 + __import__("numpy").arange(60, dtype=float)
        return __import__("pandas").DataFrame(
            {"open": close, "high": close + 1, "low": close - 1,
             "close": close, "volume": 100.0}, index=idx)

    RANGES = [{"start": "2024-01-01T00:00:00+00:00",
               "end": "2024-01-03T12:00:00+00:00"}]

    # ---- correlation_scan ----

    def test_correlation_scan_horizon_bounds(self, df) -> None:
        from core.analysis.templates.correlation_scan import correlation_scan
        with pytest.raises(ResearchError) as ei:
            correlation_scan(df, signal_column="close", horizon="24",
                             data_ranges=self.RANGES)
        assert ei.value.detail["legal_range"] == f"1..{len(df) - 1}"
        correlation_scan(df, signal_column="close", horizon=5,
                         min_samples=0,
                         data_ranges=self.RANGES)     # 界内合法完整跑通

    @pytest.mark.parametrize("param", ["horizon", "max_lag"])
    @pytest.mark.parametrize("bad", [True, 2.5, None, 0, -3])
    def test_correlation_scan_illegal(self, df, param, bad) -> None:
        from core.analysis.templates.correlation_scan import correlation_scan
        kwargs = dict(signal_column="close", data_ranges=self.RANGES)
        kwargs[param] = bad
        with pytest.raises(ResearchError) as ei:
            correlation_scan(df, **kwargs)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_correlation_scan_min_samples_zero_allowed(self, df) -> None:
        """评估补例①：min_samples=0=禁用小样本检查（放行）。"""
        from core.analysis.templates.correlation_scan import correlation_scan
        correlation_scan(df, signal_column="close", min_samples=0,
                         data_ranges=self.RANGES)      # 不抛小样本拒绝

    def test_correlation_scan_min_samples_large_allowed(self, df) -> None:
        """二次评估补例：无上界参数大值合法（hi=None 端到端）。"""
        from core.analysis.templates.correlation_scan import correlation_scan
        with pytest.raises(ResearchError) as ei:      # 大值过守卫、落小样本拒绝
            correlation_scan(df, signal_column="close", min_samples=10**9,
                             data_ranges=self.RANGES)
        assert ei.value.detail.get("min_samples") == 10**9  # 守卫未拦的证据

    def test_correlation_scan_zero_variance_signal(self, df) -> None:
        """零方差信号列（volume 常数）：corr=NaN 逐 lag 跳过 → 空扫描
        小样本拒绝。min_samples=1 且配对数远超之——构造保证唯一路径
        是 NaN continue 而非小样本 continue。"""
        from core.analysis.templates.correlation_scan import correlation_scan
        with pytest.raises(ResearchError) as ei:
            correlation_scan(df, signal_column="volume", min_samples=1,
                             max_lag=2, data_ranges=self.RANGES)
        assert ei.value.detail["min_samples"] == 1

    # ---- event_study ----

    COND = {"subject": "close", "action": "gt", "target": "0"}

    def test_event_study_window_bounds(self, df) -> None:
        from core.analysis.templates.event_study import event_study
        with pytest.raises(ResearchError) as ei:
            event_study(df, condition=self.COND, window=0,
                        data_ranges=self.RANGES)
        assert ei.value.detail["legal_range"] == f"1..{len(df) - 1}"
        event_study(df, condition=self.COND, window=5,
                    min_events=0, data_ranges=self.RANGES)

    @pytest.mark.parametrize("bad", [True, "12", 2.5, None, -1])
    def test_event_study_min_events_illegal(self, df, bad) -> None:
        from core.analysis.templates.event_study import event_study
        with pytest.raises(ResearchError):
            event_study(df, condition=self.COND, min_events=bad,
                        data_ranges=self.RANGES)

    def test_event_study_min_events_zero_allowed(self, df) -> None:
        from core.analysis.templates.event_study import event_study
        event_study(df, condition=self.COND, min_events=0,
                    data_ranges=self.RANGES)

    # ---- distribution_compare ----

    GA = {"start": "2024-01-01T00:00:00+00:00",
          "end": "2024-01-01T20:00:00+00:00"}
    GB = {"start": "2024-01-02T00:00:00+00:00",
          "end": "2024-01-02T20:00:00+00:00"}

    @pytest.mark.parametrize("bad", ["return", "", "RETURNS", None, 24])
    def test_transform_enum_closed(self, df, bad) -> None:
        """枚举封闭拒绝（静默 raw 消灭——specs 场景二）。"""
        from core.analysis.templates.distribution_compare import (
            distribution_compare,
        )
        with pytest.raises(ResearchError) as ei:
            distribution_compare(df, column="close", transform=bad,
                                 group_a=self.GA, group_b=self.GB,
                                 data_ranges=self.RANGES)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    @pytest.mark.parametrize("legal", ["returns", "log_returns", "raw"])
    def test_transform_three_legal(self, df, legal) -> None:
        """评估补例②：三枚举全合法跑通。"""
        from core.analysis.templates.distribution_compare import (
            distribution_compare,
        )
        distribution_compare(df, column="close", transform=legal,
                             min_samples=0,          # 短分组+0 语义双重复证
                             group_a=self.GA, group_b=self.GB,
                             data_ranges=self.RANGES)

    def test_distribution_min_samples_guard(self, df) -> None:
        from core.analysis.templates.distribution_compare import (
            distribution_compare,
        )
        with pytest.raises(ResearchError) as ei:
            distribution_compare(df, column="close", min_samples=True,
                                 group_a=self.GA, group_b=self.GB,
                                 data_ranges=self.RANGES)
        assert ei.value.detail["legal_range"] == "0..inf"

    # ---- pattern_mining（恒 heavy——函数级直调证校验位，引擎不触达）----

    def test_pattern_mining_n_neighbors_semantics(self, df) -> None:
        from core.analysis.templates.pattern_mining import similar_at_time
        with pytest.raises(ResearchError) as ei:
            similar_at_time(df, as_of="2024-01-02T00:00:00+00:00",
                            n_neighbors=0, data_ranges=self.RANGES)
        assert ei.value.detail["legal_range"] == "1..inf"   # 数量语义 lo=1

    def test_pattern_mining_horizon_bound(self, df) -> None:
        from core.analysis.templates.pattern_mining import similar_at_time
        with pytest.raises(ResearchError) as ei:
            similar_at_time(df, as_of="2024-01-02T00:00:00+00:00",
                            horizon=len(df), data_ranges=self.RANGES)
        assert ei.value.detail["legal_range"] == f"1..{len(df) - 1}"

    @pytest.mark.parametrize("bad", ["50", 2.5, True, None])
    def test_pattern_mining_non_int_forms(self, df, bad) -> None:
        """恒 heavy 模板同拒非整数形/bool（specs 场景一形集在
        pattern_mining 域的代表锚——越界形已由上两例锚定）。"""
        from core.analysis.templates.pattern_mining import similar_at_time
        with pytest.raises(ResearchError) as ei:
            similar_at_time(df, as_of="2024-01-02T00:00:00+00:00",
                            n_neighbors=bad, data_ranges=self.RANGES)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "n_neighbors"

    # ---- condition_stats（CARD-01：min_samples 裸透传补守卫）----

    CSI = {"indicator": "RSI", "params": {"period": 14},
           "condition": {"subject": "close", "action": "gt",
                         "target": "0"}}

    @pytest.mark.parametrize("bad", ["30", 2.5, True, -1])
    def test_condition_stats_min_samples_illegal(self, df, bad) -> None:
        """阈值类四非法形（str/float/bool/负）→ 422 附 legal_range="0..inf"
        精确锚（specs 阈值语义：lo=0 无上界）。"""
        from core.analysis.templates.condition_stats import condition_stats
        with pytest.raises(ResearchError) as ei:
            condition_stats(df, min_samples=bad, data_ranges=self.RANGES,
                            **self.CSI)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "min_samples"
        assert ei.value.detail["legal_range"] == "0..inf"

    def test_condition_stats_min_samples_zero_disables_check(self, df) -> None:
        """0=禁用小样本检查（specs 阈值类既有合法语义）——对照组证可红：
        同一小样本输入（valid=4 < 30），0 放行出报告、默认档 30 拒。"""
        from core.analysis.templates.condition_stats import condition_stats
        rare = dict(self.CSI, condition={"subject": "close",
                                         "action": "gt", "target": "150"})
        r = condition_stats(df, min_samples=0, forward_bars=5,
                            data_ranges=self.RANGES, **rare)
        assert r.sample_size == 4            # 0 档照常出报告（有效前向信号 4）
        with pytest.raises(ResearchError, match="小样本拒绝"):
            # 默认档 30：同输入小样本拒绝（build_result 既有通道）
            condition_stats(df, forward_bars=5,
                            data_ranges=self.RANGES, **rare)

    def test_condition_stats_min_samples_default_legal(self, df) -> None:
        """默认档 30 合法（宽松条件全命中）——守卫不误伤既有默认调用。"""
        from core.analysis.templates.condition_stats import condition_stats
        r = condition_stats(df, forward_bars=5, data_ranges=self.RANGES,
                            **self.CSI)
        assert r.sample_size > 0
