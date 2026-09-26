"""分析模板集（add-ai-research-mcp 2.x + add-screen-patterns）：六类参数化模板。

模板是纯计算（DataFrame 输入 → AnalysisResult 输出），不做 IO、
不感知 HTTP；数据装载由调用方（路由/编排层）完成。全部出口经
core.analysis.result.build_result（三要素强制：样本量/置信/数据区间）。
"""
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.correlation_scan import correlation_scan
from core.analysis.templates.distribution_compare import distribution_compare
from core.analysis.templates.event_study import event_study
from core.analysis.templates.pattern_mining import similar_at_time
from core.analysis.templates.param_sweep import param_sweep
from core.analysis.templates.regime_split import regime_split
from core.analysis.templates.screen_patterns import screen_patterns

__all__ = [
    "condition_stats",
    "correlation_scan",
    "distribution_compare",
    "event_study",
    "similar_at_time",
    "screen_patterns",
]
