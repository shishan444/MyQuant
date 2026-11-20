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
    "RESEARCH_SYMBOL_NOT_FOUND":
        "品种/周期无数据：先调 list_data_sources 确认目录内条目后更正",
    "RESEARCH_HYPOTHESIS_NOT_FOUND":
        "假设不存在：检查 hypothesis_id——存量假设为只读，可经 "
        "get_research_log 核对",
    # ---- 验证执行
    "RESEARCH_STRATEGY_NOT_FOUND":
        "策略不存在：检查 strategy_id，或用 list_strategies 查询",
    # add-validation-detail B2（HTTP 面专用端点的 404 码——MCP 面不
    # 暴露明细端点，注册表按既有映射机制登记防 parity 反向红）
    "RESEARCH_RECORD_NOT_FOUND":
        "验证记录不存在：检查 record_id，或经验证记录列表查询",
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
    # 回放执行段异常收敛码（fix-replay-task-lifecycle 2.3——禁裸 500）
    "REPLAY_EXECUTION_FAILED":
        "回放执行异常：经任务查询核对 task 行终态与 error_detail "
        "摘要；同参数重试大概率无效，属工程缺陷时附 detail 的 "
        "exception 摘要提交问题报告",
    # ---- 多周期条件（0.2.14）
    "TIMEFRAME_UNKNOWN":
        "周期键非法：用合法键集内的周期（detail.legal_range）；高周期"
        "数据可达性以 list_data_sources 实际清单为准",
    "TIMEFRAME_NOT_HIGHER":
        "低周期条件请降基准表达：基准周期（data_ranges 统一周期）降为"
        "该低周期、其余条件挂高周期——聚合歧义不可自证",
    "RESEARCH_TIMEFRAME_MISMATCH":
        "验证请求周期≠策略执行周期（异轴执行语义未定义）：不传 "
        "timeframe 用策略行周期自洽验证；显式传轴须与策略执行周期"
        "一致（detail 披露两侧值）",
    "RESEARCH_MTF_CONTEXT_MISSING":
        "求值入口未构建多周期上下文（调用方接线缺陷非输入错误）："
        "经 run_analysis/inspect_cases/api/validate 正规入口重试；"
        "持续出现属工程缺陷，附 detail 提交问题报告",
    # ---- 转化与表达
    "RESEARCH_DRAFT_IMMUTABLE":
        "草稿策略受保护字段不可改：DNA 对更新入口锁死（name/tags/notes "
        "等元数据仍可更新）；需不同 DNA 形制时新建策略",
    "RESEARCH_NOT_EXPRESSIBLE":
        "条件超出策略表达边界：查看 detail 的不可表达位置，拆解为可表达"
        "子集重试",
    # ---- 分析模板
    # 清单静态抄录自 api/routes/research/analysis.py 的 _TEMPLATES（权威源，
    # 同 _SUPPORTED_ACTIONS 锚定 engine 惯例）；漂移防线在
    # tests/mcpserver/test_errors.py 的集合对拍断言（不一致即红）
    "RESEARCH_TEMPLATE_UNKNOWN":
        "模板名不在册：template ∈ condition_stats|distribution_compare|"
        "correlation_scan|event_study|pattern_mining|screen_patterns|"
        "param_sweep|regime_split|precheck_hypothesis|segment_narrative|"
        "counter_profile|exit_sweep|wave_inventory|box_inventory|"
        "ruler_compare|ab_compare",
    "RESEARCH_COLUMN_NOT_AVAILABLE":
        "引用了模板不可用列：查看 detail.available_columns 清单；指标条件"
        "（如 rsi_14）请改用 condition_stats（支持指标参数计算）",
    "RESEARCH_INDICATOR_UNKNOWN":
        "指标不在注册表：查看 detail.available_indicators 清单"
        "（元数据经 list_indicators 查询）",
    # 指标依赖装配（core/features/indicators.py 三处+patterns/
    # divergence.py 两处）：detail 的 indicator=声明方、dependency=
    # 问题依赖、chain=循环依赖完整链
    "RESEARCH_INDICATOR_DEPENDENCY_FAILED":
        "指标依赖装配失败：按 detail 的 dependency 定位问题依赖"
        "（indicator 为声明方），chain 为循环依赖声明的完整链——"
        "依赖不在注册表或链成环属声明错误，修 depends_on 声明；"
        "计算失败按 message 内层异常修指标参数或数据",
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
    "RESEARCH_DNA_INVALID":
        "库内策略 DNA 结构/值域非法（非请求参数错误，重试同 DNA 无效）："
        "按 detail.errors 定位具体判据（含 signal_sources 条目序号与键名），"
        "修正 DNA 后经策略写入入口更新或修正库内 dna_json 再重试",
    "RESEARCH_COMPOSITION_INVALID":
        "组合形制非法：检查 WHEN/THEN 组合结构（detail 含位置）",
    # ---- 沙盒回测
    "RESEARCH_SANDBOX_LIMIT":
        "沙盒区间超 bar 上限：分多次小区间 run_backtest（单次上限内"
        "验证后并读结果）",
    # ---- 纸盘域（库内 JSON 列结构化解析——load_stored_json 经 code
    # 形参派发域码，抛出点 api/routes/paper_trading.py 与 core/
    # paper_trading/reporting.py）
    "PAPER_STORED_DATA_INVALID":
        "纸盘库内持久化数据损坏（非请求参数错误，重试无效）：按 detail 的"
        "parse_error 与记录标识（task_id/bar_time/seq/report_version）"
        "定位损坏行（message 点名 pt_signal/pt_report 等表.字段），"
        "核对是否被外部改写或数据迁移遗留；持续出现附 detail "
        "提交问题报告",
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

DEFAULT_HINT = ("未注册错误码：查看 detail 字段定位问题；持续出现属工程"
                "缺陷，附 detail 提交问题报告")


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
    # 评测尺子族参数面（add-evaluation-ruler）
    "threshold": "zigzag 转折门槛非法：threshold 须为 (0,1) 开区间"
                 "小数（如 0.05=5%）",
    "warmup_bars": "warmup bar 数非法：须为非负整数（前扩只用不算，"
                   "不足不拒绝、以 boundary_effect 披露）",
    "data_range.end": "浪/箱体盘点无开放右端形制：data_range.end "
                      "须为显式 UTC 时间戳",
    "hypothesis_id": "行情结构底账不适用假设关联：移除 hypothesis_id"
                     "（底账不进判定并集、不作策略输入）",
    "significance.method": "显著性方法未知：method ∈ t_test_vs_baseline"
                           "（缺省，存量行为）|bootstrap_ci（bootstrap "
                           "均值差 CI）",
    "significance.B": "significance.B 须为正整数（bootstrap 重抽样次数）",
    "significance.seed": "significance.seed 须为整数（随机种子）",
    "window|min_len": "该轴无冻结默认：window 与 min_len 必填"
                      "（冻结默认仅 1h=(120,48)/15m=(240,96)）",
    "window": "window 须为正整数（滚动窗宽 bar 数）",
    "min_len": "min_len 须为正整数（成箱最小连续 bar 数）",
    "width_thr": "width_thr 须为 (0,1) 开区间小数（宽度门槛，含等号）",
    # 对照模板参数面（add-evaluation-ruler）
    "mode": "对照模式未知：ruler_compare mode ∈ wave（浪模式：识别率/"
            "捕获率/净均值CI）|box（箱体模式：先到对侧比例/三分类/"
            "月频）；ab_compare mode 固定 \"ab\"（A/B 双记录对照）",
    # A/B 对照参数面（sandbox-and-report-supplement B4——新校验面
    # 随第 16 模板登记；mode/trade_source 族键与 ruler_compare 共用）
    "dimension": "A/B 对照维度未知：dimension ∈ none（默认——仅两组"
                "指标+组间差）|wave（挂识别率/捕获率）|box（挂先到"
                "对侧比例）",
    "trade_source_a|trade_source_b": "双记录区间不等（两侧归一值在 "
                                     "detail 披露）：A/B 对照须两记录 "
                                     "data_range 全值相等——改用同区间"
                                     "记录重试",
    # 任务级封顶参数面（sandbox-and-report-supplement）：leverage 与
    # margin_cap 共用 require_task_cap_params 校验单源——扫描键按
    # detail.field 取（leverage 已有 PtTask 覆写先例面，此处补 margin_cap）
    "margin_cap": "margin_cap 非法：须为 (0,0.60] 小数（需求 60% 资金"
                  "使用上限硬约束封顶，恰 0.60 合法、0/负值拒绝）",
    "halt_drawdown_pct": "halt_drawdown_pct 非法：须为 (0,1) 开区间"
                         "回撤比率（0/1/负值拒绝——熔断保命参数，"
                         "未配置=不启用）",
    "trade_source": "交易清单来源形制非法：唯一合法 {kind:"
                    "\"validation\", record_id:\"rec_...\"}"
                    "（存量隔离验证记录）",
    "trade_source.kind": "kind 拒绝：仅 kind=\"validation\" 合法（沙盒"
                        "记录 params 无单区间形制，不可对照）——对照"
                        "指标请引用存量隔离验证记录 record_id"
                        "（未知 kind 附合法形制）",
    "trade_source.record_id": "验证记录不存在：record_id 须为存量隔离"
                             "验证记录（rec_... 前缀，经验证记录列表"
                             "查询核对）",
    "trade_source.record.range": "验证记录区间与评估区间不一致（归一后"
                                "须全值相等，不静默截断）——改用一致"
                                "区间重试",
    "trade_source.record.init_cash": "引用记录缺实际生效 init_cash"
                                    "（净均值分母）——非标准记录不可"
                                    "对照",
    "trades": "逐笔时间越界（seq+时间在 detail 披露）：对照区间须"
              "完整覆盖全部交易，不做静默截断",
    "edge_window": "边沿滚动窗宽非法：须为正整数（1h=240/15m=480 "
                  "冻结默认，其余轴必填）",
    "edge_q_low|edge_q_high": "分位参数非法：0 < edge_q_low < "
                             "edge_q_high < 1（默认 0.10/0.90）",
    "edge_q_low": "edge_q_low 须为 (0,1) 开区间小数（下分位沿，"
                  "默认 0.10；另须 edge_q_low < edge_q_high）",
    "edge_q_high": "edge_q_high 须为 (0,1) 开区间小数（上分位沿，"
                   "默认 0.90；另须 edge_q_low < edge_q_high）",
    "buffer": "buffer 须为非负数（推定止损缓冲，默认 0.25）",
    "identify_window": "identify_window 须在 (0,1]（识别窗口占比，"
                       "默认 0.3）",
    "B": "B 须为 1~100000 正整数（bootstrap 重抽样次数）",
    "seed": "seed 须为整数（随机种子）",
    # 转化执行周期（fix-mtf-execution-consistency R2）：A1 异周期继承
    # 拒绝的 detail.param 面
    "strategy_timeframe": "转化执行周期无法缺省继承（data_ranges 周期"
                          "不一致）：显式传 strategy_timeframe 指定执行"
                          "周期，或修正假设 data_ranges 后重新登记",
    # 转化品种（fr_296f43410afb 修复①，sandbox-and-report-supplement
    # 块 C）：symbol 缺省继承守卫拒绝的 detail.param 面（畸形
    # data_ranges/首条目缺 symbol 键）
    "strategy_symbol": "转化品种无法缺省继承（data_ranges 无可用 "
                       "symbol）：显式传 strategy_symbol 指定策略品种，"
                       "或修正假设 data_ranges 后重新登记",
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
                     "（每笔风险预算占当时权益比例，闭区间上界 0.5）",
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
    # 止损显式声明强制（research-execution-config-alignment D5②：
    # stop_loss 键在场判据的 detail field 面）
    "strategy_params.stop_loss": "止损为强制风控组件：登记声明块必须"
                                 "显式携带 stop_loss（pct/trail 档比例值"
                                 " [0.005,0.20]/atr 档 ATR 倍数 [0.5,5.0]，"
                                 "legal_range 按 sl_mode 分叉）",
    # series 档来源声明族（dynamic-stop-loss-series 4.1：组合四断言+
    # 两形制互斥+静态校验的 detail field 面——six 键同族共用语义）
    "strategy_params.sl_mode": "series 档组合约束：sl_mode=series 须携"
                               "带 sl_series_source 来源声明且与标量 "
                               "stop_loss/risk_budget 互斥（其余档位不"
                               "得携带来源键）",
    "strategy_params.sl_series_source": "series 档止损线来源声明非法："
                                        "两形制互斥（指标形制 "
                                        "{indicator, params, field?} 或 "
                                        "fx 形制 {column} 二选一）、"
                                        "sl_mode=series 档专属",
    "strategy_params.sl_series_source.indicator": "来源指标不在注册表："
                                                  "sl_series_source."
                                                  "indicator 须为 "
                                                  "INDICATOR_REGISTRY "
                                                  "在册指标名",
    "strategy_params.sl_series_source.params": "来源指标参数形状非法："
                                               "params 须为对象且键属该"
                                               "指标的参数空间",
    "strategy_params.sl_series_source.field": "来源输出列非法：field 须"
                                              "属于该指标的 output_fields"
                                              "（多输出指标必填）",
    "strategy_params.sl_series_source.column": "fx 形制列名非法：column "
                                               "须为 fx_ 前缀精确列名且"
                                               "注册表可达",
    "grid.modes[].sl_series_source": "exit_sweep series 档 entry 须携带"
                                     "sl_series_source（指标或 fx 形制）"
                                     "——空 stops 合法（线值经列给出）",
    "grid.modes[].sl_series_source.column": "fx 形制列名非法：column 须为"
                                           "fx_ 前缀精确列名（exit_sweep "
                                           "grid 校验面与登记面同判）",
    # 信号组合参数面（fix-silent-degradation R-7）：combine_signals
    # 未知 logic 显式拒绝的 detail.param 面（原静默首信号副本收口）
    "logic": "logic 取值非法：检查 DNA logic_genes 的 entry_logic/"
             "exit_logic/add_logic/reduce_logic 与 cross_layer_logic，"
             "合法值 AND/OR",
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
