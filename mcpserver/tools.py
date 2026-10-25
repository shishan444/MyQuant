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
from urllib.parse import quote

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

# 轮询总时长钳制域 [1, 3600]s（防误传 0/负值立即超时、超长值挂死会话）
_TOTAL_TIMEOUT_MAX = 3600.0

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
        return api.get(f"/api/strategies/{quote(strategy_id, safe='')}")

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
        """内置指标元数据（48 项：参数空间、输出列、lookback——warmup 推导
        依据；预算规则：主公式 max(200, 跨指标取最大的(周期类参数注册表
        上限之和 × 3))，高周期条件掩码链 max(条件实际参数 × 3, 20)——bar
        数按该轴周期折算为前扩时长（1d 轴 42 bar = 42 天；下限 20 与主
        公式下限 200 分属两式），含公式插件时取 max(内置推导,
        plugin_formula_lookback)；warmup 不足报错 detail 携带实算
        warmup_bars 与 rule 全文）。"""
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
    def union_balance(symbol: str, timeframe: str,
                      hypothesis_id: str) -> Dict[str, Any]:
        """判定并集余量查询（0.4.1 R4——本变更唯一新工具）：该假设的
        已判区间清单、剩余可用区间与剩余可用时长。三参数必填（判定
        并集是假设级账本）；假设不存在 404（前置存在性判定）；输出
        {hypothesis_id, symbol, timeframe, judged[], remaining[],
        remaining_bars}——remaining=数据目录实查范围（parquet 首尾 bar
        完整时间戳直读，半开）−判定并集（先合并重叠/相邻再逐段差）；
        remaining_bars=Σ floor(段时长/周期)（不足整 bar 残余丢弃——保守
        计数）。零副作用只读；与隔离验证 RESEARCH_RANGE_OVERLAP 同一
        账本两个出口——查询 remaining 段发起隔离验证不被重叠拒。"""
        return api.get("/api/research/union-balance", params={
            "symbol": symbol, "timeframe": timeframe,
            "hypothesis_id": hypothesis_id})

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
        try:
            return api.get("/api/research/market-snapshot", params=params)
        except ApiError as exc:
            # 卡6/A3：快照参数面无 start（仅 symbol/timeframes/as_of）——
            # 通用 warmup hint 的「扩大数据前缀」指引在此不可执行，
            # 局部覆写为降 timeframes 子集特化文案（不动 tool_safe 全局）
            if exc.code == "RESEARCH_WARMUP_INSUFFICIENT":
                payload = errors.error_payload(
                    exc.code, exc.message, exc.detail)
                payload["hint"] = (
                    "快照 warmup 不足：降低 timeframes 子集（如去掉 3d/"
                    "1d 长周期档），或换 bar 数更充足的品种——快照参数面 "
                    "无 start，无法前移起点")
                return {"error": payload}
            raise

    @mcp.tool()
    @tool_safe
    def list_hypotheses(status: Optional[str] = None, page: int = 1,
                        cursor: Optional[str] = None) -> Dict[str, Any]:
        """研究假设列表（含状态与区间；conditions 为结构化混合形制——
        0.2.13 起参数化指标 subject 为 {indicator, params, field} 对象，
        历史留痕可能仍含字符串编码形）。缺省最新在前；total 为真总数；
        cursor=上一响应 next_cursor 续页（keyset 深翻页无漂移）。"""
        params: Dict[str, Any] = {"page": page}
        if status:
            params["status"] = status
        if cursor is not None:
            params["cursor"] = cursor
        return api.get("/api/research/hypotheses", params=params)

    @mcp.tool()
    @tool_safe
    def get_hypothesis(hypothesis_id: str) -> Dict[str, Any]:
        """单个假设详情（结构化条件、状态、区间；subject 混合形制——
        结构化指标对象或字符串精确名）。"""
        return api.get(
            f"/api/research/hypotheses/{quote(hypothesis_id, safe='')}")

    @mcp.tool()
    @tool_safe
    def get_research_log(hypothesis_id: Optional[str] = None,
                         entry_type: Optional[str] = None,
                         page: int = 1,
                         cursor: Optional[str] = None) -> Dict[str, Any]:
        """研究日志（假设→分析→证据→结论全链路；续接研究前先读）。
        entry_type 可按类型过滤（screen=全局扫描留痕：历史扫描区间与
        top 候选指纹清单——登记引用候选前自查，避免踩入已扫描数据）。
        缺省最新在前；total 为真总数；cursor=上一响应 next_cursor 续页
        （keyset 深翻页无漂移）。"""
        params: Dict[str, Any] = {"page": page}
        if hypothesis_id:
            params["hypothesis_id"] = hypothesis_id
        if entry_type:
            params["entry_type"] = entry_type
        if cursor is not None:
            params["cursor"] = cursor
        return api.get("/api/research/log", params=params)

    @mcp.tool()
    @tool_safe
    def get_analysis_task(task_id: str) -> Dict[str, Any]:
        """分析长任务轮询（status: pending|running|succeeded|failed）。"""
        return api.get(
            f"/api/research/analysis/tasks/{quote(task_id, safe='')}")

    @mcp.tool()
    @tool_safe
    def get_validation_report(strategy_id: str) -> Dict[str, Any]:
        """验证证据档案（人话关键数字：最差情况与样本量）。"""
        return api.get(
            f"/api/research/validation/{quote(strategy_id, safe='')}/report")

    @mcp.tool()
    @tool_safe
    def list_feature_requests(status: Optional[str] = None, page: int = 1,
                              page_size: int = 50) -> Dict[str, Any]:
        """慢通道功能需求单列表（只持久化与可查，不会自动实现；
        分页 page/page_size≤50）。"""
        page = max(1, page)
        page_size = max(1, min(page_size, _PAGE_MAX))
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        return api.get("/api/research/feature-requests", params=params)

    # ── 纸盘只读四件（rebuild-paper-trading 5.3） ──

    @mcp.tool()
    @tool_safe
    def list_paper_tasks(status: Optional[str] = None, limit: int = 20,
                         offset: int = 0) -> Dict[str, Any]:
        """纸盘任务列表（实时行情虚拟资金前向验证的任务状态查询；
        status 五态过滤 pending|running|completed|stopped|error；
        created_at 倒序分页 limit≤50/offset）。"""
        limit = max(1, min(limit, _PAGE_MAX))
        offset = max(0, offset)
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return api.get("/api/paper-trading/tasks", params=params)

    @mcp.tool()
    @tool_safe
    def get_paper_task(task_id: str) -> Dict[str, Any]:
        """纸盘任务详情（运行态组：账户/持仓/统计+窗口游标 bars_processed
        +研究验证状态 research_status——查询期现算〔三序规则，状态迁移
        后随动〕；任务行快照列是创建时点审计事实，不进本响应。经 REST
        白名单透传自动随动）。"""
        return api.get(
            f"/api/paper-trading/tasks/{quote(task_id, safe='')}")

    @mcp.tool()
    @tool_safe
    def get_paper_report(task_id: str, final: bool = False) -> Dict[str, Any]:
        """纸盘验证报告（三层结构 performance/deviation/evidence，无
        可行/不可行自动判定——考场无观点）。final=False 滚动版（实时
        计算，任意状态可取——running 中随时看当前成绩）；final=True
        结题固化版（未结题 409 task not concluded）。"""
        # 路径字面量分派（AST 对拍形制——变量拼接会脱出白名单提取）
        if final:
            return api.get(
                f"/api/paper-trading/tasks/{quote(task_id, safe='')}"
                "/report/final")
        return api.get(
            f"/api/paper-trading/tasks/{quote(task_id, safe='')}/report")

    @mcp.tool()
    @tool_safe
    def get_paper_history(task_id: str, type: str = "fills",
                          limit: int = 100) -> Dict[str, Any]:
        """纸盘历史明细三面：type=fills 成交流水（含 seq/kind/reason 审计
        字段，bar_time 升序）｜equity 权益曲线（逐 bar 快照）｜funding
        资金费明细（逐 8h 结算点对账披露——settled/pending/
        settled_disclosure_only 三态可见）。分页 limit≤50。"""
        if type == "fills":
            return api.get(
                f"/api/paper-trading/tasks/{quote(task_id, safe='')}/fills",
                params={"limit": max(1, min(limit, _PAGE_MAX))})
        if type == "equity":
            return api.get(
                f"/api/paper-trading/tasks/{quote(task_id, safe='')}/equity",
                params={"limit": max(1, min(limit, _PAGE_MAX))})
        if type == "funding":
            return api.get(
                f"/api/paper-trading/tasks/{quote(task_id, safe='')}/funding",
                params={"limit": max(1, min(limit, _PAGE_MAX))})
        return {"error": errors.error_payload(
            "MCP_PARAM_INVALID",
            f"type 须为 fills|equity|funding，收到 {type!r}")}

    for fn in (list_data_sources, list_strategies, get_strategy,
               list_evolution_tasks, list_indicators, list_signal_columns,
               get_market_snapshot, union_balance,
               list_hypotheses, get_hypothesis, get_research_log,
               get_analysis_task, get_validation_report,
               list_feature_requests,
               list_paper_tasks, get_paper_task, get_paper_report,
               get_paper_history):
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
        param_sweep|regime_split|precheck_hypothesis|segment_narrative|
        counter_profile|exit_sweep
        （重组走 202+task_id 轮询，响应含 poll 提示）。区间半开
        [start,end) UTC。params 必填键（缺键 422 detail 含键名）：
        condition_stats 须 indicator/params/condition；distribution_compare
        须 column/group_a/group_b；correlation_scan 须 signal_column；
        event_study 须 condition；pattern_mining 须 as_of；param_sweep 须
        indicator+condition；precheck_hypothesis 须 when_conditions+
        validate_range；screen_patterns/regime_split 无必填键。
        condition_stats/event_study/regime_split 支持 conditions 条件数组形制
        （0.4.1 起；regime_split 2026-10-14 起）：与旧键组二选一（同传 422 指明互斥——condition_stats
        旧组 indicator+params+condition ∥ 新组 conditions；event_study
        旧组 condition ∥ 新组 conditions）；条数 1..5（AND 语义——全部
        条件同时成立的时点才计入统计与事件流；空数组与超限 422 披露
        条数域）；每条沿单条件混合形制（结构化指标对象/字符串列名，
        含可选高周期 timeframe 键）。conditions 条目中插件列引用
        （fx_/model_ 前缀列）与 timeframe 键不可同用——422
        RESEARCH_CONDITION_INVALID，detail 携带 plugin_columns 与
        hint「移除 timeframe 键（基准周期求值）或改用内置指标列
        引用」（插件列只注入基准周期数据，高周期轴求值语义未定义）。
        形制间边界差异：两形制在
        condition_stats 上结果一致（存量本前扩装载）；在 event_study
        上存在区间首段指标收敛性差异——旧键走普通装载（头部 bar 指标
        为截断近似、payload 键集冻结无 warmup_bars 键），conditions
        （含长度 1）统一走引用并集前扩装载（头部已收敛、payload 携带
        warmup_bars>0 披露）；多条件 warmup 预算=全部条目指标引用的
        并集（「只用不算」契约）。
        condition_stats 支持 params.forward_bars（前向持有期
        bar 数，默认 24）与 params.min_samples（小样本下限，默认 30）。
        方向口径（包四 GAP-01 披露）：win_rate 与 forward_return_mean 为
        做多持有口径（收益=价格涨跌幅）；short 视角下做空胜率=收益为负
        比例（零收益样本不计入胜方，不可用 1−win_rate 换算）——返回体
        metric_perspective 键携带同款口径说明，计算逻辑不随视角变化
        （param_sweep 效应方向锚自适应做空候选，见其 effect_direction
        输出列）。
        当前可用比较动作全集（fix-research-c2 起，统计与翻译两侧
        一致）：gt/lt/ge/le、cross_above/cross_below（含 breakout/
        breakdown 别名）、price_above/price_below（价格对指标列比较
        ——比较主体固定 close，target 为占位值被忽略）、touch/spike/
        shrink/divergence_top/divergence_bottom/consecutive_up/
        consecutive_down/cross_above_series/cross_below_series/
        lookback_any/touch_bounce/role_reversal；未识别 action 结构化
        拒绝（422 附 legal_values=动作分派表键集）。
        时序动作（fix-mtf-execution-consistency R4 起）：
        within_bars_after（内层事件在近 N 根内触发过、不含当根——
        lookback_any 的当根排除变体；登记面可选键 window（>=1 整数）
        与 inner_action（gt/lt/ge/le/price_above，内层阈值由 target
        兼任）；window 单位=基因求值轴根数，轴内求值后投影）。
        同步/异步分界：pattern_mining 与 screen_patterns 恒异步（202+
        task_id 轮询）；其余模板求值行数超过 10000（_HEAVY_ROWS，见
        api/routes/research/analysis.py 常量）降级异步，低于阈值同步
        直返。
        screen_patterns 全谱扫描（恒异步，长耗时注意 executor 并发占用）：
        params.features（"all" 或指标名子集）、params.horizons（默认
        [4,12,24,48,72]）、params.top_n（默认 20）、params.min_samples（默认
        30）——输出按 FDR 校正 q 值排序的候选清单；skipped_indicators 为
        {name, reason} 数组（reason 封闭枚举三值：no_default_params 无默认
        参数网格/compute_failed 计算无产出（异常时附 reason_detail 首个
        异常摘要，无异常时无此键）/no_covered_actions 无覆盖动作；含候选
        指纹，登记引用时经
        register_hypothesis.provenance 回链；组合候选（is_combination
        产物）经 precheck_hypothesis 获得组合复核——when 条件全集 AND
        语义整体求值）；候选条目携带 direction_hint 方向分类标注
        （包四 GAP-01——short=统计量均值为负的做空候选/long=均值为正
        的做多候选/neutral=零均值；仅提示性标注，排序仍按 FDR 校正
        q 值不变）。param_sweep 参数稳健性检查（第③步检查①，快路径
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
        复查方式，判定参数不可传。条件可携带可选高周期 timeframe 键
        （语义与假设登记的多周期条件一致——仅 >= 请求基准周期的高
        周期、经掩码投影求值，低周期拒绝）；插件列引用与 timeframe
        键不可同用（422 边界见 conditions 总述句）。regime_split 行情分段（第③步检查②，
        快路径）：params.segmentation（"auto" 或显式段列表
        [{start,end,type?}]）、可选 params.indicator+params.condition+
        params.forward_bars（携带则逐段统计前向收益——段样本<30 标
        insufficient_sample）。状态序列直切与行情快照同一套机械判读
        （高波动覆盖态优先于趋势/震荡三态），判读参数内建不可调、gates
        披露；段清单供「候选规律在各行情段是否一致」检查与后续分段
        验证（0.2.10）共用段定义。conditions 组合条件形制与
        condition 单条件键二选一（同传 422，detail.param=
        "condition|conditions" 指明互斥对）：条数 1..5（AND 语义
        ——全部条件同时成立的组合触发集做逐段统计，分段统计口径
        与单条件形制一致），空数组与超限 422 披露 legal_range=
        1..5；每条沿单条件混合形制（结构化指标对象/基础列/插件列
        字符串精确名，含可选高周期 timeframe 键——掩码投影求值），
        但插件列引用与 timeframe 键不可同用（插件列只注入基准周期
        数据，高周期轴求值语义未定义——422 RESEARCH_CONDITION_
        INVALID，detail 携带 plugin_columns 与 hint「移除
        timeframe 键（基准周期求值）或改用内置指标列引用」）；
        conditions 形式经引用并集前扩装载，warmup 预算=全部条目
        指标引用的并集（「只用不算」契约）。
        precheck_hypothesis 假设预检（登记前
        体检，G3 组合复核+G11 频率外推+G5 事前预算三合一）：params.
        when_conditions（when 条件全集，登记同构混合形制含可选
        timeframe 键）+ params.validate_range（拟验证区间 {symbol,
        timeframe,start,end}——须与分析区间同 symbol 同 timeframe，外推
        量纲前提）+ 可选 params.forward_bars（默认 24）；三层报告
        （combination_stats 组合统计/frequency_projection 密度外推/
        budget_check 两维 warmup 预算对照）+ 三态结论 verdict：
        projected_trades ≥5 pass / >0 且 <5 warn / 分析段触发数 0
        reject_suggestion——阈值 5 内建不可配，比较规则直接比较不取整
        （4.9 落 warn，保守方向）。口径差披露：预估=触发密度外推上界
        （basis: trigger_density_upper_bound）——引擎空仓进场抑制未
        计入，单向偏大于实际成交笔数。事件策略层：event_study/
        condition_stats/precheck_hypothesis 的 params 可选
        trigger_policy（条件集整体级，形制与 register_hypothesis.
        trigger_policy 同构——三值封闭枚举 {none, first_true,
        suppress_bars}）：携带时事件口径=合并掩码经权威变换后的事件流
        （n_events/sample_size/触发数与外推基数均切事件数，报告声明
        生效策略与 basis 切 event_density——事件数对事件数与统计链
        /api/validate 闭环对照；不携带=逐 bar 现状口径逐字节不变）。
        事件形制整理触发不创造触发：0 事件形态照常 reject_suggestion
        ——suppress_bars 的 bars 上界在求值时点按数据长度校验（超界
        422 附 legal_range）。双锚口径：本模板锚=拟验证区间
        预估笔数 projected_trades；register_hypothesis 登记自动挂接的
        锚=分析段实际触发数 analysis_segment_trades（多区间取最小值）
        ——两锚共用阈值与映射。预算口径与隔离回测 warmup.basis 同源
        同值（derive_warmup_full 两维式）；预算不足进报告不报错。预检
        不进判定并集（entry_type=precheck 不挂假设——预检过的区间仍可
        用于隔离验证）。segment_narrative 区间事件摘要（0.4.1 R3，快
        路径）：无必填键；可选 params.indicators 点名事件源指标清单
        （任何在册指标——缺省走默认事件源=category∈{trend,momentum,
        pattern} 全指标 default 参数+轨道族 {BB,Keltner,Donchian}，机械
        推导；不入默认集类别 trend_strength/volume/structure/ml/
        derivatives 在结果披露口径、显式点名不受限）——输出时间线
        事件清单（四类：pattern_trigger 形态触发时点/indicator_cross
        封闭交叉对 MACD(macd,signal)·Stochastic(k,d)·Aroon(up,down)
        上/下穿/band_position close 进出轨道上下轨/sustained_run close
        连续同向变动段）+结构统计（区间涨跌幅/波动区间/事件计数）；
        载荷无原始 OHLCV 行；双上限：区间 ≤5000 bar（装载前）、事件
        ≤500 条（提取后）；留痕 analysis 型（挂 hypothesis_id 则区间
        进该假设判定并集，沿既有语义）。counter_profile 反例画像（0.4.1
        R5，快路径）：params 必填 condition|conditions（二选一键组）
        + direction（封闭枚举 long/short——mixed 是执行概念不属研究
        主张）+ 可选 forward_bars（观察窗口，缺省 24）——把条件的历史
        触发分为符合组（窗口内任一时点实现方向预期——区间语义，「触及
        目标后回落」的盈利样本不误判；判定比较序列=close）/反例组，
        输出两组在触发时点的全指标特征分布对比（特征域=注册表默认
        参数全部输出列减 guard_only/lazy；八键分布参数；现算失败
        skipped_features 披露）按判别力（Cohen's d）排序的差异清单
        ——工具只呈现差异不生成条件（特征选择由 AI 做；判别特征 ≤2
        属研究协议约束不写入工具）；反例组样本 <30 标 insufficient
        不拒；反例组 0=全符合合法；总触发 <30 走既有小样本拒绝。
        exit_sweep 退出参数扫描（0.4.1 R6——本模板不挂假设：结果
        三要素 hypothesis_id 被模板忽略，留痕检查型不进判定并集）：
        params 必填 condition|conditions（二选一）+ direction（long/
        short——做空语义经此既有形参表达（包四 GAP-01：不新增
        perspective 同义参数）；direction=short 时出场统计按空头持仓
        计算——价格下跌产生正收益，win_rate 即做空胜率）+
        grid={modes:[{mode, stops, take_profits}]}——网格按
        止损模式分组（量纲随模式双关）；模式合法集=回测引擎真实分派
        集引用式（现值 {pct, atr, trail}，引擎扩分派后自动跟随）；
        stops 值域 pct 档 [0.005,0.20] 与 validator 现役域同源、
        atr 档 [0.5, 5.0]（ATR 倍数）与 validator 现役域同源；止盈
        必填正值；跨模式 Σ(len(stops)×len
        (take_profits)) ≤50；重复模式条目拒绝；校验期任何失败整批
        422 无部分结果、执行期单组回测失败该组标注不阻塞（两期分野）；
        不设 take_profit>stop_loss 约束（劣结构组按引擎真实行为回测
        如实呈现、三道门自然判 ineffective）；条件条目可携带
        timeframe 键，语义三分支——等于基准周期与缺省语义相同
        （键归一，显式写基准周期不构成高周期过滤）、严格高于基准
        周期经掩码投影求值（与策略翻译链同款放行）、低于基准周期
        拒绝（TIMEFRAME_NOT_HIGHER）；不可表达形制仍显式拒绝不
        静默降级（RESEARCH_NOT_EXPRESSIBLE，与 translate_
        hypothesis 同一公共入口防线）；
        输出每组 {mode,stop_loss,take_profit,win_rate,profit_factor
        （亏损笔数 0→null）,net_expectation（含费滑）,max_drawdown,
        total_trades,status}+三道门整体判定（连续门直接复用
        param_sweep 判定器；资格门交易数≥5/真伪门净期望>0；最差聚合
        全序 unsupported>spike>indeterminate>plateau；判定标尺内建
        不可传）；单轴长度 1 推论：固定止损只扫止盈时整体结论恒不超
        过 spike（单点无法证明稳健——预期行为非可疑信号）；组数>10
        或行数超阈值走 202+轮询。"""
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
        三形态互斥：{condition:{subject,action,target}}（subject 混合形制：
        参数化指标 {indicator, params, field} 结构化或字符串精确名——
        扫描候选同形制直喂；0.2.14 起 MAY 携带 timeframe 键=高周期条件
        掩码投影（>= 请求基准周期、结构化 subject 任意参数现算、
        indicator 参数可省——注意穿越类动作挂高周期为状态窗语义：
        一次穿越持续 True 至下次高周期收盘）或 {conditions:[c1..c5]}
        （包四 GAP-03 条件数组——1..5 条 AND 交集命中，条目形制与
        condition 同构；校验与求值和 condition_stats 等四消费者同一
        内核，组合假设案例无需拆单条件近似；warmup=全部条目指标引用
        并集；含字符串 subject 条目时 indicator 参数必填）或
        {timestamps:[...]}（点名 ≤5，自带定位不传 start/end）。硬上限：
        limit ≤5（缺省 3，命中超限等距取样首尾保底，n_matched 披露命中
        总数）、context_bars ≤20（缺省 8，触发点前后各取）。start 缺省=
        全量数据集+effective_start 披露有效域；end 显式时装载终点自动
        外扩 context_bars 根（尾部窗口完整）。用法：检查①②统计判定通过
        后，抽查真实案例辨数据质量（K 线缺口/异常值/指标失真——统计量
        会把坏数据平均掉）。"""
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
            indicator: Optional[str] = None,
            params: Optional[Dict[str, Any]] = None,
            reductions: Optional[list[str]] = None,
            start: Optional[str] = None, end: Optional[str] = None,
            groups: Optional[list[dict[str, Any]]] = None,
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
        的分布形态不同，阈值不该凭常识硬编码。
        批量形制（0.4.1）：groups=[{indicator, params?, reductions?,
        start?, end?}] 与单组字段（indicator/params/reductions/start/
        end）互斥（同传 422 指明互斥对）；组数 1..8；各组区间独立、
        返回 {"groups":[…]}（数组序=请求序）；整批原子含执行期——
        任一组失败整批 422 无部分结果（detail 以 groups[N] 指明组序）；
        载荷分档：单组 2048 / 批量 16384 字节。
        指标参数修复（2026-10-13）：BB std 与 Keltner
        atr_period/multiplier 传参修复已生效，新生成计算数值正确；
        BB 历史存量已按 2026-10-13 终局裁决整批清除。Keltner 历史
        存量为吞参语义（atr_period 从未生效）；按 2026-10-14 裁决
        历史策略已全部清除——无存量即无重算，重算边界问题终局
        关闭。"""
        body: Dict[str, Any] = {
            "symbol": symbol, "timeframe": timeframe}
        # 单组字段无条件透传（is not None 形制——与 params/reductions 同）：
        # indicator 与 groups 同传时交 API 单一事实源判互斥 422，工具层
        # 不吞键（else 分支会使互斥判定对 indicator 键永不触发）
        if indicator is not None:
            body["indicator"] = indicator
        if groups is not None:
            body["groups"] = groups
        if params is not None:
            body["params"] = params
        if reductions is not None:
            body["reductions"] = reductions
        if start is not None:
            body["start"] = start
        if end is not None:
            body["end"] = end
        return api.post("/api/research/query-indicator", body)

    @mcp.tool()
    @tool_safe
    def register_hypothesis(
            when_conditions: list[dict[str, Any]],
            then_conditions: list[dict[str, Any]],
            statement: str,
            data_ranges: list[dict[str, Any]],
            provenance: Optional[dict[str, Any]] = None,
            trigger_policy: Optional[dict[str, Any]] = None,
            strategy_params: Optional[dict[str, Any]] = None,
            exit_partial_conditions: Optional[list[dict[str, Any]]] = None,
            add_trigger_conditions: Optional[list[dict[str, Any]]] = None,
            add_suppress_conditions: Optional[list[dict[str, Any]]] = None,
            direction: Optional[str] = None) -> Dict[str, Any]:
        """登记研究假设（取得隔离验证资格）。when/then 为 {subject,action,target}
        对象列表；subject 混合形制（0.2.13）：参数化指标用结构化
        {indicator, params, field}（多输出指标 field 必填，参数空间经
        list_indicators 查询）、基础列/插件列用字符串精确名（经
        list_signal_columns 校验）；字符串编码形（如 rsi_14）已关闭。
        条件条目 MAY 携带可选顶层 timeframe 键：支持组=when 与
        add_trigger_conditions（Q1 裁决加仓触发组跟随 when 侧）；
        值须合法周期且 >= 基准周期（data_ranges 统一周期）——timeframe
        修饰整条件（掩码投影：subject/action/target 在高周期轴求值、
        布尔掩码按 asof_completed 投影回基准索引——仅已收盘高周期
        K 线可见）；低周期/未知周期/then 与减仓抑制组携带键结构化
        拒绝；显式写基准周期=缺省同身份（canonical 归一）；高周期须
        数据目录可达（登记时校验）。
        data_ranges 为 {symbol,timeframe,start,end} 对象列表（半开区间，
        0.2.14 起须全条目同 symbol 同 timeframe——该周期即基准周期）。
        同条件集+区间集
        重复登记返回既有 id（duplicated=true）。provenance={task_id,
        candidate_fingerprint} 可选——引用扫描候选来源（screen_patterns
        输出的候选指纹），登记后该扫描区间自动进入判定并集（同区间
        验证会被拒）；不改变假设身份。登记时自动执行假设预检
        （precheck_hypothesis 能力）：返回体附加可选 precheck_warnings
        键（三态结论 verdict + 判定锚 analysis_segment_trades——data_ranges
        多区间时取逐区间触发数最小值，附 per_range 逐区间明细；纯增量
        键，预检失败降级或幂等命中时不携带该键）；预检不改变登记的
        校验链、幂等键与假设身份，不阻断任何后续动作（裁决权在研究者
        ——0 触发建议不送考，判定并集的机会成本可预见）。
        登记返回体另附可选 threshold_unit_warnings 键（阈值单位可疑
        警告——与 precheck_warnings 语义正交的纯观察面）：条件比较对象
        属「小数比率语义」列（值域量级以 1 为界的无量纲量——CMF/
        OI_ChangeRate/OIPriceDivergence/FundingPressure/funding_rate）
        且阈值绝对值>1 时逐条目警示（field/column/target/hint 四要素
        ——如 -2.33 应为 -0.0233 的百分数误写）。警告不阻断登记（个别
        指标大阈值可能合法，自行核对）；五组条件（when/then/分批三组）
        全覆盖；幂等命中不携带。
        trigger_policy 可选（事件策略层，条件集整体级——修饰 when
        全集 AND 合并掩码如何变换为事件流，非单条件修饰）：封闭枚举
        {"mode": "none"|"first_true"|"suppress_bars", "bars": <正整数>}
        ——none=缺省等价（显式与缺省同身份）；first_true=每个极大连续
        True 段仅首根计一次事件（连续段计一次）；suppress_bars 必带
        bars=事件后 N bar 内的后续触发抑制（跨段生效，间隔恰 N 产生
        新事件）。trigger_policy 参与假设指纹与幂等身份（同条件集+
        同区间集+不同策略=不同假设——事件密度语义是研究主张的组成
        部分）；带策略假设按事件流验证与外推（统计链与预检外推同为
        事件数口径闭环），翻译为策略时直接映射进入场信号组装（信号
        层同口径；实际成交笔数另受空仓进场抑制影响）。事件形制整理
        触发不创造触发：0 事件形态照常 reject_suggestion。bars 数据
        长度上界在求值时点校验（登记时点仅形制——短区间实跑超界即
        422）。
        strategy_params 可选（0.4.2 策略层参数声明块——与假设身份
        正交的风险参数）：键集 stop_loss/take_profit/sl_mode/
        position_size/leverage/exit_fraction/add_fraction/max_exposure/
        max_hold/time_exit_utc/position_mode/risk_fraction/
        initial_fraction/max_position_loss
        （值域按 sl_mode 分叉：pct/trail 档 [0.005,0.20] 比例值、
        atr 档 [0.5,5.0] ATR 倍数；sl_mode∈{pct,atr,trail}；
        take_profit>stop_loss，trail 档豁免；position_size∈
        [0.10,1.0]；leverage∈[1,10]；exit_fraction∈(0,1]/
        add_fraction∈(0,1] 须伴随宿主组分批声明；max_exposure∈
        (0,生效杠杆]（未声明 leverage 时上界 1——声明更大上限须同
        时声明 leverage）；三键有组无值时登记归一化字面化 0.5/0.1/
        1.0）；max_hold∈[1,200] 正整数 bar 数——最长持有 N 根 K 线的
        时间出场（到期无条件平仓，与条件出场/止损止盈并行的第三出场
        机制；不声明=无时间离场；N=1 行为等价 N=2——入场 bar 的出场
        时点被入场单占据，最早平仓在入场次 bar；仅回测域生效——live
        链路当前不承载，实盘化前统一处理）；time_exit_utc∈[0,1439]
        整数（UTC 分钟时刻——固定时刻出场，持仓 bar 区间覆盖该时刻
        即平仓；跨日持仓逐日生效；与 max_hold 并存先到者先触发；无
        宿主组依赖；不声明=无固定时刻出场）；position_mode∈
        {fixed,risk_budget}（complete-research-tooling 动态定仓——
        缺省 fixed 固定比例路径；risk_budget=风险预算制定仓：入场
        size=当时权益×risk_fraction÷实际生效止损距离（pct 档距离=
        价格×比例、atr/trail 档=倍数×当根 ATR），名义敞口上限=
        当时权益×min(leverage,max_exposure) 超限截断+orders_capped
        计数披露；risk_budget 须同时声明 risk_fraction 与 stop_loss
        ——止损距离是仓位公式分母；回测报告 metrics 携带 position_
        sizing 六子键（mode/risk_fraction/leverage_cap/orders_
        capped/avg_entry_size/max_entry_size）；组间配对对照表：
        risk_budget 只管首仓，加仓仍按 add_fraction 固定比例、
        max_exposure 全局取严、exit_fraction 比例语义不变）；
        risk_fraction∈(0,0.5]（每笔风险预算占当时权益比例）；
        initial_fraction∈(0,1]（add-position-sizing-params 首笔
        动用资金比例——声明时首笔定仓 margin=当时权益×initial_
        fraction（fixed 路径内替换 position_size 消费点），加仓仍走
        add_fraction、position_size 仍必填仍校验；与 position_mode=
        risk_budget 互斥——同笔首仓不可两个指挥）；max_position_
        loss>0 金额无上界（持仓金额防线——单一合并持仓总浮亏=
        价格浮亏+该持仓累计资金费净支出，每根已闭合 K 线按收盘价
        判定，达线强制全部平仓；与价格止损各自独立判定、
        归因序在止损止盈后时间出场前；fixed 与 risk_budget 档均
        生效；两键回测与纸盘同语义执行——判定机制/归因序一致
        （收盘判定、位次、reason 可辨识；成交时点为环境形制差——
        回测当根收盘扣滑点、纸盘 pending 次根开盘））；不声明两键=
        存量行为逐位不变（首笔用 position_size、无金额防线）。翻译时
        逐参数映射进产物 risk_genes（返回体 risk_genes="declared"；
        无声明=平台默认 "platform_default"）。声明块不进条件指纹与
        幂等身份（同条件集+同区间集=同一假设——参数
        变体经终态后带新声明重登记产生）；幂等四分支：请求不带键
        →duplicated 返回既有（附 existing_strategy_params 摘要键）；
        带键（含显式空对象 {}=显式声明「无参数」）与既有声明全等
        →duplicated、不等→422（防静默丢弃既有声明）；{} 与键缺失
        语义不同。坏值 422 RESEARCH_PARAM_INVALID（detail 含
        field/legal_range）。
        exit_partial_conditions/add_trigger_conditions/add_suppress_conditions
        可选（0.4.2 R3 分批条件组，对象数组——组内 {subject,action,target}
        与 when/then 同构）：部分离场触发/加仓触发/加仓抑制三组，进
        conditions 参与条件指纹与幂等身份（不同分批条件组=不同假设——
        触发条件属研究主张）；组条目不带 timeframe 键；组存在须非空。
        翻译映射 REDUCE_TRIGGER/ADD_TRIGGER/ADD_INHIBIT；分批数值参数
        （exit_fraction 0.5/add_fraction 0.1/max_exposure 1.0 缺省）经
        strategy_params 声明块承载（有组无值自动归一化字面值）。
        direction 可选（交易方向 {long,short}——做空假设通道）：缺省与
        显式 "long" 语义相同（同指纹同实体）；"short" 为做空假设——
        方向参与假设身份指纹与幂等身份（同条件集+同区间集+异方向=
        两条独立假设，各自独立幂等/判定并集/谱系族）；翻译直通产物
        risk_genes.direction（做空策略价格下跌产生正收益）；登记响应
        回显 direction 键（新建/幂等命中均携带）；非法值 422
        RESEARCH_PARAM_INVALID（detail 含 field/legal_range）。"""
        body: Dict[str, Any] = {
            "conditions": {"when_conditions": when_conditions,
                           "then_conditions": then_conditions},
            "statement": statement, "data_ranges": data_ranges}
        if provenance:
            body["provenance"] = provenance
        if trigger_policy is not None:
            body["trigger_policy"] = trigger_policy
        if strategy_params is not None:
            body["strategy_params"] = strategy_params
        if exit_partial_conditions is not None:
            body["exit_partial_conditions"] = exit_partial_conditions
        if add_trigger_conditions is not None:
            body["add_trigger_conditions"] = add_trigger_conditions
        if add_suppress_conditions is not None:
            body["add_suppress_conditions"] = add_suppress_conditions
        if direction is not None:
            body["direction"] = direction
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
            f"/api/research/hypotheses/"
            f"{quote(hypothesis_id, safe='')}/transition",
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
        保持 active 可换区间再验证、不可作迁移证据。返回体 direction=short
        时，total_return/win_rate 等指标为空头口径（价格下跌产生正收益）；
        benchmark_return 恒为同期多头买入持有，excess_return=空头收益−多头基准。"""
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
        无 fail 有 inconclusive 窗→inconclusive；全 pass→pass。返回体
        direction=short 时，total_return/win_rate 等指标为空头口径（价格
        下跌产生正收益）；benchmark_return 恒为同期多头买入持有，
        excess_return=空头收益−多头基准。"""
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
        都行吗」，分段答「哪类行情不行」。返回体 direction=short 时，
        total_return/win_rate 等指标为空头口径（价格下跌产生正收益）；
        benchmark_return 恒为同期多头买入持有，excess_return=空头收益−多头基准。"""
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
            strategy_timeframe: str | None = None) -> Dict[str, Any]:
        """把 active 假设转化为草稿策略（draft——验证对象，不要求先有
        pass 记录；终态假设拒绝转化）。WHEN→入场信号、THEN→出场信号
        （预期兑现即离场）；不可表达显式拒绝；同基因签名显式分流标注。
        执行周期契约：strategy_timeframe 缺省 None=继承假设基准周期
（data_ranges[0].timeframe；存量异周期假设拒绝并指引显式传参）；
回执 execution_timeframe 键披露产物执行周期。
回执身份六键：direction（产物方向 long|short）、max_hold（声明
        的最长持仓 bar 数|int|null）、time_exit_utc（声明的固定时刻
        出场 UTC 分钟|int|null）、position_mode（声明的仓位模式
        fixed|risk_budget，缺省 fixed）、risk_fraction（声明的风险
        预算比例|float|null）、reuse_note（same_dna_existing=true
        时附同基因策略复用三要素说明，其余 null）。多周期边界：when 与
        加仓触发组条件支持顶层 timeframe 键（值须合法周期且不低于策略
        基准周期——低于基准拒，等于基准与缺省同语义归一）；then/减仓/
        抑制组携带 timeframe 拒绝（跨周期前向/归属语义未定义）；插件列
        条件不支持 timeframe（插件源无周期槽位）。
        转正走 transition(supported) 原子路径（draft 自身 pass 时自动转正）。"""
        return api.post(
            f"/api/research/translate/{quote(hypothesis_id, safe='')}",
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
        """轮询进化任务到终态（退避 5s×1.5 上限 60s；保留最后已知状态；
        total_timeout 钳制 [1,3600]s）。"""
        total_timeout = max(1.0, min(total_timeout, _TOTAL_TIMEOUT_MAX))
        return api.poll_until_terminal(
            f"/api/evolution/tasks/{quote(task_id, safe='')}",
            terminal=("completed", "failed", "stopped", "cancelled"),
            total_timeout=total_timeout)

    # ── 纸盘挂载两件（rebuild-paper-trading 5.3；研究执行档——资源
    #    创建语义类比触发回测/进化任务，非触金） ──

    @mcp.tool()
    @tool_safe
    def mount_paper_task(strategy_id: str,
                         initial_cash: Optional[float] = None,
                         leverage: Optional[int] = None,
                         fee: Optional[float] = None,
                         slippage: Optional[float] = None,
                         symbol: Optional[str] = None,
                         window_bars: Optional[int] = None,
                         stop_loss_pct: Optional[float] = None,
                         ) -> Dict[str, Any]:
        """挂载纸盘任务（上盘=把策略库条目送去实时前向验证）。参数面与
        REST 创建完全一致——仅验证条件参数（初始资金/杠杆/费滑/品种/
        验证窗口/停止条件），**不含任何策略语义参数**：方向与执行周期
        以策略 DNA 为准，AI 无法经挂载覆写信号语义。创建恒受理（201
        pending 行；并发满载时排队不拒绝）。window_bars：缺省 180 根；
        N≥1=N 根窗口到点自动结题（不限期窗口是 REST 专属形态——显式
        null 与参数缺省在本工具参数面不可区分，故不经 MCP 表达）。
        策略演化不影响已挂载任务（创建时 DNA 快照自包含）。"""
        body: Dict[str, Any] = {"strategy_id": strategy_id}
        if initial_cash is not None:
            body["initial_cash"] = initial_cash
        if leverage is not None:
            body["leverage"] = leverage
        if fee is not None:
            body["fee"] = fee
        if slippage is not None:
            body["slippage"] = slippage
        if symbol is not None:
            body["symbol"] = symbol
        if window_bars is not None:
            body["window_bars"] = window_bars
        if stop_loss_pct is not None:
            body["stop_loss_pct"] = stop_loss_pct
        return api.post("/api/paper-trading/tasks", body)

    @mcp.tool()
    @tool_safe
    def unmount_paper_task(task_id: str) -> Dict[str, Any]:
        """撤盘（stop 语义：保留数据出结题报告，**不删除证据**——成交/
        权益/资金费记录与报告均可查）。running 任务协作停止（当前 K 线
        完整落库后退出）；pending 未拾取直接撤销；终态任务 409（幂等
        拒绝面）。结题报告经 get_paper_report(final=true) 获取。"""
        return api.post(
            f"/api/paper-trading/tasks/{quote(task_id, safe='')}/stop", {})

    for fn in (run_analysis, inspect_cases, query_indicator,
               register_hypothesis, transition_hypothesis,
               run_isolated_validation, run_random_window_test,
               run_staged_validation, run_backtest,
               register_formula_plugin, register_model_plugin,
               translate_hypothesis_to_strategy,
               submit_feature_request, trigger_evolution,
               poll_evolution_task,
               mount_paper_task, unmount_paper_task):
        TOOL_TIERS["research"].add(fn.__name__)
