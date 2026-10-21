"""披露面锚清单（数据模块——非测试文件，_ 前缀不被 pytest 收集）。

承载 mcp-server spec「披露面锚清单全检」的锚数据（change
2026-10-21-mcp-disclosure-remediation-and-anchor-inventory）：四个
披露位置（总述句清单/各模板段落/能力自述条目/条目内能力域句）的
全部既有清单成员、边界句、条目要素。断言引擎 test_disclosure_
anchors.py 按本清单遍历断言——披露面文本漂移即红；能力扩容的
披露维护动作=向本清单加行（完备性元断言强制新披露必须登记，
防「全量收录」退化为人工记得）。

取材纪律（两断言源）：tools 条目的截段锚基于 tools.py 源码文本
的 squash 域（@mcp.tool() 闭包无模块属性）；capability 条目基于
CAPABILITY_DOC 模块属性直读。截段锚均为 squash 归一后的精确子串
（起点锚、终点锚各须在场，段域=[起点, 终点)）。

条目五元组：anchor_id × location（source+截段起止锚）× kind
（token_set 要素在场 / list_members 清单成员集 / negative_clear
负向清零）× expectation（期望 token 集或成员集）× scope（辖域
标签）+ basis（权威源依据：设计卡文案编号或实现层路径）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DisclosureAnchor:
    """单条披露锚：断言引擎按 source 取材、截段、按 kind 断言。"""

    anchor_id: str
    source: str                 # "tools" | "capability"
    seg_start: str              # squash 域截段起点锚
    seg_end: str                # squash 域截段终点锚
    kind: str                   # token_set | list_members | negative_clear
    tokens: tuple = ()          # token_set/negative_clear 期望或禁入 token
    members: tuple = ()         # list_members 期望成员集
    list_pattern: str = ""      # list_members 清单串提取正则（squash 域）
    covered: tuple = ()         # 该锚登记披露的模板名（完备性①覆盖面）
    scope: str = ""             # 辖域标签（不同语义清单串按辖域分锚）
    basis: str = ""             # 权威源依据（设计卡文案编号/实现路径）


# ---------------------------------------------------------------------------
# 组 1：组合条件族四位置全量（既有披露面现状守护）
# ---------------------------------------------------------------------------

# 总述段段域（tools.py run_analysis docstring 组合条件总述段——
# condition_stats/event_study 的组合条件细节唯一承载面；起点锚含
# 清单串本体——清单成员提取须在段域内命中完整清单串）
_SEG_OVERVIEW = ("condition_stats/event_study/regime_split支持conditions条件数组形制",
                 "condition_stats支持params.forward_bars")
_SEG_REGIME = ("regime_split行情分段", "precheck_hypothesis假设预检")
_SEG_PARAMSWEEP = ("param_sweep参数稳健性检查", "regime_split行情分段")
_SEG_COUNTER = ("counter_profile反例画像", "exit_sweep退出参数扫描")
# exit_sweep 段终点=docstring 尾后首个 body 构造（「202+轮询」是段内
# 要素 token 不可作终点锚——段域右开会把它排除在外）
_SEG_EXIT = ("exit_sweep退出参数扫描", "body={")
_SEG_PRECHECK = ("precheck_hypothesis假设预检", "segment_narrative区间事件摘要")
_SEG_SCREEN = ("screen_patterns全谱扫描", "param_sweep参数稳健性检查")
_SEG_SEGMENT = ("segment_narrative区间事件摘要", "counter_profile反例画像")
_SEG_ASYNC = ("同步/异步分界：", "screen_patterns全谱扫描")
_SEG_REQUIRED = ("params必填键（缺键422detail含键名）：", "支持conditions条件数组形制")
_SEG_ACTIONS = ("当前可用比较动作全集", "同步/异步分界：")
# 能力自述条目段域（capability.py CAPABILITY_DOC）
_CSEG_COMBO = ("组合条件（0.4.1起；regime_split2026-10-14起）", "区间事件摘要")
_CSEG_PLUGINS = ("分析模板插件列条件（fix-research-c3起；regime_split2026-10-14起）",
                 "纯公式进场形制")
_CSEG_COUNTER = ("反例画像（0.4.1counter_profile）", "退出参数扫描（0.4.1exit_sweep）")
_CSEG_EXIT = ("退出参数扫描（0.4.1exit_sweep）", "分段验证")
_CSEG_TRIGGER = ("事件策略层（trigger_policy）", "沙盒回测纪律")

# B1 清单串提取正则（与 test_contract_parity._conditions_support_list 同形制）
_RE_CONDITIONS_LIST = r"condition_stats/event_study(?:/\w+)*支持conditions"
# A5 清单串提取正则（插件列条目「……的条件引用插件列」）
_RE_PLUGINS_LIST = r"([\w]+(?:/\w+)*)的条件引用插件列"

GROUP1_ANCHORS: tuple = (
    # —— 四位置既有清单（绿态现状守护）——
    DisclosureAnchor(
        anchor_id="G1-总述句清单-组合条件三成员",
        source="tools", seg_start=_SEG_OVERVIEW[0], seg_end=_SEG_OVERVIEW[1],
        kind="list_members", list_pattern=_RE_CONDITIONS_LIST,
        members=("condition_stats", "event_study", "regime_split"),
        scope="组合条件支持面", basis="A1/B1 同款（test_contract_parity 既有）",
    ),
    DisclosureAnchor(
        anchor_id="G1-总述段-既有要素",
        source="tools", seg_start=_SEG_OVERVIEW[0], seg_end=_SEG_OVERVIEW[1],
        kind="token_set",
        tokens=("二选一", "互斥", "1..5", "AND", "timeframe",
                "形制间边界差异", "并集", "「只用不算」"),
        covered=("condition_stats", "event_study"),
        scope="组合条件总述段", basis="tools.py:341-353 现状（0.4.1 披露）",
    ),
    DisclosureAnchor(
        anchor_id="G1-regime_split段-七要素",
        source="tools", seg_start=_SEG_REGIME[0], seg_end=_SEG_REGIME[1],
        kind="token_set",
        tokens=("conditions", "二选一", "互斥", "condition|conditions", "1..5",
                "AND", "timeframe", "并集", "插件列",
                "params.indicator+params.condition"),
        covered=("regime_split",),
        scope="regime_split 段落", basis="A2 同款（test_contract_parity 既有）",
    ),
    DisclosureAnchor(
        anchor_id="G1-能力自述组合条件条目-三成员",
        source="capability", seg_start=_CSEG_COMBO[0], seg_end=_CSEG_COMBO[1],
        kind="list_members", list_pattern=_RE_CONDITIONS_LIST,
        members=("condition_stats", "event_study", "regime_split"),
        scope="组合条件支持面", basis="A3/B1 同款",
    ),
    DisclosureAnchor(
        anchor_id="G1-能力自述插件列条目-六成员",
        source="capability", seg_start=_CSEG_PLUGINS[0], seg_end=_CSEG_PLUGINS[1],
        kind="list_members", list_pattern=_RE_PLUGINS_LIST,
        members=("condition_stats", "event_study", "inspect_cases",
                 "param_sweep", "counter_profile", "regime_split"),
        scope="插件列条件支持面", basis="A5 同款（fix-research-c3 五成员+regime_split）",
    ),
    # —— 模板段落既有披露（12 模板段落在场锚——完备性①覆盖面）——
    DisclosureAnchor(
        anchor_id="G1-必填键段-无专属段模板登记",
        source="tools", seg_start=_SEG_REQUIRED[0], seg_end=_SEG_REQUIRED[1],
        kind="token_set",
        tokens=("condition_stats须indicator/params/condition",
                "distribution_compare须column/group_a/group_b",
                "correlation_scan须signal_column", "event_study须condition",
                "pattern_mining须as_of", "param_sweep须indicator+condition",
                "screen_patterns/regime_split无必填键"),
        covered=("distribution_compare", "correlation_scan", "pattern_mining"),
        scope="params 必填键段", basis="tools.py:335-340 ×路由 _TEMPLATE_REQUIRED_KEYS",
    ),
    DisclosureAnchor(
        anchor_id="G1-同步异步段-pattern_mining恒异步",
        source="tools", seg_start=_SEG_ASYNC[0], seg_end=_SEG_ASYNC[1],
        kind="token_set",
        tokens=("pattern_mining与screen_patterns恒异步", "10000", "_HEAVY_ROWS"),
        covered=(),
        scope="同步/异步分界段", basis="tools.py:370-373 ×_HEAVY_ROWS 常量",
    ),
    DisclosureAnchor(
        anchor_id="G1-screen_patterns段-skipped三值",
        source="tools", seg_start=_SEG_SCREEN[0], seg_end=_SEG_SCREEN[1],
        kind="token_set",
        tokens=("no_default_params", "compute_failed", "no_covered_actions",
                "skipped_indicators", "FDR", "q值"),
        covered=("screen_patterns",),
        scope="screen_patterns 段落",
        basis="reason 封闭枚举三值（tools.py:377-380）",
    ),
    DisclosureAnchor(
        anchor_id="G1-segment_narrative段-事件四类",
        source="tools", seg_start=_SEG_SEGMENT[0], seg_end=_SEG_SEGMENT[1],
        kind="token_set",
        tokens=("pattern_trigger", "indicator_cross", "band_position",
                "sustained_run", "5000"),
        covered=("segment_narrative",),
        scope="segment_narrative 段落", basis="事件四类（tools.py:451-456）",
    ),
    DisclosureAnchor(
        anchor_id="G1-counter_profile段-既有要素",
        source="tools", seg_start=_SEG_COUNTER[0], seg_end=_SEG_COUNTER[1],
        kind="token_set",
        tokens=("condition|conditions", "direction", "forward_bars",
                "Cohen'sd", "insufficient", "工具只呈现差异不生成条件"),
        covered=("counter_profile",),
        scope="counter_profile 段落", basis="A4 既有披露（0.4.1 R5）",
    ),
    DisclosureAnchor(
        anchor_id="G1-exit_sweep段-既有要素",
        source="tools", seg_start=_SEG_EXIT[0], seg_end=_SEG_EXIT[1],
        kind="token_set",
        tokens=("condition|conditions", "direction", "grid=", "0.4.1={pct}",
                "三道门", "不挂假设", "202+轮询"),
        covered=("exit_sweep",),
        scope="exit_sweep 段落",
        basis="A4 既有披露（0.4.1 R6——不挂假设句 :471 保留硬约束）",
    ),
    DisclosureAnchor(
        anchor_id="G1-precheck段-既有要素",
        source="tools", seg_start=_SEG_PRECHECK[0], seg_end=_SEG_PRECHECK[1],
        kind="token_set",
        tokens=("when_conditions", "validate_range", "timeframe",
                "projected_trades", "reject_suggestion", "trigger_policy"),
        covered=("precheck_hypothesis",),
        scope="precheck_hypothesis 段落",
        basis="既有披露（tools.py:418-445——timeframe 句 :420-421）",
    ),
    DisclosureAnchor(
        anchor_id="G1-param_sweep段-既有要素",
        source="tools", seg_start=_SEG_PARAMSWEEP[0], seg_end=_SEG_PARAMSWEEP[1],
        kind="token_set",
        tokens=("params.indicator", "params.condition", "params.sweep",
                "forward_bars", "三道门", "plateau|spike|indeterminate|"
                "unsupported", "判定参数不可传"),
        covered=("param_sweep",),
        scope="param_sweep 段落", basis="既有披露（tools.py:387-398）",
    ),
    DisclosureAnchor(
        anchor_id="G1-能力自述反例画像条目-既有要素",
        source="capability", seg_start=_CSEG_COUNTER[0], seg_end=_CSEG_COUNTER[1],
        kind="token_set",
        tokens=("Cohen'sd", "insufficient", "工具只呈现差异不生成条件",
                "小样本拒绝"),
        scope="反例画像条目", basis="既有披露（capability.py:124-129）",
    ),
    DisclosureAnchor(
        anchor_id="G1-能力自述退出参数扫描条目-既有要素",
        source="capability", seg_start=_CSEG_EXIT[0], seg_end=_CSEG_EXIT[1],
        kind="token_set",
        tokens=("Σ≤50组", "0.4.1={pct}", "不挂假设", "202+轮询"),
        scope="退出参数扫描条目", basis="既有披露（capability.py:130-139）",
    ),
)

# ---------------------------------------------------------------------------
# 组 1（续）：五文案目标锚（红态先行——组 2 文案落地后转绿）
# ---------------------------------------------------------------------------

# 能力域句五要素（文案三②/文案四同款——两处要素一致，三面口径一致 MUST）
_DOMAIN_SENTENCE_TOKENS = (
    "可携带timeframe键",      # ①条件条目可携带 timeframe 键（能力域句本体）
    "等于基准周期",           # ①a 等基准归一（v1.3 对质增量）
    "键归一",                 # ①b 显式写基准周期不构成高周期过滤
    "掩码投影",               # ②严格高于基准周期经掩码投影求值
    "TIMEFRAME_NOT_HIGHER",   # ③低于基准周期拒绝
    "RESEARCH_NOT_EXPRESSIBLE",  # ④不可表达形制显式拒绝不静默降级
    "不挂假设",               # ⑤既有句保留（tools 侧；capability 条目含同款）
)

GROUP1_TARGET_ANCHORS: tuple = (
    DisclosureAnchor(
        anchor_id="W1-总述段-TD1互斥边界句",
        source="tools", seg_start=_SEG_OVERVIEW[0], seg_end=_SEG_OVERVIEW[1],
        kind="token_set",
        tokens=("conditions条目中插件列引用", "fx_/model_", "不可同用",
                "RESEARCH_CONDITION_INVALID", "plugin_columns",
                "移除timeframe键（基准周期求值）或改用内置指标列引用",
                "高周期轴求值语义未定义"),
        scope="TD-1 互斥边界（conditions 条目域限定——A-2）",
        basis="文案一（R1）×analysis.py:128-143 实现层 422 detail/hint",
    ),
    DisclosureAnchor(
        anchor_id="W2-能力自述反例画像条目-二选一与timeframe",
        source="capability", seg_start=_CSEG_COUNTER[0], seg_end=_CSEG_COUNTER[1],
        kind="token_set",
        tokens=("condition|conditions", "可含高周期timeframe键", "掩码投影"),
        scope="TD-2+timeframe 承接",
        basis="文案二（R2+R6）×tools.py:458-459 形制+counter_profile.py:133",
    ),
    DisclosureAnchor(
        anchor_id="W3-能力自述退出参数扫描条目-二选一",
        source="capability", seg_start=_CSEG_EXIT[0], seg_end=_CSEG_EXIT[1],
        kind="token_set",
        tokens=("condition|conditions",),
        scope="TD-2 条目侧",
        basis="文案三①（R2）×tools.py:470-472 段落形制",
    ),
    DisclosureAnchor(
        anchor_id="W3-能力自述退出参数扫描条目-能力域句五要素",
        source="capability", seg_start=_CSEG_EXIT[0], seg_end=_CSEG_EXIT[1],
        kind="token_set", tokens=_DOMAIN_SENTENCE_TOKENS,
        scope="TD-3 条目侧（对照句消亡）",
        basis="文案三②（R3）×exit_sweep.py docstring+test_exit_sweep.py:547",
    ),
    DisclosureAnchor(
        anchor_id="W4-exit_sweep段-能力域句五要素",
        source="tools", seg_start=_SEG_EXIT[0], seg_end=_SEG_EXIT[1],
        kind="token_set", tokens=_DOMAIN_SENTENCE_TOKENS,
        scope="TD-3 段落侧（对照句消亡）",
        basis="文案四（R3）×test_exit_sweep.py:547+test_condition_identity.py:110",
    ),
    DisclosureAnchor(
        anchor_id="W5-param_sweep段-timeframe补教",
        source="tools", seg_start=_SEG_PARAMSWEEP[0], seg_end=_SEG_PARAMSWEEP[1],
        kind="token_set",
        tokens=("可携带可选高周期timeframe键", "基准周期", "掩码投影",
                "低周期拒绝"),
        scope="第五缺口（spec:362 转述）",
        basis="文案五（R6）×ai-research-tools spec:362+param_sweep.py:284",
    ),
)

# ---------------------------------------------------------------------------
# 组 2：timeframe 能力域披露面互拍（方案甲——六落点描述面×事实面）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeframeFace:
    """单落点：描述面（截段+支持 token 集）× 事实面（模板集）。

    互拍语义：描述面 token 集齐备（=该落点教「条件支持 timeframe」）
    当且仅当 事实面该模板集全部经代码行为提取为支持——任一侧漂移
    （教了不支持/支持了不教）即红。
    """

    face_id: str
    source: str                # "tools" | "capability"
    seg_start: str
    seg_end: str
    tokens: tuple              # 描述面支持语义 token 集（段域内齐备=教支持）
    fact_templates: tuple      # 事实面模板集（提取器须全支持）
    basis: str = ""


TIMEFRAME_FACES: tuple = (
    TimeframeFace(
        face_id="落点1-总述段句",
        source="tools", seg_start=_SEG_OVERVIEW[0], seg_end=_SEG_OVERVIEW[1],
        tokens=("含可选高周期timeframe键",),
        fact_templates=("condition_stats", "event_study", "regime_split"),
        basis="总述段 :347 既有句（condition_stats/event_study 唯一承载面）",
    ),
    TimeframeFace(
        face_id="落点2-regime_split段",
        source="tools", seg_start=_SEG_REGIME[0], seg_end=_SEG_REGIME[1],
        tokens=("含可选高周期timeframe键", "掩码投影求值"),
        fact_templates=("regime_split",),
        basis="regime_split 段 :411 既有句",
    ),
    TimeframeFace(
        face_id="落点3-param_sweep段",
        source="tools", seg_start=_SEG_PARAMSWEEP[0], seg_end=_SEG_PARAMSWEEP[1],
        tokens=("可携带可选高周期timeframe键", "基准周期"),
        fact_templates=("param_sweep",),
        basis="文案五补后（spec:362 转述）",
    ),
    TimeframeFace(
        face_id="落点4-counter_profile条目",
        source="capability", seg_start=_CSEG_COUNTER[0], seg_end=_CSEG_COUNTER[1],
        tokens=("可含高周期timeframe键",),
        fact_templates=("counter_profile",),
        basis="文案二补后（对照句消亡后自述承接）",
    ),
    TimeframeFace(
        face_id="落点5-exit_sweep能力域句-段落侧",
        source="tools", seg_start=_SEG_EXIT[0], seg_end=_SEG_EXIT[1],
        tokens=("可携带timeframe键", "等于基准周期"),
        fact_templates=("exit_sweep",),
        basis="文案四改后（翻译链同款放行——T10 现役）",
    ),
    TimeframeFace(
        face_id="落点5-exit_sweep能力域句-条目侧",
        source="capability", seg_start=_CSEG_EXIT[0], seg_end=_CSEG_EXIT[1],
        tokens=("可携带timeframe键", "等于基准周期"),
        fact_templates=("exit_sweep",),
        basis="文案三②改后（与段落侧两处要素一致）",
    ),
    TimeframeFace(
        face_id="落点6-precheck既有句",
        source="tools", seg_start=_SEG_PRECHECK[0], seg_end=_SEG_PRECHECK[1],
        tokens=("登记同构混合形制含可选timeframe键",),
        fact_templates=("precheck_hypothesis",),
        basis="precheck 段 :420-421 既有句",
    ),
)

# ---------------------------------------------------------------------------
# 组 2（续）：过期语义清零（负向 token 集——两声明文件显式集，禁全仓）
# ---------------------------------------------------------------------------
# 封闭枚举四判据（与 design 口径一致；排除「掩码路径支持」假红词——
# 新能力域句「经掩码投影」不含该四字连写，不误伤）：
#   ①「MUST NOT 携带 timeframe」——tools 旧对照句直接命中措辞（squash 形）
#   ②「不支持 timeframe 键」——capability 旧对照句直接命中措辞（带键字）
#   ③「MUST NOT 支持高周期」/「高周期条件显式拒绝」——spec 旧语义句式族
#   ④「condition_stats/counter_profile」——跨模板对照句清单形制指纹
#      （紧邻清单串——合法清单串不产生该紧邻：组合条件清单含
#      event_study、插件列清单含 param_sweep/counter_profile 相邻序）
NEGATIVE_CLEAR_TOKENS: tuple = (
    "MUSTNOT携带timeframe",
    "不支持timeframe键",
    "MUSTNOT支持高周期",
    "高周期条件显式拒绝",
    "condition_stats/counter_profile",
)

NEGATIVE_CLEAR_ANCHORS: tuple = (
    DisclosureAnchor(
        anchor_id="清零-tools声明面-旧对照句式族",
        source="tools", seg_start="", seg_end="",  # 空段域=全文件 squash 域
        kind="negative_clear", tokens=NEGATIVE_CLEAR_TOKENS,
        scope="tools.py 声明面（显式集，禁全仓）",
        basis="D-1 对照句消亡（TD-3）+旧句式族封闭枚举四判据",
    ),
    DisclosureAnchor(
        anchor_id="清零-capability声明面-旧对照句式族",
        source="capability", seg_start="", seg_end="",
        kind="negative_clear", tokens=NEGATIVE_CLEAR_TOKENS,
        scope="capability.py 声明面（显式集，禁全仓）",
        basis="同上（两文件各自清零判据独立实证）",
    ),
)

# ---------------------------------------------------------------------------
# 组 3：高频漂移清单句（比较动作全集/trigger_policy/模式集/枚举族）
# ---------------------------------------------------------------------------

GROUP3_ANCHORS: tuple = (
    DisclosureAnchor(
        anchor_id="G3-比较动作全集段-21动作与legal_values",
        source="tools", seg_start=_SEG_ACTIONS[0], seg_end=_SEG_ACTIONS[1],
        kind="token_set",
        tokens=("gt/lt/ge/le", "cross_above/cross_below", "breakout",
                "price_above/price_below", "touch", "spike", "shrink",
                "divergence_top", "divergence_bottom", "consecutive_up",
                "consecutive_down", "cross_above_series", "cross_below_series",
                "lookback_any", "touch_bounce", "role_reversal",
                "legal_values"),
        scope="比较动作全集（fix-research-c2 起）",
        basis="tools.py:362-369 ×动作分派表键集",
    ),
    DisclosureAnchor(
        anchor_id="G3-能力自述事件策略层条目-三枚举",
        source="capability", seg_start=_CSEG_TRIGGER[0], seg_end=_CSEG_TRIGGER[1],
        kind="token_set",
        tokens=("none", "first_true", "suppress_bars", "不创造触发",
                "同一权威变换", "不静默降级"),
        scope="trigger_policy 三面（与存量锚双断言并存）",
        basis="capability.py:38-49 ×TestTriggerPolicyDisclosure 互补",
    ),
    DisclosureAnchor(
        anchor_id="G3-exit_sweep段-模式合法集与direction枚举",
        source="tools", seg_start=_SEG_EXIT[0], seg_end=_SEG_EXIT[1],
        kind="token_set",
        tokens=("0.4.1={pct}", "引擎未支持", "long/", "short"),
        scope="exit_sweep 模式集{pct}+direction 枚举",
        basis="tools.py:477-478/:472-475（引擎真实分派集引用式）",
    ),
    DisclosureAnchor(
        anchor_id="G3-counter_profile段-direction封闭枚举",
        source="tools", seg_start=_SEG_COUNTER[0], seg_end=_SEG_COUNTER[1],
        kind="token_set",
        tokens=("封闭枚举long/short", "mixed是执行概念不属研究主张"),
        scope="direction 枚举（counter_profile 侧）",
        basis="tools.py:460-461（包四 GAP-01）",
    ),
)

# ---------------------------------------------------------------------------
# 组 4：五文案残余漂移面补强（tester 建网——fix/mcp-disclosure-review-anchor）
# ---------------------------------------------------------------------------
# 收录依据：W1/W5/W3/W4 目标锚与组 1 既有锚之外的落地文案语义承重墙——
# 建网核对发现五文案落地句存在未被任何锚钉住的要素（delta-3「边界句
# 全量入锚」契约面），逐 token 实测在场后收录（现状记录语义）。

# 能力域句精确限定 token 集（文案三②/文案四同款句共享——与
# _DOMAIN_SENTENCE_TOKENS 并存互补：后者钉五要素骨架，本集钉三分支
# 结构词、等基准歧义防线（delta-2 三分支精确表述要件）与防线动词、
# 机制来源引用）
_DOMAIN_PRECISION_TOKENS = (
    "语义三分支",           # 三分支结构词（等基准/严格高/低周期三分）
    "严格高于基准周期",      # 等基准歧义防线（排除「>=」笼统表述回潮）
    "不静默降级",           # 防线动词（显式拒绝的对面——丢了只剩错误码罗列）
    "同款放行",             # 机制来源引用（与策略翻译链同源放行）
    "同一公共入口",          # 防线结构声明（translate_hypothesis 公共入口）
)

GROUP4_ANCHORS: tuple = (
    DisclosureAnchor(
        anchor_id="W1b-总述段-互斥机制理由",
        source="tools", seg_start=_SEG_OVERVIEW[0], seg_end=_SEG_OVERVIEW[1],
        kind="token_set",
        tokens=("只注入基准周期数据",),
        scope="TD-1 互斥机制理由（插件列数据域——为何互斥非任意规则）",
        basis="文案一机制半句（W1 钉结论面/三要素，本锚钉理由面）",
    ),
    DisclosureAnchor(
        anchor_id="W2b-能力自述反例画像条目-形制对齐句",
        source="capability", seg_start=_CSEG_COUNTER[0], seg_end=_CSEG_COUNTER[1],
        kind="token_set",
        tokens=("形制与条件统计一致",),
        scope="TD-2a 形制对齐声明（W2 只钉 condition|conditions 符号）",
        basis="文案二①措辞骨架×tools.py:458-459 段落既有句对齐",
    ),
    DisclosureAnchor(
        anchor_id="W3b-能力自述退出参数扫描条目-形制对齐句",
        source="capability", seg_start=_CSEG_EXIT[0], seg_end=_CSEG_EXIT[1],
        kind="token_set",
        tokens=("形制与条件统计一致",),
        scope="TD-2b 形制对齐声明（W3-二选一只钉符号）",
        basis="文案三①措辞骨架×tools.py:470-472 段落形制",
    ),
    DisclosureAnchor(
        anchor_id="W3c-能力自述退出参数扫描条目-能力域句精确限定",
        source="capability", seg_start=_CSEG_EXIT[0], seg_end=_CSEG_EXIT[1],
        kind="token_set", tokens=_DOMAIN_PRECISION_TOKENS,
        scope="TD-3 条目侧精确限定（W3-五要素之外的措辞承重墙）",
        basis="文案三②×delta-2 三分支精确表述要件（「>=」笼统表述歧义）",
    ),
    DisclosureAnchor(
        anchor_id="W4b-exit_sweep段-能力域句精确限定",
        source="tools", seg_start=_SEG_EXIT[0], seg_end=_SEG_EXIT[1],
        kind="token_set", tokens=_DOMAIN_PRECISION_TOKENS,
        scope="TD-3 段落侧精确限定（W4-五要素之外的措辞承重墙）",
        basis="文案四×delta-2 三分支精确表述要件（与条目侧两处一致）",
    ),
    DisclosureAnchor(
        anchor_id="W5b-param_sweep段-限定与互斥转述",
        source="tools", seg_start=_SEG_PARAMSWEEP[0], seg_end=_SEG_PARAMSWEEP[1],
        kind="token_set",
        tokens=("仅>=请求基准周期", "语义与假设登记的多周期条件一致",
                "插件列引用与timeframe键不可同用",
                "422边界见conditions总述句"),
        scope="第五缺口补教残余面（W5/落点3 只钉 timeframe 支持语义，"
              "不钉限定形与互斥转述）",
        basis="文案五后半句（设计卡「同句可含互斥边界提示」落地物——"
              "param_sweep 单条件在互斥校验六分支覆盖域内，A-2）×"
              "「仅>=」限定与假设登记语义对照引用",
    ),
)

# ---------------------------------------------------------------------------
# 完备性元断言登记面（双向相等——「全检」名实相符的机制核心）
# ---------------------------------------------------------------------------

# ①模板披露登记键集：锚条目 covered 并集 == run_analysis docstring
# 模板枚举提取集（12 模板）。新模板披露出现未登记即红；删登记行即红。
REGISTERED_TEMPLATES: tuple = (
    "condition_stats", "distribution_compare", "correlation_scan",
    "event_study", "pattern_mining", "screen_patterns", "param_sweep",
    "regime_split", "precheck_hypothesis", "segment_narrative",
    "counter_profile", "exit_sweep",
)

# ②能力自述条目登记键集 ∪ 白名单 == CAPABILITY_DOC 条目标题提取集
# （28 条，squash 后 `- **xxx**：` 形制，首短语归一——取「（」前部分）。
# 登记域=本锚清单收录了条目锚的键；白名单=非披露锚登记域的既有条目
# （显式清单入本文件——变更须显式改文件，防逃逸口）。
CAPABILITY_ENTRY_ANCHORED: tuple = (
    "组合条件",            # G1-能力自述组合条件条目
    "分析模板插件列条件",   # G1-能力自述插件列条目
    "反例画像",            # W2/G1-能力自述反例画像条目
    "退出参数扫描",         # W3/G1-能力自述退出参数扫描条目
    "事件策略层",           # G3-能力自述事件策略层条目
)

CAPABILITY_ENTRY_WHITELIST: tuple = (
    "隔离纪律", "时间参数规约", "warmup推导式", "门槛机械判定",
    "组合策略加严", "同种子复现", "幂等", "沙盒回测纪律", "时点行情快照",
    "案例抽查", "指标读数", "批量读数", "指标参数修复", "比较动作全集",
    "纯公式进场形制", "区间事件摘要", "判定并集余量查询", "分段验证",
    "纸盘挂载", "策略层参数声明块", "分批条件组", "止损模式三分派",
    "pattern_mining为实验性",
)

ALL_ANCHORS: tuple = (GROUP1_ANCHORS + GROUP1_TARGET_ANCHORS
                      + NEGATIVE_CLEAR_ANCHORS + GROUP3_ANCHORS
                      + GROUP4_ANCHORS)
