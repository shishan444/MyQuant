"""MCP 工具注册（add-ai-research-mcp 4.2/4.3/4.4）。

两档静态装配（D9）：readonly 档只读工具；research 档加研究执行工具。
触金类工具（下单/调仓/风控修改/实盘开关）**不存在于任何档**——
工具集是代码事实，白名单是第二道闸。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from mcpserver.api_client import QuantApiClient

# 档位标记（注册时显式分组，4.4 枚举断言用）
TOOL_TIERS: Dict[str, set] = {"readonly": set(), "research": set()}


def register_readonly_tools(mcp, api: QuantApiClient) -> None:
    """只读档：数据目录/策略库/任务绩效/指标信号/研究日志/能力自述。"""

    @mcp.tool()
    def list_data_sources() -> Dict[str, Any]:
        """数据目录：可用品种与周期、bar 数与时间范围。"""
        return api.get("/api/data/available-sources")

    @mcp.tool()
    def list_strategies(page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """策略库列表（含评分与谱系）。"""
        return api.get("/api/strategies", params={"page": page, "page_size": page_size})

    @mcp.tool()
    def get_strategy(strategy_id: str) -> Dict[str, Any]:
        """单个策略详情（DNA、指标、回测记录）。"""
        return api.get(f"/api/strategies/{strategy_id}")

    @mcp.tool()
    def list_evolution_tasks(page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        """进化任务列表与状态（绩效查询入口）。"""
        return api.get("/api/evolution/tasks", params={"page": page, "page_size": page_size})

    @mcp.tool()
    def list_indicators() -> Any:
        """内置指标元数据（47 项：参数空间、输出列、lookback——warmup 推导依据）。"""
        return api.get("/api/research/indicators")

    @mcp.tool()
    def list_signal_columns() -> Any:
        """可用信号列（内置 ∪ 插件，标注来源）——假设条件 subject 合法引用集。"""
        return api.get("/api/research/signals")

    @mcp.tool()
    def list_hypotheses(status: Optional[str] = None, page: int = 1) -> Dict[str, Any]:
        """研究假设列表（含状态与区间）。"""
        params = {"page": page}
        if status:
            params["status"] = status
        return api.get("/api/research/hypotheses", params=params)

    @mcp.tool()
    def get_hypothesis(hypothesis_id: str) -> Dict[str, Any]:
        """单个假设详情（结构化条件、状态、区间）。"""
        return api.get(f"/api/research/hypotheses/{hypothesis_id}")

    @mcp.tool()
    def get_research_log(hypothesis_id: Optional[str] = None, page: int = 1) -> Dict[str, Any]:
        """研究日志（假设→分析→证据→结论全链路；续接研究前先读）。"""
        params = {"page": page}
        if hypothesis_id:
            params["hypothesis_id"] = hypothesis_id
        return api.get("/api/research/log", params=params)

    @mcp.tool()
    def get_analysis_task(task_id: str) -> Dict[str, Any]:
        """分析长任务轮询（status: pending|running|succeeded|failed）。"""
        return api.get(f"/api/research/analysis/tasks/{task_id}")

    @mcp.tool()
    def get_validation_report(strategy_id: str) -> Dict[str, Any]:
        """验证证据档案（人话关键数字：最差情况与样本量）。"""
        return api.get(f"/api/research/validation/{strategy_id}/report")

    for fn in (list_data_sources, list_strategies, get_strategy,
               list_evolution_tasks, list_indicators, list_signal_columns,
               list_hypotheses, get_hypothesis, get_research_log,
               get_analysis_task, get_validation_report):
        TOOL_TIERS["readonly"].add(fn.__name__)


def register_research_tools(mcp, api: QuantApiClient) -> None:
    """研究执行档：分析/登记/迁移/隔离验证/随机窗口/进化触发与轮询。"""

    @mcp.tool()
    def run_analysis(template: str, params: Dict[str, Any],
                     symbol: str, timeframe: str, start: str, end: str,
                     hypothesis_id: Optional[str] = None) -> Dict[str, Any]:
        """执行参数化分析。template ∈ condition_stats|distribution_compare|
        correlation_scan|event_study|pattern_mining（重组走 202+task_id 轮询，
        响应含 poll 提示）。区间半开 [start,end) UTC。"""
        body = {
            "template": template, "params": params,
            "data_range": {"symbol": symbol, "timeframe": timeframe,
                           "start": start, "end": end},
        }
        if hypothesis_id:
            body["hypothesis_id"] = hypothesis_id
        return api.post("/api/research/analysis", body)

    @mcp.tool()
    def register_hypothesis(when_conditions: list, then_conditions: list,
                            statement: str, data_ranges: list) -> Dict[str, Any]:
        """登记研究假设（取得隔离验证资格）。when/then 为 {subject,action,target}
        列表；subject 须经 list_signal_columns 校验；data_ranges 为
        {symbol,timeframe,start,end} 列表（半开区间）。同条件集+区间集
        重复登记返回既有 id（duplicated=true）。"""
        return api.post("/api/research/hypotheses", {
            "conditions": {"when_conditions": when_conditions,
                           "then_conditions": then_conditions},
            "statement": statement, "data_ranges": data_ranges})

    @mcp.tool()
    def transition_hypothesis(hypothesis_id: str, target_status: str,
                              evidence_ref: str) -> Dict[str, Any]:
        """假设状态迁移：active→supported|refuted（证据=验证 record_id）
        或→superseded（证据=接替假设 id）。终态不可再迁移。"""
        return api.post(
            f"/api/research/hypotheses/{hypothesis_id}/transition",
            {"target_status": target_status, "evidence_ref": evidence_ref})

    @mcp.tool()
    def run_isolated_validation(hypothesis_id: str, strategy_id: str,
                                symbol: str, timeframe: str,
                                start: str, end: str) -> Dict[str, Any]:
        """隔离回测（与判定并集重叠会被拒并列明细；结果含 warmup 声明
        与门槛判定明细；同键重复幂等返回既有）。"""
        return api.post("/api/research/validate-backtest", {
            "hypothesis_id": hypothesis_id, "strategy_id": strategy_id,
            "request_range": {"symbol": symbol, "timeframe": timeframe,
                              "start": start, "end": end}})

    @mcp.tool()
    def run_random_window_test(strategy_id: str, symbol: str, timeframe: str,
                               data_start: str, data_end: str,
                               window_bars: int = 200, n_windows: int = 8,
                               seed: int = 42,
                               hypothesis_id: Optional[str] = None) -> Dict[str, Any]:
        """随机窗口抽查（同种子同参数结果一致；返回最差窗口与分位摘要）。"""
        body = {"strategy_id": strategy_id, "symbol": symbol,
                "timeframe": timeframe, "data_start": data_start,
                "data_end": data_end, "window_bars": window_bars,
                "n_windows": n_windows, "seed": seed}
        if hypothesis_id:
            body["hypothesis_id"] = hypothesis_id
        return api.post("/api/research/random-window-test", body)

    @mcp.tool()
    def register_formula_plugin(name: str, expression: str) -> Dict[str, Any]:
        """注册公式插件（受限 DSL：白名单函数、禁前视、静态校验三关；
        同名同表达式幂等返回既有版本；同名新表达式版本递增）。"""
        return api.post("/api/research/plugins/formula",
                        {"name": name, "expression": expression})

    @mcp.tool()
    def register_model_plugin(name: str, features: list, data_start: str,
                              data_end: str, symbol: str = "BTCUSDT",
                              timeframe: str = "4h",
                              template: str = "rolling_logistic",
                              window: int = 0, step: int = 20,
                              target_horizon: int = 4) -> Dict[str, Any]:
        """配置模型插件（walk-forward 滚动回归；异步训练 202+task_id，
        轮询 get_analysis_task；失败产生 rejected 记录可查）。"""
        return api.post("/api/research/plugins/model", {
            "name": name, "features": features, "template": template,
            "params": {"window": window, "step": step},
            "target": {"type": "return_sign", "horizon": target_horizon},
            "symbol": symbol, "timeframe": timeframe,
            "data_start": data_start, "data_end": data_end})

    @mcp.tool()
    def translate_hypothesis_to_strategy(
            hypothesis_id: str, strategy_symbol: str = "BTCUSDT",
            strategy_timeframe: str = "4h") -> Dict[str, Any]:
        """把已验证假设转化为策略（conditions 为唯一翻译源；不可表达
        显式拒绝；须先有 pass 验证记录）。"""
        return api.post(
            f"/api/research/translate/{hypothesis_id}",
            {"strategy_symbol": strategy_symbol,
             "strategy_timeframe": strategy_timeframe})

    @mcp.tool()
    def submit_feature_request(title: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """慢通道功能需求单（只持久化与可查，不会自动实现）。"""
        return api.post("/api/research/feature-requests",
                        {"title": title, "payload": payload})

    @mcp.tool()
    def trigger_evolution(config: Dict[str, Any]) -> Dict[str, Any]:
        """触发进化任务（返回 task_id，用 poll_evolution_task 轮询）。"""
        return api.post("/api/evolution/tasks", config)

    @mcp.tool()
    def poll_evolution_task(task_id: str, total_timeout: float = 600.0) -> Dict[str, Any]:
        """轮询进化任务到终态（退避 5s×1.5 上限 60s；保留最后已知状态）。"""
        return api.poll_until_terminal(
            f"/api/evolution/tasks/{task_id}",
            terminal=("completed", "failed", "stopped", "cancelled"),
            total_timeout=total_timeout)

    for fn in (run_analysis, register_hypothesis, transition_hypothesis,
               run_isolated_validation, run_random_window_test,
               register_formula_plugin, register_model_plugin,
               translate_hypothesis_to_strategy,
               submit_feature_request, trigger_evolution, poll_evolution_task):
        TOOL_TIERS["research"].add(fn.__name__)
