#!/usr/bin/env python
"""Alembic baseline 接管存量库（B3a.2，一次性运维动作）。

接管顺序（权威顺序，勿颠倒）：
  1. legacy 轨最后一次全量执行（init_db_ext + live 表）——把存量库补齐到
     终态 schema（含 B1.3 paper_trade 去重+唯一索引等尚未跑过的增量）
  2. alembic stamp head——声明该库 schema 与 baseline 等价，纳入版本链

此后 schema 演进只经 Alembic revision（SQL 轨/Python ALTER 轨冻结，
B3d 退役旧轨）。

用法：
  python scripts/adopt_alembic_baseline.py [db_path]   # 默认 data/quant.db
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def adopt_legacy_db(db_path: Path) -> None:
    """存量库接管 Alembic 版本链（幂等：已 stamp 的库重复执行无副作用）。"""
    from alembic import command
    from alembic.config import Config

    # 1) legacy 轨补齐（幂等，含 live 表——B3c.1 收编进 init 链）
    from core.persistence.db_ext import init_db_ext
    init_db_ext(db_path)

    # 2) 纳入版本链（env.py 经 MYQUANT_DB_PATH 解析库位置）
    import os
    os.environ["MYQUANT_DB_PATH"] = str(db_path)
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.stamp(cfg, "head")


def main() -> int:
    db_arg = sys.argv[1] if len(sys.argv) > 1 else "data/quant.db"
    db_path = (PROJECT_ROOT / db_arg).resolve() if not Path(db_arg).is_absolute() else Path(db_arg)
    if not db_path.exists():
        print(f"库不存在: {db_path}")
        return 1
    adopt_legacy_db(db_path)
    print(f"已接管: {db_path} -> head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
