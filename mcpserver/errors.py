"""错误码 → 可行动 hint 注册表（improve-mcp-agent-loop M1）。

服务端码族以 core/analysis + api/routes 中 ResearchError 实际抛出点为
权威源——对拍测试（tests/mcpserver/test_errors.py）静态扫描源码断言
注册表覆盖全部实际抛出的码，漂移即红。hint 面向 AI agent：直接说明
下一步做什么，不复述错误。
"""
from __future__ import annotations

from typing import Any, Dict

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
    "RESEARCH_WARMUP_INSUFFICIENT":
        "warmup 不足：把 start 前移补足指标回看期（参数范围见 "
        "list_indicators），或换 lookback 更短的指标",
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
        "经 run_analysis/inspect_cases//api/validate 正规入口重试；"
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
        "模板名不在八类之列：template ∈ condition_stats|distribution_compare|"
        "correlation_scan|event_study|pattern_mining|screen_patterns|"
        "param_sweep|regime_split",
    "RESEARCH_COLUMN_NOT_AVAILABLE":
        "引用了模板不可用列：查看 detail.available_columns 清单；指标条件"
        "（如 rsi_14）请改用 condition_stats（支持指标参数计算）",
    "RESEARCH_INDICATOR_UNKNOWN":
        "指标不在注册表：查看 detail.available_indicators 清单"
        "（元数据经 list_indicators 查询）",
    "RESEARCH_PARAM_INVALID":
        "模板参数非法：查看 detail 的 param/legal_range"
        "（condition_stats 的 forward_bars 须为 1..数据长度-1 的整数）",
    # ---- 插件
    "RESEARCH_FORMULA_INVALID":
        "插件配置非法：检查 name/expression 必填、DSL 白名单函数与禁前视"
        "约束（detail 含具体位置）",
    "RESEARCH_SIGNAL_SOURCE_INVALID":
        "信号源非法：subject 须在 list_signal_columns 清单内（标注来源与"
        "可引用域）",
    "RESEARCH_COMPOSITION_INVALID":
        "组合形制非法：检查 WHEN/THEN 组合结构（detail 含位置）",
    # ---- 沙盒回测
    "RESEARCH_SANDBOX_LIMIT":
        "沙盒区间超 bar 上限：走隔离验证协议（run_isolated_validation "
        "支持大区间分区间验证）",
    # ---- MCP 客户端侧
    "MCP_ENDPOINT_NOT_ALLOWED":
        "端点不在当前档位白名单：readonly 档只读；研究执行需 research 档"
        "（触金类端点任何档位不可达）",
    "API_UNREACHABLE":
        "工程 API 不可达：确认应用已启动（python run.py）且 "
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


# ---------------------------------------------------------------- 载荷形制

def error_payload(code: str, message: str, detail: Any = None) -> Dict[str, Any]:
    """统一错误载荷 {code, message, detail, hint}（spec: mcp-server 结构化错误透传）。"""
    return {"code": code, "message": message, "detail": detail,
            "hint": hint_for(code)}


def payload_from(exc: ApiError) -> Dict[str, Any]:
    """ApiError → 错误载荷。"""
    return error_payload(exc.code, exc.message, exc.detail)
