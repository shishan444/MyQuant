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
    "RESEARCH_STRATEGY_NOT_FOUND":
        "策略不存在：检查 strategy_id，或用 list_strategies 查询",
    "RESEARCH_DATA_INSUFFICIENT":
        "区间数据不足：扩大区间，或换 list_data_sources 中 bar 数充足的"
        "品种/周期",
    "RESEARCH_WARMUP_INSUFFICIENT":
        "warmup 不足：把 start 前移补足指标回看期（参数范围见 "
        "list_indicators），或换 lookback 更短的指标",
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
    "RESEARCH_TEMPLATE_UNKNOWN":
        "模板名不在五类之列：template ∈ condition_stats|distribution_compare|"
        "correlation_scan|event_study|pattern_mining",
    "RESEARCH_COLUMN_NOT_AVAILABLE":
        "引用了模板不可用列：查看 detail.available_columns 清单；指标条件"
        "（如 rsi_14）请改用 condition_stats（支持指标参数计算）",
    "RESEARCH_INDICATOR_UNKNOWN":
        "指标不在注册表：查看 detail.available_indicators 清单"
        "（元数据经 list_indicators 查询）",
    # ---- 插件
    "RESEARCH_FORMULA_INVALID":
        "插件配置非法：检查 name/expression 必填、DSL 白名单函数与禁前视"
        "约束（detail 含具体位置）",
    "RESEARCH_SIGNAL_SOURCE_INVALID":
        "信号源非法：subject 须在 list_signal_columns 清单内（标注来源与"
        "可引用域）",
    "RESEARCH_COMPOSITION_INVALID":
        "组合形制非法：检查 WHEN/THEN 组合结构（detail 含位置）",
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
