"""条件身份规范化指纹（add-screen-patterns D4）。

单一权威：扫描侧（候选指纹生成）与验证侧（假设条件指纹集）共用
本函数做「精确复用」比对；指纹是派生值、现算不落库（规范函数是
唯一事实源，不建第二存储）。

归一规则（0.2.13 演进：subject 混合形制——结构化指标对象经
column_ref.canonical_ref 规范化；字符串 strip 后原样与历史指纹逐字节
连续）：
- subject：结构化 → canonical_ref 编码（i:{indicator}|p:{排序数值归一}|f:{field}）；
  字符串 → strip 后原样（基础列/插件列名）；
- action：注册表枚举原文原样（近义词不合并——语义不同即不同身份）；
- target：数值归一（int/float 同值同指纹，25 与 25.0 同指纹；整数
  值去掉浮点尾巴），字符串（列名）strip 原样；
- timeframe：参与指纹（同名列跨周期不算精确复用——数据不同）。

分隔符 \\x1f（单元分隔符）防字段拼接歧义；sha256 取前 16 hex。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict

_SEP = "\x1f"


def _normalize_target(target: Any) -> str:
    """target 归一：数值同值同形，列名原样。

    可解析为数值的字符串按数值归一（与验证引擎 _resolve_target 的
    解析语义对齐——"25" 与 25 在引擎里同义，指纹亦同义）。
    """
    if isinstance(target, bool):
        # bool 是 int 子类但语义是比较标志，不与数值混同
        return "b:1" if target else "b:0"
    if isinstance(target, (int, float)):
        f = float(target)
        if f.is_integer():
            return f"n:{int(f)}"
        return f"n:{f!r}"
    s = str(target).strip()
    try:
        f = float(s)
        if f.is_integer():
            return f"n:{int(f)}"
        return f"n:{f!r}"
    except ValueError:
        return f"s:{s}"


def canonical_condition_hash(
    condition: Dict[str, Any], timeframe: str
) -> str:
    """条件规范化指纹（稳定、跨入口一致）。

    Args:
        condition: {subject, action, target} 单条件（假设 when/then
            条目与扫描候选同形制）。
        timeframe: bar 周期键（如 "1h"）——参与指纹。

    Returns:
        16 hex 摘要；同语义条件（仅数值类型差异）同指纹。

    Raises:
        ValueError: 条件缺 subject/action/target 任一键。
    """
    for key in ("subject", "action", "target"):
        if key not in condition:
            raise ValueError(
                f"条件缺 {key} 键: {condition!r}——指纹要求三键齐备"
            )
    from core.analysis.column_ref import canonical_ref
    parts = [
        canonical_ref(condition["subject"]),
        str(condition["action"]).strip(),
        _normalize_target(condition["target"]),
        str(timeframe).strip(),
    ]
    digest = hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def legacy_compatible_hash(condition: Dict[str, Any], timeframe: str) -> str:
    """旧算法指纹完整复刻（0.2.13 F1——仅供反向核查影子消费）。

    结构化 subject 经 encode_column_ref 还原编码列名后按旧归一
    （str().strip()）参与——与迁移前 screen 留痕指纹逐字节对齐；
    字符串 subject 与旧算法完全相同（两算法在字符串形上天然一致）。

    迁移是一次性原子切（无混合态）：旧留痕 × 迁移后假设靠本函数影子
    命中，新留痕与新假设走 canonical_condition_hash——单向补影即完备。
    """
    from core.analysis.column_ref import decode_column_ref, encode_column_ref
    subject = condition["subject"]
    if isinstance(subject, dict):
        subject = encode_column_ref(
            subject.get("indicator", ""), subject.get("params") or {},
            subject.get("field") or "")
    parts = [
        str(subject).strip(),
        str(condition["action"]).strip(),
        _normalize_target(condition["target"]),
        str(timeframe).strip(),
    ]
    digest = hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
