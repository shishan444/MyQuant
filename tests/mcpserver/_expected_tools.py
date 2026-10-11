"""研究档期望工具清单（fix-hint-template-list 1.3）。

单处静态权威：test_server / test_stdio_l3 / test_http_l3 三处工具集
断言的共享期望源（替代三处散落的 len(names) == N 魔法数字——0.2.6/
0.2.7/0.2.8 三版各踩一次人肉同步）。集合相等断言使漏同步必红且 diff
直接显示缺/多的工具名。

清单内容取自 build_server(profile="research") 真实输出（2026-09-26，
main 39949a7）。增减工具只改本常量一处；与 mcpserver/server.py 的
注册集失联由三处集合断言拦截。
"""
from __future__ import annotations

EXPECTED_RESEARCH_TOOLS: frozenset[str] = frozenset({
    "get_analysis_task",
    "get_hypothesis",
    "get_market_snapshot",
    "get_research_log",
    "get_strategy",
    "get_validation_report",
    "inspect_cases",
    "list_data_sources",
    "list_evolution_tasks",
    "list_feature_requests",
    "list_hypotheses",
    "list_indicators",
    "list_signal_columns",
    "list_strategies",
    "poll_evolution_task",
    "query_indicator",
    "run_staged_validation",
    "register_formula_plugin",
    "register_hypothesis",
    "register_model_plugin",
    "run_analysis",
    "run_backtest",
    "run_isolated_validation",
    "run_random_window_test",
    "submit_feature_request",
    "transition_hypothesis",
    "translate_hypothesis_to_strategy",
    "trigger_evolution",
    "union_balance",
})
