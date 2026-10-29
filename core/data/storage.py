"""K 线 Parquet 存取单一底座。

职能：DataFrame 与 Parquet 文件的读/写/合并/最新时间戳探测，
数据目录中所有 {dataset_id}.parquet 的物理读写都经本模块。
服务对象：core/data 同包三模块（csv_importer/updater/mtf_loader）
+ core/analysis 与 core/validation 各模块直读 + api 路由
（data 预览、strategies 回测/对比）+ core/trading/runner 装载。
纯文件层无业务语义（命名校验/增量决策等在上游各模块）。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

# ── 按文件粒度的进程内写入互斥（add-data-freshness-guardian D-3）───
# 三写者（数据保鲜守护器/活跃 feed flush/启动建库线程）全在同进程，
# threading.Lock 进程内互斥即可——不引入文件锁/跨进程机制（无第二
# 进程写者，不为检查表加机制）。禁用 RLock：可重入锁会静默放行嵌套
# 持锁，掩盖组合写盘的锁粒度错误——嵌套调用面经私有内层拆解（见
# _save_parquet_unlocked）。锁表无淘汰：数据目录文件数有限集
# （几十个 parquet），Lock 对象轻量，淘汰机制收益为负。
_file_locks: dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


@contextmanager
def parquet_write_lock(path: Path):
    """按文件粒度的写入互斥上下文（键=绝对路径；覆盖读-改-写全序列）。

    三公开写入口（save_parquet/merge_parquet/merge_closed_rows）
    内部已持同一把锁；本上下文供需要组合多步写盘语义的调用方显式
    持锁复用。持有本锁时不得再调三公开入口（threading.Lock 不可
    重入——嵌套即死锁），组合写盘走私有内层。
    """
    key = str(Path(path).resolve())
    with _file_locks_guard:
        lock = _file_locks.setdefault(key, threading.Lock())
    with lock:
        yield


def _save_parquet_unlocked(df: pd.DataFrame, path: Path) -> None:
    """写盘内层（调用方已持文件锁——合并族内调防嵌套自锁）。

    原子写：先写同目录临时文件再 os.replace 替换（同目录保证同一
    文件系统——rename 原子性成立）。进程中途被杀时目标文件要么
    旧版完整要么新版完整，无半写截断态；残留 tmp 不匹配
    {dataset_id}.parquet 键形制（直构路径消费方扫不到），下次写
    同名覆盖不堆积。tmp 名含线程标识是锁外并行写的防御深度：
    各写者 tmp 独立、os.replace 各自原子，目标文件永不因 tmp
    互踩损坏。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{threading.get_ident()}")
    try:
        df.to_parquet(tmp_path, engine="pyarrow")
        os.replace(tmp_path, path)
    finally:
        # 异常路径清理 tmp 残留（best-effort；os.replace 成功后已不存在）
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    """写 DataFrame 到 Parquet 文件（覆盖写，父目录不存在则创建）。"""
    with parquet_write_lock(path):
        _save_parquet_unlocked(df, path)


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
    索引排序。文件不存在时等价于覆盖写（返回 new_df）。读-改-写
    全序列持文件锁（内层写盘防嵌套自锁）。
    """
    with parquet_write_lock(path):
        if not path.exists():
            _save_parquet_unlocked(new_df, path)
            return new_df

        existing = load_parquet(path)
        merged = pd.concat([existing, new_df])
        merged = merged[~merged.index.duplicated(keep="last")]
        merged = merged.sort_index()
        _save_parquet_unlocked(merged, path)
        return merged


def merge_closed_rows(path: Path, new_rows: pd.DataFrame) -> None:
    """闭合行合并落盘单点（既有行优先——已落行永不改写+去重不重行）。

    与 merge_parquet 的语义分野：merge_parquet keep="last"（updater
    增量覆盖语义——同时间戳新值覆盖存量）；本函数 keep="first"
    （时间戳已在文件时丢弃新行——崩溃恢复竞态下已落行不被内存
    副本改写）。SymbolFeed 落盘、启动建库（FixedKeyBackfill）与
    数据保鲜守护器共用本不变式单点（自 paper_trading.feed 迁驻，
    守护器依赖方向禁止 import 纸盘域）。读-改-写全序列持文件锁。
    """
    with parquet_write_lock(path):
        if not path.exists():
            _save_parquet_unlocked(new_rows, path)
            return
        existing = load_parquet(path)
        columns = [c for c in new_rows.columns if c in existing.columns]
        combined = pd.concat([existing[columns], new_rows[columns]])
        combined = combined[~combined.index.duplicated(keep="first")]
        _save_parquet_unlocked(combined.sort_index(), path)
