"""quant.db VACUUM 运维脚本（cleanup-data-segment 契约 3 / R-12）。

删除策略持续产生空闲页——本脚本为常驻运维件（幂等可重跑）。前置
自检 BEGIN IMMEDIATE 探测库未被占用（占用即退出码 2 提示停服——
防误对运行库执行；探测与执行之间的竞态窗口由执行侧同分流兜底）；
步骤 PRAGMA wal_checkpoint(TRUNCATE) → VACUUM；输出前后体积与
page_count/freelist_count 对比。

用法：python scripts/vacuum_quant_db.py [--db PATH]（缺省 data/quant.db）。
退出码：0=成功｜2=库占用（提示停服）｜1=其他错误。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _is_locked(exc: sqlite3.Error) -> bool:
    """库被占用（拿写锁失败）的异常判定。

    目录打不开（unable to open database file）同为 OperationalError
    但语义是打不开而非占用，不能按异常类型分流，须按文本特征
    （database is locked / database table is locked）判定。
    """
    return (
        isinstance(exc, sqlite3.OperationalError)
        and "locked" in str(exc).lower()
    )


def _probe_unoccupied(db_path: Path) -> bool:
    """BEGIN IMMEDIATE 探测：拿不到写锁即视为占用（运行库）。

    目录/非库文件等无法作为数据库打开的错误向上传播，由调用方
    按错误分流（exit 1 结构化输出）。
    """
    conn = sqlite3.connect(db_path, timeout=1.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError as exc:
        if _is_locked(exc):
            return False
        raise
    finally:
        conn.close()


def _pragma(conn: sqlite3.Connection, key: str) -> int:
    return int(conn.execute(f"PRAGMA {key}").fetchone()[0])


def _du(path: Path) -> int:
    return path.stat().st_size


def vacuum(db_path: Path) -> int:
    if not db_path.exists():
        print(f"[错误] 库不存在：{db_path}")
        return 1
    try:
        unoccupied = _probe_unoccupied(db_path)
    except sqlite3.Error as exc:
        print(f"[错误] 无法打开库：{exc}")
        return 1
    if not unoccupied:
        print("[占用] BEGIN IMMEDIATE 探测失败——库正被使用。请停服后"
              "在低峰窗口执行（docs/运维/quant-db-vacuum.md 规程）。")
        return 2

    wal = db_path.with_name(db_path.name + "-wal")
    size_before = _du(db_path)
    wal_before = _du(wal) if wal.exists() else 0
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        print(f"[错误] 无法打开库：{exc}")
        return 1
    try:
        pages_before = _pragma(conn, "page_count")
        free_before = _pragma(conn, "freelist_count")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print("[checkpoint] wal_checkpoint(TRUNCATE) 完成")
        conn.execute("VACUUM")
        pages_after = _pragma(conn, "page_count")
        free_after = _pragma(conn, "freelist_count")
    except sqlite3.Error as exc:
        if _is_locked(exc):
            # 竞态窗口兜底：探测仅是前置提示，锁在探测后才到时
            # 由执行侧同分流闭环（同样退出码 2 提示停服）。
            print("[占用] 探测后库被并发占用（竞态窗口）：请停服后"
                  "在低峰窗口重试（docs/运维/quant-db-vacuum.md 规程）。")
            return 2
        print(f"[错误] VACUUM 执行失败：{exc}")
        return 1
    finally:
        conn.close()

    size_after = _du(db_path)
    wal_after = _du(wal) if wal.exists() else 0
    print(
        f"[结果] 主库 {_mb(size_before)} → {_mb(size_after)}"
        f"（page_count {pages_before}→{pages_after}，"
        f"freelist_count {free_before}→{free_after}）；"
        f"wal {_mb(wal_before)} → {_mb(wal_after)}")
    print("[成功] VACUUM 完成（幂等可重跑）")
    return 0


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}MB"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db", type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "quant.db")
    args = ap.parse_args()
    sys.exit(vacuum(args.db))


if __name__ == "__main__":
    main()
