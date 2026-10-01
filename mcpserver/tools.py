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
        """可用信号列（内置 ∪ 插件，标注来源与 scopes 可引用域和
        reference_form 引用形制）——假设条件 subject 合法引用集。引用
        形制（0.2.13 统一）：参数化指标为结构化 {indicator, params,
        field}（条目 reference_form.params_space 为参数空间、多输出
        指标 field 从 output_fields 选一必填）；基础列/插件列为字符串
        精确名。scopes 三域含义：hypothesis=可作假设条件 subject；
        analysis=可作分析模板的列引用（原始列模板——指标分析走
        condition_stats 的 indicator 参数，指标列此项为 false）；strategy=
        可作策略信号源。"""
        return api.get("/api/research/signals")

    @mcp.tool()
    @tool_safe
    def get_market_snapshot(symbol: str, timeframes: Optional[str] = None,
                            as_of: Optional[str] = None) -> Dict[str, Any]:
        """时点行情快照（六步研究第①步固定入口）：多周期五维行情卡——
        trend/momentum/volatility/volume/derivatives 各含 as_of 时点读数、
        90 日分位与三态判读。symbol 交易对（如 BTCUSDT）；timeframes 逗号
        分隔周期子集（缺省 3d/1d/4h/1h）；as_of 读数时点（缺省=最新一根
        bar，闭端截至防前视）。"""
        params: Dict[str, Any] = {"symbol": symbol}
        if timeframes is not None:
            params["timeframes"] = timeframes
        if as_of is not None:
            params["as_of"] = as_of
        return api.get("/api/research/market-snapshot", params=params)

    @mcp.tool()
    @tool_safe
    def list_hypotheses(status: Optional[str] = None, page: int = 1) -> Dict[str, Any]:
        """研究假设列表（含状态与区间；conditions 为结构化混合形制——
        0.2.13 起参数化指标 subject 为 {indicator, params, field} 对象，
        历史留痕可能仍含字符串编码形）。"""
        params = {"page": page}
        if status:
            params["status"] = status
        return api.get("/api/research/hypotheses", params=params)

    @mcp.tool()
    @tool_safe
    def get_hypothesis(hypothesis_id: str) -> Dict[str, Any]:
        """单个假设详情（结构化条件、状态、区间；subject 混合形制——
        结构化指标对象或字符串精确名）。"""
        return api.get(f"/api/research/hypotheses/{hypothesis_id}")

    @mcp.tool()
    @tool_safe
    def get_research_log(hypothesis_id: Optional[str] = None,
                         entry_type: Optional[str] = None,
                         page: int = 1) -> Dict[str, Any]:
        """研究日志（假设→分析→证据→结论全链路；续接研究前先读）。
        entry_type 可按类型过滤（screen=全局扫描留痕：历史扫描区间与
        top 候选指纹清单——登记引用候选前自查，避免踩入已扫描数据）。"""
        params: Dict[str, Any] = {"page": page}
        if hypothesis_id:
            params["hypothesis_id"] = hypothesis_id
        if entry_type:
            params["entry_type"] = entry_type
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
               get_market_snapshot,
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
        correlation_scan|event_study|pattern_mining|screen_patterns|
        param_sweep（重组走 202+task_id 轮询，响应含 poll 提示）。区间半开
        [start,end) UTC。condition_stats 支持 params.forward_bars（前向持有期
        bar 数，默认 24）与 params.min_samples（小样本下限，默认 30）。
        screen_patterns 全谱扫描（恒异步，长耗时注意 executor 并发占用）：
        params.features（"all" 或指标名子集）、params.horizons（默认
        [4,12,24,48,72]）、params.top_n（默认 20）、params.min_samples（默认
        30）——输出按 FDR 校正 q 值排序的候选清单（含候选指纹，登记引用时经
        register_hypothesis.provenance 回链；其余候选用 condition_stats
        逐条独立复核）。param_sweep 参数稳健性检查（第③步检查①，快路径
        同步直返）：params.indicator（指标名）+ params.condition（结构化
        {subject, action, target}——subject 混合形制：参数化指标
        {indicator, params, field} 结构化对象或字符串精确名（扫描候选
        同形制直接喂入，解析出的列须属于该指标输出列）+ params.sweep（{axis:"target", values:"auto"
        或数值列表——auto 取与扫描同源的共享刻度）+ params.forward_bars。
        三道门内建不可调（资格门样本≥30/真伪门 BH 后 q<0.05 且方向与中心
        一致/连续门相邻有效点≥2），结论 plateau|spike|indeterminate|
        unsupported 附稳健区间（unsupported=该区间无任何显著正证据——
        丢弃或彻底换条件，换阈值复查无意义）；输出逐点披露原始统计量与
        判定所用参数（gates）——判定标尺内建，改网格/持有期/区间是正当
        复查方式，判定参数不可传。regime_split 行情分段（第③步检查②，
        快路径）：params.segmentation（"auto" 或显式段列表
        [{start,end,type?}]）、可选 params.indicator+params.condition+
        params.forward_bars（携带则逐段统计前向收益——段样本<30 标
        insufficient_sample）。状态序列直切与行情快照同一套机械判读
        （高波动覆盖态优先于趋势/震荡三态），判读参数内建不可调、gates
        披露；段清单供「候选规律在各行情段是否一致」检查与后续分段
        验证（0.2.10）共用段定义。"""
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
    def inspect_cases(
            symbol: str, timeframe: str,
            selector: Dict[str, Any],
            limit: int = 3, context_bars: int = 8,
            start: Optional[str] = None, end: Optional[str] = None,
            indicator: Optional[str] = None) -> Dict[str, Any]:
        """案例抽查（第③步检查③——眼见为实）：候选条件或点名时间戳的
        真实触发案例窗口，全流程唯一让原始 K 线进入上下文的通道。selector
        两形态互斥：{condition:{subject,action,target}}（subject 混合形制：
        参数化指标 {indicator, params, field} 结构化或字符串精确名——
        扫描候选同形制直喂；0.2.14 起 MAY 携带 timeframe 键=高周期条件
        掩码投影（>= 请求基准周期、结构化 subject 任意参数现算、
        indicator 参数可省——注意穿越类动作挂高周期为状态窗语义：
        一次穿越持续 True 至下次高周期收盘）或 {timestamps:[...]}（点名
        ≤5，自带定位不传 start/end）。硬上限：limit ≤5（缺省 3，命中
        超限等距取样首尾保底，n_matched 披露命中总数）、context_bars
        ≤20（缺省 8，触发点前后各取）。start 缺省=全量数据集+effective_start
        披露有效域；end 显式时装载终点自动外扩 context_bars 根（尾部窗口
        完整）。用法：检查①②统计判定通过后，抽查真实案例辨数据质量
        （K 线缺口/异常值/指标失真——统计量会把坏数据平均掉）。"""
        body: Dict[str, Any] = {
            "symbol": symbol, "timeframe": timeframe, "selector": selector,
            "limit": limit, "context_bars": context_bars}
        if start:
            body["start"] = start
        if end:
            body["end"] = end
        if indicator:
            body["indicator"] = indicator
        return api.post("/api/research/inspect-cases", body)

    @mcp.tool()
    @tool_safe
    def query_indicator(
            symbol: str, timeframe: str,
            indicator: str,
            params: Optional[Dict[str, Any]] = None,
            reductions: Optional[list[str]] = None,
            start: Optional[str] = None, end: Optional[str] = None,
            ) -> Dict[str, Any]:
        """指标读数（第③步检查④——参数是策略的一部分）：单指标任意
        参数现算，归约摘要直答「该参数下指标在这段数据上什么样子」。
        reductions 封闭枚举多选：latest（末端有效值）/quantiles（固定
        七点分位 p01..p99）/regime_means（按趋势/过渡/震荡/高波动分组
        均值+count）/trend_slope（最小二乘斜率），缺省 [latest,
        quantiles]。params 缺省=注册表 default（echo 披露实际生效值）；
        键值越界或多余键结构化拒绝。多输出指标（BB/MACD 等）全部输出
        列独立归约；个别列冷启动未完成时该列归约为 null（键保留）。
        求值域 [effective_start, end]（end 缺省=数据末端），warmup 段
        不入归约。用法：登记假设前读数定阈值——RSI(38) 与 RSI(14)
        的分布形态不同，阈值不该凭常识硬编码。"""
        body: Dict[str, Any] = {
            "symbol": symbol, "timeframe": timeframe,
            "indicator": indicator}
        if params is not None:
            body["params"] = params
        if reductions is not None:
            body["reductions"] = reductions
        if start:
            body["start"] = start
        if end:
            body["end"] = end
        return api.post("/api/research/query-indicator", body)

    @mcp.tool()
    @tool_safe
    def register_hypothesis(
            when_conditions: list[dict[str, Any]],
            then_conditions: list[dict[str, Any]],
            statement: str,
            data_ranges: list[dict[str, Any]],
            provenance: Optional[dict[str, Any]] = None) -> Dict[str, Any]:
        """登记研究假设（取得隔离验证资格）。when/then 为 {subject,action,target}
        对象列表；subject 混合形制（0.2.13）：参数化指标用结构化
        {indicator, params, field}（多输出指标 field 必填，参数空间经
        list_indicators 查询）、基础列/插件列用字符串精确名（经
        list_signal_columns 校验）；字符串编码形（如 rsi_14）已关闭。
        when 条目 MAY 携带可选 timeframe 键（0.2.14）：仅 >= 基准周期
        （data_ranges 统一周期）的高周期——timeframe 修饰整条件（掩码
        投影：subject/action/target 在高周期轴求值、布尔掩码按
        asof_completed 投影回基准索引——仅已收盘高周期 K 线可见）；
        低周期/未知周期/then 携带键结构化拒绝；显式写基准周期=缺省
        同身份；高周期须数据目录可达（登记时校验）。
        data_ranges 为 {symbol,timeframe,start,end} 对象列表（半开区间，
        0.2.14 起须全条目同 symbol 同 timeframe——该周期即基准周期）。
        同条件集+区间集
        重复登记返回既有 id（duplicated=true）。provenance={task_id,
        candidate_fingerprint} 可选——引用扫描候选来源（screen_patterns
        输出的候选指纹），登记后该扫描区间自动进入判定并集（同区间
        验证会被拒）；不改变假设身份。"""
        body: Dict[str, Any] = {
            "conditions": {"when_conditions": when_conditions,
                           "then_conditions": then_conditions},
            "statement": statement, "data_ranges": data_ranges}
        if provenance:
            body["provenance"] = provenance
        return api.post("/api/research/hypotheses", body)

    @mcp.tool()
    @tool_safe
    def transition_hypothesis(hypothesis_id: str, target_status: str,
                              evidence_ref: str) -> Dict[str, Any]:
        """假设状态迁移：active→supported|refuted（证据=验证 record_id，
        verdict 须匹配：supported 须 pass、refuted 须 fail）、→superseded
        （证据=接替假设 id）或→abandoned（免证据，evidence_ref 填放弃说明）。
        inconclusive 记录不可作迁移证据（证据不足不可定论——样本不足的
        验证不能否定假设；假设保持 active 可换区间再验证）。
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
        与门槛判定明细；同键重复幂等返回既有）。verdict 三态：
        pass/fail/inconclusive——inconclusive=证据不足非证伪（样本不足
        或每笔收益均值 95% 置信区间跨零——方向不可区分于噪声），假设
        保持 active 可换区间再验证、不可作迁移证据。"""
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
        """随机窗口抽查（同种子同参数结果一致；返回最差窗口与分位摘要）。
        每窗按三段闸门判定，总判定最差聚合三态：任一窗 fail→fail；
        无 fail 有 inconclusive 窗→inconclusive；全 pass→pass。"""
        body = {"strategy_id": strategy_id, "symbol": symbol,
                "timeframe": timeframe, "data_start": data_start,
                "data_end": data_end, "window_bars": window_bars,
                "n_windows": n_windows, "seed": seed}
        if hypothesis_id:
            body["hypothesis_id"] = hypothesis_id
        return api.post("/api/research/random-window-test", body)

    @mcp.tool()
    @tool_safe
    def run_staged_validation(strategy_id: str, symbol: str, timeframe: str,
                              start: str, end: str,
                              segmentation: str = "auto",
                              segments: Optional[list[dict[str, Any]]] = None,
                              hypothesis_id: Optional[str] = None,
                              ) -> Dict[str, Any]:
        """分段验证（第⑤步考试的分题型成绩单——策略在什么行情下失效的
        正式度量）：按市场状态切段（趋势/过渡/震荡/高波动）逐段独立
        回测，段级门槛三态（pass/fail/inconclusive——短段样本不足/
        方向不可区分判 inconclusive，证据不足非证伪）+ 汇总三键（最差
        段/段间差异/是否全段达标）。双模互斥：segmentation="auto"（机械
        判读，gates 内建）或缺省；explicit 传 segments=[{label,start,end}]
        （恰好覆盖请求区间、相接不重叠、升序）。总判定最差聚合三态
        （任一段 fail→fail；无 fail 有 inconclusive 段→inconclusive；
        全段 pass→pass）。挂假设且全段 pass 产生可被 transition引用
        的 pass 记录（与隔离验证叠加不替代）；inconclusive/fail 同样
        落对应 verdict 记录（inconclusive 不可引用——证据不足不可定论，
        假设保持 active）；不挂假设 adhoc 纯日志（verdict 照报、
        record_id=None）。验证区间不入判定并集（防同区间重复靠幂等
        ——与隔离回测同语义）。与随机窗口抽查互补：抽查答「任意时段
        都行吗」，分段答「哪类行情不行」。"""
        body = {"strategy_id": strategy_id, "symbol": symbol,
                "timeframe": timeframe, "start": start, "end": end,
                "segmentation": segmentation}
        if segments is not None:
            body["segments"] = segments
        if hypothesis_id:
            body["hypothesis_id"] = hypothesis_id
        return api.post("/api/research/staged-validation", body)

    @mcp.tool()
    @tool_safe
    def run_backtest(strategy_id: str, symbol: str, timeframe: str,
                     start: str, end: str, fee: float = 0.001,
                     slippage: float = 0.0005, init_cash: float = 100000.0,
                     hypothesis_id: Optional[str] = None) -> Dict[str, Any]:
        """沙盒回测（快速试错：可调手续费/滑点/初始资金，直返指标——
        不判门槛、不构成验证证据）。纪律：挂 hypothesis_id 时该区间计入
        判定并集（此后同区间的隔离验证会被拒——先偷看不获利）；超 bar
        上限被拒，走隔离验证。"""
        body: Dict[str, Any] = {"strategy_id": strategy_id, "symbol": symbol,
                                "timeframe": timeframe, "start": start,
                                "end": end, "fee": fee,
                                "slippage": slippage, "init_cash": init_cash}
        if hypothesis_id:
            body["hypothesis_id"] = hypothesis_id
        return api.post("/api/research/backtest-sandbox", body)

    @mcp.tool()
    @tool_safe
    def register_formula_plugin(name: str, expression: str) -> Dict[str, Any]:
        """注册公式插件（受限 DSL：白名单函数 21 词、禁前视、静态校验
        三关；同名同表达式幂等返回既有版本；同名新表达式版本递增）。
        白名单三族——时序变换（shift/diff/rolling_mean|std|max|min|sum/
        zscore/ema/corr/abs）；时序结构 crossover(a,b) 上穿/crossunder
        下穿（当前 a>b 且前一根 a≤b——金叉死叉可表达）；逐元素
        max/min（两序列或序列与常量逐 bar 取大取小，非窗口聚合）、
        gt/lt/eq、logical_and/logical_or/logical_not（比较与逻辑产布尔
        列，与数值混算按 0/1 参与——运算符写法 a>b / a and b 仍非法，
        须函数形）。布尔列作假设条件 subject 推荐 target 域：gt 0.5 /
        ge 1 / eq 1（True=1.0，gt 1 恒 False 勿踩）。"""
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
        轮询 get_analysis_task；失败产生 rejected 记录可查）。features
        混合形制（0.2.13）：参数化指标 {indicator, params, field} 结构化
        对象 + 基础列/插件列字符串精确名（校验即解析，训练按同参数现算）。"""
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

    for fn in (run_analysis, inspect_cases, query_indicator,
               register_hypothesis, transition_hypothesis,
               run_isolated_validation, run_random_window_test,
               run_staged_validation, run_backtest,
               register_formula_plugin, register_model_plugin,
               translate_hypothesis_to_strategy,
               submit_feature_request, trigger_evolution, poll_evolution_task):
        TOOL_TIERS["research"].add(fn.__name__)
