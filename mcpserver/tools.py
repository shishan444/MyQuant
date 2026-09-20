"""MCP 工具注册（add-ai-research-mcp 4.2/4.3/4.4 / improve-mcp-agent-loop M1）。

两档静态装配（D9）：readonly 档只读工具；research 档加研究执行工具。
触金类工具（下单/调仓/风控修改/实盘开关）**不存在于任何档**——
工具集是代码事实，白名单是第二道闸。

统一错误通道（tool_safe）：失败返回 {"error": {code, message, detail,
hint}} 而非异常坍缩；成功路径返回形制零改变。
"""
from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Optional

from mcpserver import errors
from mcpserver.api_client import (
    ApiError,
    ApiUnreachable,
    EndpointNotAllowed,
    QuantApiClient,
)

# 档位标记（注册时显式分组，4.4 枚举断言用）
TOOL_TIERS: Dict[str, set] = {"readonly": set(), "research": set()}

# 分页上限（M2：默认 20、上限 50——工具层统一 clamp）
_PAGE_MAX = 50

# 列表折叠保留字段（M2/D8：条目 DNA 折叠为 gene_signature 摘要，
# 全量 DNA 经 get_strategy 获取；默认响应体积受控）
_STRATEGY_LIST_FIELDS = ("strategy_id", "name", "symbol", "timeframe", "source",
                         "qualified", "best_score", "generation", "metrics",
                         "gene_signature", "created_at")


def _fold_strategy_list(payload: Any) -> Dict[str, Any]:
    """策略列表条目折叠：保留核心摘要 + gene_signature，去全量 DNA。"""
    items = payload.get("items", []) if isinstance(payload, dict) else []
    folded = [{k: it.get(k) for k in _STRATEGY_LIST_FIELDS} for it in items]
    return {"items": folded, "total": payload.get("total", len(folded)),
            "note": "条目 DNA 已折叠为 gene_signature 摘要；全量 DNA 经 get_strategy 获取"}


def tool_safe(fn: Callable) -> Callable:
    """统一错误通道装饰器：失败转 {"error": {...}}，成功形制不变。

    functools.wraps 保留原签名（SDK inspect.signature 跟随 __wrapped__
    生成参数 schema，默认值不丢）。
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            return {"error": errors.payload_from(exc)}
        except EndpointNotAllowed as exc:
            return {"error": errors.error_payload(
                "MCP_ENDPOINT_NOT_ALLOWED", str(exc))}
        except ApiUnreachable as exc:
            return {"error": errors.error_payload(
                "API_UNREACHABLE", str(exc))}
    return wrapper


def register_readonly_tools(mcp, api: QuantApiClient) -> None:
    """只读档：数据目录/策略库/任务绩效/指标信号/研究日志/能力自述。"""

    @mcp.tool()
    @tool_safe
    def list_data_sources() -> Dict[str, Any]:
        """数据目录：可用品种与周期、bar 数与时间范围。"""
        return api.get("/api/data/available-sources")

    @mcp.tool()
    @tool_safe
    def list_strategies(limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        """策略库列表（分页 limit≤50/offset；条目含评分与 gene_signature 摘要，
        DNA 已折叠——全量经 get_strategy 获取）。"""
        limit = max(1, min(limit, _PAGE_MAX))
        offset = max(0, offset)
        return _fold_strategy_list(
            api.get("/api/strategies", params={"limit": limit, "offset": offset}))

    @mcp.tool()
    @tool_safe
    def get_strategy(strategy_id: str) -> Dict[str, Any]:
        """单个策略详情（DNA、指标、回测记录）。"""
        return api.get(f"/api/strategies/{strategy_id}")

    @mcp.tool()
    @tool_safe
    def list_evolution_tasks(page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        """进化任务列表与状态（绩效查询入口；分页 page/page_size≤50）。"""
        page = max(1, page)
        page_size = max(1, min(page_size, _PAGE_MAX))
        return api.get("/api/evolution/tasks",
                       params={"page": page, "page_size": page_size})

    @mcp.tool()
    @tool_safe
    def list_indicators() -> Any:
        """内置指标元数据（47 项：参数空间、输出列、lookback——warmup 推导依据）。"""
        return api.get("/api/research/indicators")

    @mcp.tool()
    @tool_safe
    def list_signal_columns() -> Any:
        """可用信号列（内置 ∪ 插件，标注来源）——假设条件 subject 合法引用集。"""
        return api.get("/api/research/signals")

    @mcp.tool()
    @tool_safe
    def list_hypotheses(status: Optional[str] = None, page: int = 1) -> Dict[str, Any]:
        """研究假设列表（含状态与区间）。"""
        params = {"page": page}
        if status:
            params["status"] = status
        return api.get("/api/research/hypotheses", params=params)

    @mcp.tool()
    @tool_safe
    def get_hypothesis(hypothesis_id: str) -> Dict[str, Any]:
        """单个假设详情（结构化条件、状态、区间）。"""
        return api.get(f"/api/research/hypotheses/{hypothesis_id}")

    @mcp.tool()
    @tool_safe
    def get_research_log(hypothesis_id: Optional[str] = None, page: int = 1) -> Dict[str, Any]:
        """研究日志（假设→分析→证据→结论全链路；续接研究前先读）。"""
        params = {"page": page}
        if hypothesis_id:
            params["hypothesis_id"] = hypothesis_id
        return api.get("/api/research/log", params=params)

    @mcp.tool()
    @tool_safe
    def get_analysis_task(task_id: str) -> Dict[str, Any]:
        """分析长任务轮询（status: pending|running|succeeded|failed）。"""
        return api.get(f"/api/research/analysis/tasks/{task_id}")

    @mcp.tool()
    @tool_safe
    def get_validation_report(strategy_id: str) -> Dict[str, Any]:
        """验证证据档案（人话关键数字：最差情况与样本量）。"""
        return api.get(f"/api/research/validation/{strategy_id}/report")

    @mcp.tool()
    @tool_safe
    def list_feature_requests(status: Optional[str] = None, page: int = 1,
                              page_size: int = 50) -> Dict[str, Any]:
        """慢通道功能需求单列表（只持久化与可查，不会自动实现）。"""
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        return api.get("/api/research/feature-requests", params=params)

    for fn in (list_data_sources, list_strategies, get_strategy,
               list_evolution_tasks, list_indicators, list_signal_columns,
               list_hypotheses, get_hypothesis, get_research_log,
               get_analysis_task, get_validation_report, list_feature_requests):
        TOOL_TIERS["readonly"].add(fn.__name__)


def register_research_tools(mcp, api: QuantApiClient) -> None:
    """研究执行档：分析/登记/迁移/隔离验证/随机窗口/进化触发与轮询。"""

    @mcp.tool()
    @tool_safe
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
    @tool_safe
    def register_hypothesis(
            when_conditions: list[dict[str, Any]],
            then_conditions: list[dict[str, Any]],
            statement: str,
            data_ranges: list[dict[str, Any]]) -> Dict[str, Any]:
        """登记研究假设（取得隔离验证资格）。when/then 为 {subject,action,target}
        对象列表；subject 须经 list_signal_columns 校验；data_ranges 为
        {symbol,timeframe,start,end} 对象列表（半开区间）。同条件集+区间集
        重复登记返回既有 id（duplicated=true）。"""
        return api.post("/api/research/hypotheses", {
            "conditions": {"when_conditions": when_conditions,
                           "then_conditions": then_conditions},
            "statement": statement, "data_ranges": data_ranges})

    @mcp.tool()
    @tool_safe
    def transition_hypothesis(hypothesis_id: str, target_status: str,
                              evidence_ref: str) -> Dict[str, Any]:
        """假设状态迁移：active→supported|refuted（证据=验证 record_id，
        verdict 须匹配：supported 须 pass、refuted 须 fail）、→superseded
        （证据=接替假设 id）或→abandoned（免证据，evidence_ref 填放弃说明）。
        supported 时谱系 draft 若有自身 pass 记录则同事务转正（draft→active）。"""
        return api.post(
            f"/api/research/hypotheses/{hypothesis_id}/transition",
            {"target_status": target_status, "evidence_ref": evidence_ref})

    @mcp.tool()
    @tool_safe
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
    @tool_safe
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
    @tool_safe
    def register_formula_plugin(name: str, expression: str) -> Dict[str, Any]:
        """注册公式插件（受限 DSL：白名单函数、禁前视、静态校验三关；
        同名同表达式幂等返回既有版本；同名新表达式版本递增）。"""
        return api.post("/api/research/plugins/formula",
                        {"name": name, "expression": expression})

    @mcp.tool()
    @tool_safe
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
    @tool_safe
    def translate_hypothesis_to_strategy(
            hypothesis_id: str, strategy_symbol: str = "BTCUSDT",
            strategy_timeframe: str = "4h") -> Dict[str, Any]:
        """把 active 假设转化为草稿策略（draft——验证对象，不要求先有
        pass 记录；终态假设拒绝转化）。WHEN→入场信号、THEN→出场信号
        （预期兑现即离场）；不可表达显式拒绝；同基因签名显式分流标注。
        转正走 transition(supported) 原子路径（draft 自身 pass 时自动转正）。"""
        return api.post(
            f"/api/research/translate/{hypothesis_id}",
            {"strategy_symbol": strategy_symbol,
             "strategy_timeframe": strategy_timeframe})

    @mcp.tool()
    @tool_safe
    def submit_feature_request(title: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """慢通道功能需求单（只持久化与可查，不会自动实现）。"""
        return api.post("/api/research/feature-requests",
                        {"title": title, "payload": payload})

    @mcp.tool()
    @tool_safe
    def trigger_evolution(config: Dict[str, Any]) -> Dict[str, Any]:
        """触发进化任务（返回 task_id，用 poll_evolution_task 轮询）。"""
        return api.post("/api/evolution/tasks", config)

    @mcp.tool()
    @tool_safe
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
