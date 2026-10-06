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
})


def _indicator_lookback(defn: Any) -> int:
    """指标 lookback：周期类参数的注册表上限之和（保守取向，宁可多预热）。"""
    total = 0
    for key, param in defn.params.items():
        if key in _PERIOD_LIKE_PARAMS:
            total += int(param.max)
    return total


def indicator_metadata() -> List[Dict[str, Any]]:
    """内置 47 指标元数据只读外化（含 lookback 与参数空间）。"""
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
) -> int:
    """两维 warmup 推导式：max(内置指标推导, 插件公式 lookback, 200)。

    预检（add-hypothesis-precheck）与隔离回测 ``_derive_dna_warmup``
    的同源公共入口——两处共用同段代码即「预检 warmup 数值与隔离验证
    warmup 声明同源同值」的字面保证（specs R3 MUST）。

    插件维度按依赖闭包递归取 max（fx_b 引用 fx_a → 两窗口都计入）；
    plugin_columns_lookback 对不存在/非公式/已失效列静默跳过贡献 0
    （求值端显式拒绝兜底）——调用方 MUST 自行校验插件列 registry
    可达性（不可达 422 结构化拒绝），禁止依赖本函数的静默跳过判定面。
    仅含内置引用时与 derive_warmup 完全一致（floor 200 自带）。

    Args:
        db_path: 插件注册表所在库（插件列 lookback 需要）。
        indicator_names: 内置指标名清单（INDICATOR_REGISTRY 键）。
        plugin_columns: 插件列名集合（fx_*/model_* 精确名；空/None
            时跳过插件维度）。

    Raises:
        KeyError: 内置指标未知（derive_warmup 透传）。
    """
    warmup = derive_warmup(indicator_names)  # 自带 floor 200
    if plugin_columns:
        # 延迟 import（环规避）：plugin_service 依赖链不进本模块加载期
        from core.analysis.plugin_service import plugin_columns_lookback
        warmup = max(warmup, plugin_columns_lookback(db_path, set(plugin_columns)))
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
    "策略链含公式插件时取 max(内置指标推导, plugin_formula_lookback)"
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
