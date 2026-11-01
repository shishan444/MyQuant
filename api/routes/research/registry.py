"""指标元数据与信号列查询端点（add-ai-research-mcp 1.3 出口；词表端点 alignment-batch γ）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from api.deps import get_db_path
from core.analysis.registry_meta import available_signal_columns, indicator_metadata
from core.signals import formula

router = APIRouter()

# 词族映射（alignment-batch γ）：源码族归属只存在于 formula.py 的分隔
# 注释（非机器可读），此处建映射使其可读。口径=源码主口径（v2.1 修订
# 权威口径）：时序变换 12 / 时序结构 2 / 逐元素 10 / 数学词汇 2
# （kalman 封装族两输出位）。
_WORD_FAMILY: Dict[str, str] = {
    # 时序变换族（12）
    "shift": "时序变换", "diff": "时序变换",
    "rolling_mean": "时序变换", "rolling_std": "时序变换",
    "rolling_max": "时序变换", "rolling_min": "时序变换",
    "rolling_sum": "时序变换", "zscore": "时序变换",
    "ema": "时序变换", "corr": "时序变换", "abs": "时序变换",
    "rolling_quantile": "时序变换",
    # 时序结构族（2，布尔直出）
    "crossover": "时序结构", "crossunder": "时序结构",
    # 逐元素族（10：max/min 广播；比较/逻辑布尔；sqrt/log 逐元素）
    "max": "逐元素", "min": "逐元素",
    "gt": "逐元素", "lt": "逐元素", "eq": "逐元素",
    "logical_and": "逐元素", "logical_or": "逐元素", "logical_not": "逐元素",
    "sqrt": "逐元素", "log": "逐元素",
    # 数学词汇族（2：kalman 双状态递归的两输出位）
    "kalman": "数学词汇", "kalman_slope": "数学词汇",
}

# 启动期完整性断言（fail-loud）：扩词忘建族映射即 import 红——
# 「收录自动获得≠构造自动获得」教训的制度化（枚举扩容计数残留实录）。
assert set(_WORD_FAMILY) == set(formula.FORMULA_FUNCTIONS), (
    "词族映射与 FORMULA_FUNCTIONS 键集不一致（扩词须同步 _WORD_FAMILY）："
    f"{set(_WORD_FAMILY) ^ set(formula.FORMULA_FUNCTIONS)}"
)

_CALIBER_NOTE = (
    "源码主口径（v2.1 修订权威口径）四族：时序变换 12 / 时序结构 2 / "
    "逐元素 10 / 数学词汇 2（rolling_quantile 归时序变换、sqrt/log 归"
    "逐元素）。历史口径差异单列：源码分隔注释字面的「数学词汇族」曾含 "
    "rolling_quantile/sqrt/log/kalman/kalman_slope 五词——两口径差异即 "
    "前三词的族归属（词集相同，v2.1 时点共 26 词；现行总数以响应 "
    "total 字段现算为准）。"
)


@router.get("/indicators")
def indicators() -> List[Dict[str, Any]]:
    """内置指标元数据（48 项，含 lookback——warmup 推导依据）。"""
    return indicator_metadata()


@router.get("/signals")
def signals(db_path: Path = Depends(get_db_path)) -> List[Dict[str, Any]]:
    """可用信号列：内置 ∪ 插件，运行时合并（条目标注来源）。"""
    return available_signal_columns(db_path)


@router.get("/vocabulary")
def vocabulary() -> Dict[str, Any]:
    """研究词表（alignment-batch γ）：FORMULA_FUNCTIONS 键集现算
    （formula.py 权威源，属性访问——响应跟随注册表，非启动快照），
    四族分组+双口径披露（caliber_note）。"""
    words = sorted(formula.FORMULA_FUNCTIONS.keys())
    families: Dict[str, List[str]] = {}
    for w in words:
        families.setdefault(_WORD_FAMILY.get(w, "未映射"), []).append(w)
    counts = {k: len(v) for k, v in families.items()}
    return {
        "total": len(words),
        "families": families,
        "counts": counts,
        "caliber_note": _CALIBER_NOTE,
    }
