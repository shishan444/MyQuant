"""错误码 → 可行动 hint 注册表（improve-mcp-agent-loop M1）。

服务端码族以 core/analysis + api/routes 中 ResearchError 实际抛出点为
权威源——对拍测试（tests/mcpserver/test_errors.py）静态扫描源码断言
注册表覆盖全部实际抛出的码，漂移即红。hint 面向 AI agent：直接说明
下一步做什么，不复述错误。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from mcpserver.api_client import ApiError

# ---------------------------------------------------------------- 注册表

HINTS: Dict[str, str] = {
    # ---- 条件与假设登记
    "RESEARCH_CONDITION_INVALID":
        "条件形制非法：检查 when/then 是否逐条 {subject, action, target}，"
        "subject 须经 list_signal_columns 校验",
    "RESEARCH_RANGE_INVALID":
        "data_ranges 形制非法：每条须为 {symbol, timeframe, start, end} "
        "半开区间（start < end）",
    "RESEARCH_SYMBOL_NOT_FOUND":
        "品种/周期无数据：先调 list_data_sources 确认目录内条目后更正",
    "RESEARCH_HYPOTHESIS_NOT_FOUND":
        "假设不存在：检查 hypothesis_id，或用 list_hypotheses 查询",
    # ---- 状态迁移
    "RESEARCH_HYPOTHESIS_TERMINAL":
        "假设已终态：不可再迁移或验证；登记新假设（可在新假设中引用旧假设）",
    "RESEARCH_TRANSITION_INVALID":
        "迁移非法：检查目标状态与证据——supported 须引用 verdict=pass、"
        "refuted 须引用 verdict=fail 的验证记录（evidence_ref 填 record_id）",
    "RESEARCH_EVIDENCE_NOT_FOUND":
        "证据记录不存在：evidence_ref 用 run_isolated_validation 返回的 "
        "record_id（接替迁移填新假设 id）",
    "RESEARCH_HYPOTHESIS_UNVALIDATED":
        "假设尚无 pass 验证记录：先对草稿策略跑 run_isolated_validation",
    # ---- 验证执行
    "RESEARCH_RANGE_OVERLAP":
        "验证区间与判定并集重叠：查看 detail 的 judged_ranges/overlaps 明细，"
        "换到并集之外的区间（通常把 start 推移到已判定区间之后）",
    "SCREEN_CANDIDATE_REUSE":
        "条件精确复用已扫描区间的候选（该区间数据已被扫描看过）：查看 "
        "detail 的 screen_task_id，实质修改条件（改阈值/参数）登记新假设，"
        "或换到未扫描区间验证；历史扫描可用 get_research_log("
        "entry_type=\"screen\") 自查",
    "RESEARCH_STRATEGY_NOT_FOUND":
        "策略不存在：检查 strategy_id，或用 list_strategies 查询",
    "RESEARCH_DATA_INSUFFICIENT":
        "区间数据不足：扩大区间，或换 list_data_sources 中 bar 数充足的"
        "品种/周期",
    "RESEARCH_DATA_EMPTY":
        "区间无数据（数据空非请求错）：按 detail 的 series_span 调整"
        "分组窗口至数据覆盖范围内，或先用 list_data_sources 确认该"
        "品种/周期的数据区间",
    "RESEARCH_WARMUP_INSUFFICIENT":
        "warmup 不足：换 lookback 更短的指标（参数范围见 "
        "list_indicators）、降低请求周期，或扩大该品种的可得数据前缀"
        "（参数面无 start 的调用方按前两者行动）",
    "RESEARCH_TEMPLATE_INTERNAL":
        "模板内部错误：附 detail 的 template/exception_type/summary "
        "提交问题报告；同参数重试大概率无效（0.3.2 卡4/卡5——诊断字段 "
        "保留异常文本，message 通道已保持干净）",
    # ---- 多周期条件（0.2.14）
    "TIMEFRAME_ON_THEN":
        "THEN 条件不接受 timeframe 键（前向收益对照以基准 bar 计，"
        "跨周期归属未定义）——去掉 then 条目的 timeframe，跨周期方向"
        "表达放 when 条件",
    "TIMEFRAME_UNKNOWN":
        "周期键非法：用合法键集内的周期（detail.legal_range）；高周期"
        "数据可达性以 list_data_sources 实际清单为准",
    "TIMEFRAME_NOT_HIGHER":
        "低周期条件请降基准表达：基准周期（data_ranges 统一周期）降为"
        "该低周期、其余条件挂高周期——聚合歧义不可自证",
    "RESEARCH_MTF_CONTEXT_MISSING":
        "求值入口未构建多周期上下文（调用方接线缺陷非输入错误）："
        "经 run_analysis/inspect_cases/api/validate 正规入口重试；"
        "持续出现属工程缺陷，附 detail 提交问题报告",
    "RESEARCH_COMPONENT_EVIDENCE_MISSING":
        "组合假设缺组件证据：对每个组件条件分别取得验证记录后再组合",
    # ---- 转化与表达
    "RESEARCH_DRAFT_IMMUTABLE":
        "草稿策略受保护字段不可改：修改假设条件后重新转化（translate）"
        "产出新草稿；转正走 transition(supported) 原子路径",
    "RESEARCH_NOT_EXPRESSIBLE":
        "条件超出策略表达边界：查看 detail 的不可表达位置；拆解为可表达"
        "子集重登记，或用 submit_feature_request 走慢通道",
    # ---- 分析模板
    # 清单静态抄录自 api/routes/research/analysis.py 的 _TEMPLATES（权威源，
    # 同 _SUPPORTED_ACTIONS 锚定 engine 惯例）；漂移防线在
    # tests/mcpserver/test_errors.py 的集合对拍断言（不一致即红）
    "RESEARCH_TEMPLATE_UNKNOWN":
        "模板名不在册：template ∈ condition_stats|distribution_compare|"
        "correlation_scan|event_study|pattern_mining|screen_patterns|"
        "param_sweep|regime_split|precheck_hypothesis|segment_narrative|"
        "counter_profile|exit_sweep",
    "RESEARCH_COLUMN_NOT_AVAILABLE":
        "引用了模板不可用列：查看 detail.available_columns 清单；指标条件"
        "（如 rsi_14）请改用 condition_stats（支持指标参数计算）",
    "RESEARCH_INDICATOR_UNKNOWN":
        "指标不在注册表：查看 detail.available_indicators 清单"
        "（元数据经 list_indicators 查询）",
    "RESEARCH_PARAM_INVALID":
        "模板参数非法：查看 detail 的 param/legal_range"
        "（各参数面的合法形制以 detail 为准）",
    # ---- 插件
    "RESEARCH_FORMULA_INVALID":
        "插件配置非法：检查 name/expression 必填、DSL 白名单函数与禁前视"
        "约束（detail 含具体位置）",
    "RESEARCH_SIGNAL_SOURCE_INVALID":
        "信号源非法：subject 须在 list_signal_columns 清单内（标注来源与"
        "可引用域）",
    "RESEARCH_FORMULA_REGISTRY_MISSING":
        "valid 插件的注册表条目缺失（原子双写断裂的数据完整性异常）："
        "核对 signal_registry 与插件表是否被外部改动，必要时重建注册条目",
    "RESEARCH_STORED_DATA_INVALID":
        "库内持久化数据损坏（非请求参数错误，重试无效）：按 detail 的"
        "parse_error 与记录标识（record_id/hypothesis_id/ref）定位损坏"
        "记录，核对是否被外部改写或数据迁移遗留；持续出现附 detail "
        "提交问题报告",
    "RESEARCH_PLUGIN_COLUMN_NOT_FOUND":
        "条件引用的插件列不可达：用 list_signal_columns 查询当前可见插件"
        "列（only_visible），更正列名或先 register_formula_plugin 注册",
    "RESEARCH_COMPOSITION_INVALID":
        "组合形制非法：检查 WHEN/THEN 组合结构（detail 含位置）",
    # ---- 沙盒回测
    "RESEARCH_SANDBOX_LIMIT":
        "沙盒区间超 bar 上限：走隔离验证协议（run_isolated_validation "
        "支持大区间分区间验证）",
    "FEATURE_REQUEST_NOT_FOUND":
        "需求单不存在：核对 request_id 或用 list_feature_requests 查询",
    # ---- MCP 客户端侧
    "MCP_ENDPOINT_NOT_ALLOWED":
        "端点不在当前档位白名单：readonly 档只读；研究执行需 research 档"
        "（触金类端点任何档位不可达）",
    "API_UNREACHABLE":
        "工程 API 不可达：确认应用已启动（./scripts/start.sh）且 "
        "MYQUANT_API_URL 正确",
    # ---- 未分类降级
    "API_INTERNAL_ERROR":
        "无结构化错误码的失败：对照工具描述检查参数形制（detail 含原始"
        "响应）；仍失败时查看 API 服务日志",
}

DEFAULT_HINT = ("未注册错误码：查看 detail 字段定位问题；无法解决时用 "
                "submit_feature_request 提交慢通道需求单")


def hint_for(code: str) -> str:
    """code → 可行动 hint；未注册码回退通用指引。"""
    return HINTS.get(code, DEFAULT_HINT)


# RESEARCH_PARAM_INVALID 的场景分派层（fix-research-c2）：共用错误码下
# 不同参数面共用一句 hint 会与实际错误场景错配（fr_ce325c609a8d）——
# 按 detail.param 匹配场景文案，无命中回退注册表通用文案。注册表机制
# 与静态守护不动（场景表是增量键，tests/mcpserver/test_errors.py 的
# 覆盖面对拍不受影响）。
_PARAM_HINT_SCENARIOS: Dict[str, str] = {
    "forward_bars": "前向持有期 bar 数越界：forward_bars 须为 "
                    "1..数据长度-1 的整数",
    "min_samples": "小样本阈值非法：min_samples 须为 ≥0 的整数"
                   "（0=禁用小样本检查）",
    "condition.action": "比较动作未知：查看 detail.legal_values——"
                        "动作分派表键集（gt/lt/price_above/"
                        "price_below 等）",
    "data_range": "区间参数非法：区间 bar 数超上限时缩小区间"
                  "（detail 的 limit/actual 披露实际值）",
    "condition|conditions": "condition 与 conditions 须二选一"
                            "（键组分派，直调方同样须遵守）",
    "stop_loss": "止损参数越域：值域随 sl_mode 分档（pct 档与 atr 档"
                 "不同）——查看 detail.legal_range 与 sl_mode，按档修正",
    "events": "事件数超上限：缩小区间或收窄 indicators 指标集"
              "（detail 含 actual/limit）",
    # 固定时刻出场（add-session-structure-expression 任务 6）：登记
    # 声明块校验族 detail 用 field 承载（分派层 field 兼容通道）
    "time_exit_utc": "固定时刻出场参数越域：time_exit_utc 为 UTC 分钟"
                     "时刻 0-1439（0=0:00、1439=23:59；单位是分钟不是"
                     "小时）——按 detail.legal_range 修正",
    # 动态定仓（complete-research-tooling 任务 4）：同族 field 通道
    "position_mode": "定仓模式非法：position_mode ∈ {fixed, risk_budget}"
                     "（缺省 fixed 固定比例；risk_budget=风险预算制——"
                     "须同时声明 risk_fraction 与 stop_loss）",
    "risk_fraction": "风险预算比例越域：risk_fraction ∈ (0, 0.5]"
                     "（每笔风险预算占当時权益比例，闭区间上界 0.5）",
    # 仓位参数（add-position-sizing-params 5.1）：同族 field 通道
    "initial_fraction": "首笔动用资金比例越域：initial_fraction ∈ "
                        "(0, 1]（闭区间上界 1.0=全部可用资金；与 "
                        "position_mode='risk_budget' 互斥——同笔首仓"
                        "不可两个指挥）",
    "max_position_loss": "持仓金额防线越域：max_position_loss > 0 且为"
                         "有限数（金额无人为上界——考场无观点不替用户"
                         "判断防线松紧；inf/超大整数非金额拒绝；浮亏"
                         "达线次根开盘全平）",
    # 谱系引用泛化（complete-research-tooling 任务 3）：两类引用
    # （screen_candidate 扫描候选/analysis_task 分析任务）的参数面
    "provenance.ref_type": "引用类型非法：ref_type ∈ {screen_candidate,"
                           " analysis_task}（扫描候选/网格统计任务两类"
                           "来源——按实际来源选型）",
    "provenance.candidate_fingerprint": "候选指纹缺失：ref_type="
                                        "screen_candidate 须携带 "
                                        "candidate_fingerprint（候选定位"
                                        "语义；analysis_task 任务级引用"
                                        "方可省略）",
}


def _scenario_hint(code: str, detail: Any) -> Optional[str]:
    """按 detail.param 场景分派的增量 hint 层；None=无场景命中。"""
    if code == "RESEARCH_PARAM_INVALID" and isinstance(detail, dict):
        # param 优先；无 param 时兼容 field 键（变更二 4.3——登记链
        # strategy_params 校验族 detail 契约统一用 field 承载
        # legal_range，改抛出点会动摇整族 detail 契约且消费方不止
        # 本层，故在分派层单点扩展）
        key = detail.get("param") or detail.get("field")
        return _PARAM_HINT_SCENARIOS.get(str(key)) if key is not None else None
    return None


# ---------------------------------------------------------------- 载荷形制

def error_payload(code: str, message: str, detail: Any = None) -> Dict[str, Any]:
    """统一错误载荷 {code, message, detail, hint}（spec: mcp-server 结构化错误透传）。

    hint 两层：场景分派层（detail.param 命中时给参数面专属文案）→
    注册表通用层（hint_for）。"""
    return {"code": code, "message": message, "detail": detail,
            "hint": _scenario_hint(code, detail) or hint_for(code)}


def payload_from(exc: ApiError) -> Dict[str, Any]:
    """ApiError → 错误载荷。"""
    return error_payload(exc.code, exc.message, exc.detail)
