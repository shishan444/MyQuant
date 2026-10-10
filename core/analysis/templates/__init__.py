"""分析模板集（add-ai-research-mcp 2.x + add-screen-patterns）：十类参数化模板（0.4.1 增 segment_narrative）。

模板是纯计算（DataFrame 输入 → AnalysisResult 输出），不做 IO、
不感知 HTTP；数据装载由调用方（路由/编排层）完成。全部出口经
core.analysis.result.build_result（三要素强制：样本量/置信/数据区间）。
例外：precheck_hypothesis（0.3.4）自装载——两维 warmup 前扩与插件列
enrich 须与预算计算同源（同一集合喂装载/求值/预算三处），拆到路由层
会复制引用收集链；IO 面收敛在模板壳内，计算面仍纯（compute 层无 IO）。
"""
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.correlation_scan import correlation_scan
from core.analysis.templates.distribution_compare import distribution_compare
from core.analysis.templates.event_study import event_study
from core.analysis.templates.pattern_mining import similar_at_time
from core.analysis.templates.param_sweep import param_sweep
from core.analysis.templates.precheck_hypothesis import precheck_hypothesis
from core.analysis.templates.regime_split import regime_split
from core.analysis.templates.screen_patterns import screen_patterns
from core.analysis.templates.segment_narrative import segment_narrative

__all__ = [
    "condition_stats",
    "correlation_scan",
    "distribution_compare",
    "event_study",
    "similar_at_time",
    "param_sweep",
    "precheck_hypothesis",
    "regime_split",
    "screen_patterns",
    "segment_narrative",
]
