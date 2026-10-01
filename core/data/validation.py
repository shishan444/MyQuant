"""数据集命名契约校验（csv_importer 写入路径的路径穿越收口）。

dataset_id = f"{symbol}_{interval}" 直接拼入 Parquet 写入路径，
symbol/interval 是路径成分的唯一外部来源。契约：白名单拒绝式
校验（拒绝而非清洗——dataset_id 须如实反映调用方指定，静默改写
会造成命名与预期不符），另加 resolve 纵深防御兜底。

interval 两档口径（差异属契约，按消费侧语义选用，勿混用）：
- 导入侧（csv_importer）宽口径 [0-9]+[mhdw]：只约束形状，容纳
  Binance 官方归档命名的全部周期；
- 拉取/读取侧（updater/mtf_loader）窄口径 TF_SECONDS 键集：
  外部 API 请求与读取路径的周期必须已知且可映射秒数。
"""
from __future__ import annotations

import re
from pathlib import Path

from core.data.timeframes import TF_SECONDS

# symbol 白名单：1-20 位字母数字（显式字符类，规避 \d/\w 的 Unicode 匹配）
_SYMBOL_RE = re.compile(r"[A-Za-z0-9]{1,20}")
# 导入侧 interval 宽口径：数字 + m/h/d/w 单位
_IMPORT_INTERVAL_RE = re.compile(r"[0-9]+[mhdw]")


def validate_symbol(symbol: str) -> str:
    """校验交易对命名，非法即拒绝（拒绝而非清洗）。

    Args:
        symbol: 交易对（如 "BTCUSDT"）。

    Returns:
        原样返回合法 symbol，便于链式传递。

    Raises:
        ValueError: 含字母数字以外字符，或长度不在 1-20。
    """
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError(
            f"symbol 非法: {symbol!r}（须为 1-20 位字母数字）")
    return symbol


def validate_import_interval(interval: str) -> str:
    """校验导入侧 interval（宽口径），非法即拒绝。

    Args:
        interval: K 线周期（如 "4h"、"30m"、"1d"、"1w"）。

    Returns:
        原样返回合法 interval。

    Raises:
        ValueError: 不匹配 [0-9]+[mhdw] 形状。
    """
    if not _IMPORT_INTERVAL_RE.fullmatch(interval):
        raise ValueError(
            f"interval 非法: {interval!r}（须形如 4h/30m/1d/1w）")
    return interval


def validate_known_interval(interval: str) -> str:
    """校验拉取/读取侧 interval（TF_SECONDS 键集），非法即拒绝。

    Args:
        interval: K 线周期（如 "4h"、"1d"）。

    Returns:
        原样返回合法 interval。

    Raises:
        ValueError: 不在 TF_SECONDS 键集中（周期未知、无法映射秒数）。
    """
    if interval not in TF_SECONDS:
        raise ValueError(
            f"interval 未知: {interval!r}（可用: {sorted(TF_SECONDS)}）")
    return interval


def ensure_path_within(path: Path, root: Path) -> None:
    """纵深防御：路径解析后必须落在 root 目录内。

    上游白名单已拦截常规非法输入；本检查兜底其余路径逃逸
    可能（如未来新增的拼接来源），不依赖单一防线。

    Raises:
        ValueError: path.resolve() 越出 root.resolve()。
    """
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"写入路径越界: {path}（须位于 {root} 内）")
