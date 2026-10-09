"""db_ext：初始化入口 + CRUD 兼容 re-export 过渡一版（TD-10 终态）。

init_db_ext（保名换芯：alembic stamp/upgrade——版本演进唯一权威
为 Alembic 版本链，legacy ALTER 轨已退役删除）与 _connect（测试库
直连工具）为本模块实体职责。生产调用方已全部直连 repository.*；
下方 CRUD re-export 仅供 tests/ 过渡（tests 域逐步切换后整块删除）。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _connect(db_path: Path) -> sqlite3.Connection:
    """测试库直连工具（WAL/busy_timeout 统一 PRAGMA；业务代码走 orm.engine 工厂）。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# legacy 轨终态映射的 alembic 版本号（TD-10 换芯）：init_db_ext 旧轨
# （ORM create_all+PRAGMA guard ALTER）产物 schema 恰等该版本终态
# （stamp 前三库 diff 实证）；其后的版本（segmentation 五元/equity
# 唯一/排序索引补齐）由 upgrade 链正常推进
_LEGACY_TERMINAL_REVISION = "b7c4e9a2f1d3"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config_for(db_path: Path):
    """绑定库路径的 Alembic Config（attributes 通道线程安全）。"""
    from alembic.config import Config

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.attributes["db_path"] = str(db_path)
    return cfg


def init_db_ext(db_path: Path) -> None:
    """初始化/升级库 schema（保名换芯：签名与调用面零改动）。

    新库（无业务表）：alembic upgrade head 全链建库。
    存量库无 alembic_version（legacy 轨产物）：先 stamp 至
    legacy 终态版本（schema 恰等，三库 diff 实证）再 upgrade head。
    已在 alembic 轨：upgrade head 幂等（版本表短路）。
    版本记账由 schema_version 换为 alembic_version（legacy 记账表
    在存量库保留为历史数据，无消费者）。
    """
    import sqlite3 as _sqlite3

    from alembic import command

    db_path = Path(db_path)
    cfg = _alembic_config_for(db_path)

    stamped = False
    if db_path.exists():
        conn = _sqlite3.connect(str(db_path))
        try:
            has_business_tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchone()[0] > 0
            on_alembic = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE name='alembic_version'").fetchone()[0] > 0
        finally:
            conn.close()
        if has_business_tables and not on_alembic:
            # stamp 目标探测：ORM create_all 形库（init_db 产物）为 head
            # 全列形，直接 stamp head（upgrade 短路）；legacy 轨产物为
            # b7c4e9a2f1d3 终态形，stamp 后由 upgrade 补链。不探测则对
            # 全列库 ADD COLUMN 撞 duplicate column（混用形态防御——
            # tests 的 init_db fixture 即此路径）
            conn2 = _sqlite3.connect(str(db_path))
            try:
                is_head_shape = conn2.execute(
                    "SELECT COUNT(*) FROM pragma_table_info('validation_records') "
                    "WHERE name='segmentation'").fetchone()[0] > 0
            finally:
                conn2.close()
            target = "head" if is_head_shape else _LEGACY_TERMINAL_REVISION
            command.stamp(cfg, target)
            stamped = True
            logger.info("存量库 stamp 至 %s（版本演进权归 Alembic）", target)

    command.upgrade(cfg, "head")
    if stamped:
        logger.info("存量库已升级至 alembic head（保数据零重建）")


# ===================================================================
# CRUD re-export 过渡一版（TD-10 步骤 3）：生产调用方已直连
# repository.*（13 文件切换完成）；本块仅供 tests/ 存量 import
# 兜底——tests 域切换完成后整块删除
# ===================================================================

from core.persistence.repository.backtest_result import (  # noqa: F401
    get_backtest_result, list_backtest_results, save_backtest_result,
)
from core.persistence.repository.dataset_meta import (  # noqa: F401
    delete_dataset, get_dataset, list_datasets, save_dataset_meta,
    update_dataset_stats,
)
from core.persistence.repository.paper_trading import (  # noqa: F401
    compute_trading_metrics, count_paper_trades, count_paper_trading_tasks,
    delete_paper_trading_task, get_paper_trading_task,
    list_equity_snapshots, list_paper_trades, list_paper_trading_tasks,
    save_equity_snapshots, save_paper_trade, save_paper_trading_task,
    update_paper_trading_task,
)
from core.persistence.repository.strategy import (  # noqa: F401
    count_strategies, count_strategies_by_tasks, delete_strategy,
    find_by_gene_signature, get_strategy, list_strategies, save_strategy,
    update_strategy,
)
from core.persistence.repository.verify_session import (  # noqa: F401
    get_verify_session, list_verify_sessions, save_verify_session,
    update_verify_session,
)
