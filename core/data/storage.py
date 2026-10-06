"""K 线 Parquet 存取单一底座。

职能：DataFrame 与 Parquet 文件的读/写/合并/最新时间戳探测，
数据目录中所有 {dataset_id}.parquet 的物理读写都经本模块。
服务对象：core/data 同包三模块（csv_importer/updater/mtf_loader）
+ core/analysis 与 core/validation 各模块直读 + api 路由
（data 预览、strategies 回测/对比）+ core/trading/runner 装载。
纯文件层无业务语义（命名校验/增量决策等在上游各模块）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    """写 DataFrame 到 Parquet 文件（覆盖写，父目录不存在则创建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow")


def load_parquet(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """读 Parquet 文件为 DataFrame。

    columns（0.3.3 C-β）：列子集读取（None=全列）——重载判定等只读
    行数/单列的场景免装全列 IO；index 列（如 timestamp）随 pandas
    自动恢复，不进 columns 列表。
    """
    return pd.read_parquet(path, engine="pyarrow", columns=columns)


def get_latest_timestamp(path: Path) -> pd.Timestamp | None:
    """取 Parquet 文件的最新时间戳；文件不存在或为空返回 None（增量起点探测用）。"""
    if not path.exists():
        return None
    df = load_parquet(path)
    if df.empty:
        return None
    return df.index.max()


def merge_parquet(new_df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """把新数据合并进既有 Parquet 文件并返回合并结果。

    按索引去重（保留新值——同时间戳时 new_df 覆盖存量）、输出按
    索引排序。文件不存在时等价于覆盖写（返回 new_df）。
    """
    if not path.exists():
        save_parquet(new_df, path)
        return new_df

    existing = load_parquet(path)
    merged = pd.concat([existing, new_df])
    merged = merged[~merged.index.duplicated(keep="last")]
    merged = merged.sort_index()
    save_parquet(merged, path)
    return merged
