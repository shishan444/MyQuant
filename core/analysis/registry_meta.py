"""指标元数据外化与信号列双源合并（add-ai-research-mcp 1.3）。

两个职责：
- ``indicator_metadata``：内置 INDICATOR_REGISTRY 只读外化，逐指标附
  lookback（周期类参数的注册表上限之和——warmup 推导
  ``max(200, 跨指标最大的周期类参数上限之和 × 3)`` 的依据，D5）。
- ``available_signal_columns``：内置注册表与插件表（signal_registry，
  仅插件行）运行时合并，条目标注来源——单一事实源纪律：内置指标
  权威在 core/features，禁止复制入库（D4），合并只在查询时刻发生。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from core.features.registry import INDICATOR_REGISTRY

# 周期类参数名（bar 数语义）：其注册表 max 参与 lookback 推导。
# 非周期类（std/multiplier/step/max_step/bins 等）不参与。
_PERIOD_LIKE_PARAMS = frozenset({
    "period", "fast", "slow", "signal", "k_period", "d_period",
    "ema_period", "atr_period", "lookback",
    # Pivot 确认延迟双参（add-session-structure-expression：bar 数语义
    # ——left+right 求和为 warmup 收敛需求）
    "left", "right",
})

# 小数比率语义列（add-threshold-unit-warning C-1）：值域量级以 1 为界的
# 无量纲量——阈值按小数写（2.33% → 0.0233），|target|>1 疑似单位误写。
# 明确不在此列的三族：百分数形制（ROC/VROC/TRIX，阈值以百分数计——
# TRIX 经 venv pandas_ta/momentum/trix.py:53/:70 实证 scalar 默认 100，
# indicators.py:269-272 调用不传 scalar，系 ×100 百分数形制）、
# 0-100/-100~100 标度（RSI/MFI/Stochastic/Williams %R/CMO/ADX/Aroon）、
# 无界统计量（OI_ZScore/FundingZScore 等 z 分数、CCI）；RVOL 为
# 倍数语义（2.0=放量两倍）同理排除。插件列无值域元数据，保守豁免。
_DECIMAL_RATIO_INDICATORS = frozenset({
    "CMF", "OI_ChangeRate", "OIPriceDivergence", "FundingPressure",
})
_DECIMAL_RATIO_BASE_COLUMNS = frozenset({"funding_rate"})


def is_decimal_ratio_subject(subject: Any) -> bool:
    """canonical 条件 subject 是否小数比率语义列（登记单位警告判定面）。

    输入为登记链第 3 步 canonical 形（ref_dict 产物）：指标类 dict
    {indicator, params, field} / base·plugin 类裸字符串。插件列
    （fx_*/model_*）不在 base 清单，天然落豁免——无需查
    signal_registry，判定保持纯静态（与插桩点只读零阻断自洽）。
    """
    if isinstance(subject, dict):
        return subject.get("indicator") in _DECIMAL_RATIO_INDICATORS
    return subject in _DECIMAL_RATIO_BASE_COLUMNS


def _indicator_lookback(defn: Any) -> int:
    """指标 lookback：周期类参数的注册表上限之和（保守取向，宁可多预热）。"""
    total = 0
    for key, param in defn.params.items():
        if key in _PERIOD_LIKE_PARAMS:
            total += int(param.max)
    return total


def indicator_metadata() -> List[Dict[str, Any]]:
    """内置 48 指标元数据只读外化（含 lookback 与参数空间）。"""
    out: List[Dict[str, Any]] = []
    for name, defn in INDICATOR_REGISTRY.items():
        out.append({
            "name": name,
            "category": defn.category,
            "params": {
                key: {
                    "type": p.type,
                    "min": p.min,
                    "max": p.max,
                    "default": p.default,
                    "step": p.step,
                }
                for key, p in defn.params.items()
            },
            "output_fields": list(defn.output_fields),
            "supported_conditions": list(defn.supported_conditions),
            "lookback": _indicator_lookback(defn),
            "source": "builtin",
        })
    return out


def derive_warmup(indicator_names: List[str], default_floor: int = 200) -> int:
    """warmup 推导式：max(200, 跨指标最大的周期类参数注册表上限之和 × 3)。

    Args:
        indicator_names: 本次实际用到的指标名（INDICATOR_REGISTRY 键）。
        default_floor: 下限保底（默认 200，D5）。

    Raises:
        KeyError: 引用不存在的指标（显式拒绝，禁止静默跳过）。
    """
    if not indicator_names:
        return default_floor
    max_period = 0
    for name in indicator_names:
        defn = INDICATOR_REGISTRY.get(name)
        if defn is None:
            raise KeyError(f"未知指标: {name}")
        max_period = max(max_period, _indicator_lookback(defn))
    return max(default_floor, max_period * 3)


def derive_warmup_full(
    db_path: Path,
    indicator_names: List[str],
    plugin_columns: Optional[set] = None,
    indicator_tfs: Optional[dict] = None,
    base_tf: Optional[str] = None,
) -> int:
    """三维 warmup 推导式：max(内置基准轴推导, 高周期轴折算, 插件, 200)。

    预检（add-hypothesis-precheck）与隔离回测 ``_derive_dna_warmup``
    的同源公共入口——两处共用同段代码即「预检 warmup 数值与隔离验证
    warmup 声明同源同值」的字面保证（specs R3 MUST）。

    插件维度按依赖闭包递归取 max（fx_b 引用 fx_a → 两窗口都计入）；
    plugin_columns_lookback 对不存在/非公式/已失效列静默跳过贡献 0
    （求值端显式拒绝兜底）——调用方 MUST 自行校验插件列 registry
    可达性（不可达 422 结构化拒绝），禁止依赖本函数的静默跳过判定面。
    仅含内置引用时与 derive_warmup 完全一致（floor 200 自带）。

    高周期轴维度（unblock-research-validation 基因级 timeframe）：
    indicator_tfs={轴周期: 该轴 (indicator, params) 引用清单（含
    target_indicator 并集）}——每轴按 high_tf_warmup_span（收敛粒度，
    indicator_convergence 单源不复制）算前扩时长，÷ 基准周期折算为
    基准 bar 并入主 max（与主公式 200 floor 显式分离；缺 base_tf 时
    该维跳过——兼容既有两参数调用面）。

    Args:
        db_path: 插件注册表所在库（插件列 lookback 需要）。
        indicator_names: 内置指标名清单（INDICATOR_REGISTRY 键）。
        plugin_columns: 插件列名集合（fx_*/model_* 精确名；空/None
            时跳过插件维度）。
        indicator_tfs: 基因级高周期轴分组（轴周期→(indicator,
            params) 引用清单）。
        base_tf: 基准周期（折算分母；None 时跳过高周期维度）。

    Raises:
        KeyError: 内置指标未知（derive_warmup 透传）。
    """
    warmup = derive_warmup(indicator_names)  # 自带 floor 200
    if plugin_columns:
        # 延迟 import（环规避）：plugin_service 依赖链不进本模块加载期
        from core.analysis.plugin_service import plugin_columns_lookback
        warmup = max(warmup, plugin_columns_lookback(db_path, set(plugin_columns)))
    if indicator_tfs and base_tf:
        from core.analysis.warmup_loader import TF_MINUTES
        base_minutes = TF_MINUTES.get(base_tf)
        if base_minutes:
            for tf_key, tf_refs in indicator_tfs.items():
                if not tf_refs or tf_key == base_tf:
                    continue
                tf_minutes = TF_MINUTES.get(tf_key)
                if not tf_minutes:
                    continue
                # 折算基数=high_tf_warmup_span（收敛粒度 max(实际参数×3,
                # 20)——indicator_convergence 单源不复制），÷ 基准周期折
                # 基准 bar（design 2.5 契约）；与主公式 derive_warmup 的
                # 200 floor 显式分离（两式语义刻意不同，见 mtf_alignment
                # 模块 docstring——此前误用主式使 RSI(14)@1d 按 200 而非
                # 42 bar 折算）。函数内延迟 import 环规避（同 plugin 先例
                # ——mtf_alignment 函数内反向 import 本模块）
                from core.data.mtf_alignment import high_tf_warmup_span
                span = high_tf_warmup_span(list(tf_refs), tf_key)
                warmup = max(
                    warmup,
                    int(span.total_seconds() // (base_minutes * 60)))
    # 分钟语义预热维（add-session-structure-expression 任务 2）：
    # 指标级 warmup_minutes（自然分钟——如 SessionRange 1440=完整
    # 一天）按 base_tf 折算 bar=ceil(minutes ÷ 周期分钟) 并入主 max；
    # base_tf 缺席（存量两参调用面）该维不参与——floor 200 不虚增
    if base_tf:
        from core.analysis.warmup_loader import TF_MINUTES
        import math
        base_minutes = TF_MINUTES.get(base_tf)
        if base_minutes:
            for name in (indicator_names or []):
                defn = INDICATOR_REGISTRY.get(name)
                # warmup_minutes 为 dataclass 定义字段（恒存在，None=无）
                minutes = defn.warmup_minutes if defn else None
                if minutes:
                    warmup = max(
                        warmup,
                        math.ceil(minutes / base_minutes))
            # 轴引用为 (indicator, params) 元组清单（_gene_tf_axis_refs
            # 单源形制）——分钟维取指标名；warmup_minutes 是**时间需求**
            # （自然分钟，与求值轴无关），折算基准 bar 用 base_minutes
            # ——此前误除轴分钟产出轴 bar 数（返回值语义=基准 bar 数），
            # 1m 轴@1h 基准虚增 / 1h 轴@1m 基准欠账双向偏差
            #（fix-review D-2）
            for tf_refs in (indicator_tfs or {}).values():
                for ref in tf_refs:
                    name = ref[0] if isinstance(ref, (tuple, list)) else ref
                    defn = INDICATOR_REGISTRY.get(name)
                    minutes = defn.warmup_minutes if defn else None
                    if minutes:
                        warmup = max(
                            warmup, math.ceil(minutes / base_minutes))
    return warmup


# warmup 推导规则的唯一权威文本（0.3.3 G5：四披露面——错误 detail /
# 能力自述 / list_indicators 元数据 / 隔离回测 warmup.basis——同口径；
# mcpserver 文本层经 REST 分层不直接 import，由 test_contract_parity
# 锚强制子串一致）。忠实实现口径：_indicator_lookback 为周期类参数
# 上限**求和**——多周期参数指标按各周期参数求和（保守取向），非只
# 取最大单一周期参数。
WARMUP_RULE_TEXT = (
    "主公式 max(200, 跨指标取最大的(周期类参数注册表上限之和 × 3))"
    "——单指标按其周期类参数上限求和(保守取向)；"
    "高周期条件掩码链 max(条件实际参数 × 3, 20)，bar 数按该轴周期折算"
    "为前扩时长（1d 轴 42 bar = 42 天，非 42 根基准 bar；下限 20 与"
    "主公式下限 200 分属两式）；"
    "策略链含公式插件时取 max(内置指标推导, plugin_formula_lookback)；"
    "基因级周期维度：各高周期轴 warmup bar 数 × 轴周期 ÷ 基准周期"
    "折算为基准 bar 并入主 max（1d 轴 42 bar @1h 基准 = 1008 基准 bar）；"
    "分钟语义预热维：指标级 warmup_minutes（自然分钟，如 SessionRange 1440=完整一天）按基准周期折算 bar=ceil(分钟 ÷ 周期分钟) 并入主 max"
)


def indicator_convergence(indicator: str, params: Dict[str, Any]) -> int:
    """指标真实收敛需求（条件实际参数粒度——0.2.14 N1）。

    周期类参数（bar 数语义）的**实际取值**之和；params 缺键补注册表
    default。与 ``_indicator_lookback``（注册表 max 保守取向）区分：
    高周期轴 warmup（high_tf_warmup_span）用本函数——机械沿用 max
    会把 RSI(14) 当 RSI(50) 预热（3d 轴多吃 108 bar）。

    Raises:
        KeyError: 引用不存在的指标。
    """
    defn = INDICATOR_REGISTRY.get(indicator)
    if defn is None:
        raise KeyError(f"未知指标: {indicator}")
    total = 0
    for key in _PERIOD_LIKE_PARAMS:
        if key in defn.params:
            total += int(params.get(key, defn.params[key].default))
    return total


def default_params_for(indicator: str) -> Dict[str, Any]:
    """注册表默认参数单组（add-query-indicator 自 case_inspection 私有
    ``_default_params_for`` 提升——0.2.8 预留的「任意参数现算属 0.2.9
    边界」兑现；两消费者 inspect_cases/query_indicator 共用单一事实源）。
    """
    defn = INDICATOR_REGISTRY[indicator]
    return {key: p.default for key, p in defn.params.items()}


def _scopes_for(source: str, strategy_visible: bool) -> Dict[str, bool]:
    """域标注（improve-mcp-agent-loop M5）：列在三个引用域的可用性。

    - hypothesis：假设条件 subject（指标输出列形制如 rsi_14 / 插件列均可）
    - analysis：分析模板的**列引用**（correlation_scan/distribution_compare
      /event_study——模板 df 为原始 OHLCV+衍生品列；内置指标分析不走列
      引用而走 condition_stats 的 indicator 参数）
    - strategy：策略信号源（SignalGene indicator / 插件 strategy_visible）
    """
    if source == "builtin":
        return {"hypothesis": True, "analysis": False, "strategy": True}
    return {"hypothesis": True, "analysis": False, "strategy": strategy_visible}


def available_signal_columns(db_path: Path) -> List[Dict[str, Any]]:
    """可用信号列：内置指标输出 + 插件注册列，运行时合并（双源不漂移）。

    内置条目 source=builtin（指标级）；插件条目 source=formula|model（列级）。
    插件域查询走 repository（signal_registry 仅插件行——内置不落库）。
    每条目带 scopes 域标注（hypothesis/analysis/strategy 三域可用性——
    消除"一个清单看似全域通用"的暗坑）。
    """
    from core.persistence.repository.research import list_signal_registry_entries

    columns: List[Dict[str, Any]] = []
    for meta in indicator_metadata():
        columns.append({
            "column_key": meta["name"],
            "output_fields": meta["output_fields"],
            "source": "builtin",
            "category": meta["category"],
            "lookback": meta["lookback"],
            "strategy_visible": True,
            "version": None,
            "scopes": _scopes_for("builtin", True),
            # 0.2.13 引用形制直出：结构化 {indicator, params, field} 的构造
            # 依据（参数空间 + 输出列清单）；多输出指标 field 必填
            "reference_form": {
                "kind": "structured",
                "params_space": meta["params"],
                "note": "引用形制 {indicator, params, field}；"
                        "多输出指标 field 必填（output_fields 选一）"},
        })
    for entry in list_signal_registry_entries(db_path, only_visible=True):
        columns.append({
            "column_key": entry["column_name"],
            "output_fields": [entry["column_name"]],
            "source": entry["source"],
            "category": "plugin",
            "lookback": None,  # 插件 lookback 由表达式/模板自证（求值时声明）
            "strategy_visible": bool(entry["strategy_visible"]),
            "version": entry["version"],
            "scopes": _scopes_for(entry["source"], bool(entry["strategy_visible"])),
            "reference_form": {
                "kind": "string",
                "note": "插件列以注册列名（字符串精确名）引用"},
        })
    return columns
