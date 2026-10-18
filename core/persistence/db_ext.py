"""db_ext：库初始化入口（TD-10 终态）。

init_db_ext（保名换芯：alembic stamp/upgrade——版本演进唯一权威
为 Alembic 版本链，legacy ALTER 轨已退役删除）与 _connect（测试库
直连工具）为本模块全部实体职责。CRUD 访问一律直连
core.persistence.repository.*（生产与 tests 均已切换完成）。
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

# create_all 形库（init_db 产物）的 stamp 目标：lifecycle 迁移前一版
# （CARD-01）。ORM create_all 按 models 建表（含事件表）但建不出 DB
# 触发器、也不执行存量分类（两者仅存在于迁移链）——stamp 至前一版
# 由 upgrade 链补齐，不再 stamp head 短路（否则触发器永久缺失且
# 版本记账谎报 head 终态）
_PRE_LIFECYCLE_REVISION = "d8a2f6c3e9b1"

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
    legacy 终态版本（schema 恰等，三库 diff 实证）再 upgrade head；
    其中 create_all 形库（init_db 产物——全列形但 DB 触发器必缺）
    stamp 至 lifecycle 前一版，由 upgrade 补触发器与存量分类
    （事件表由迁移内在场守卫防撞）。
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
            # stamp 目标探测三分支（CARD-01）：全列形+双域触发器在场→
            # head 真终态，直接 stamp head（upgrade 短路）；全列形但
            # 触发器缺失→create_all 形库（init_db 产物建不出 DB 触发器
            # 也不执行存量分类），stamp 至 lifecycle 前一版由 upgrade
            # 补齐；非全列→legacy 轨产物为 b7c4e9a2f1d3 终态形，stamp
            # 后由 upgrade 补链。不做列形探测则对全列库 ADD COLUMN 撞
            # duplicate column（混用形态防御——tests 的 init_db fixture
            # 即此路径）；不做触发器判据则 create_all 形库 stamp head
            # 短路后触发器永久缺失且版本谎报
            conn2 = _sqlite3.connect(str(db_path))
            try:
                is_head_columns = conn2.execute(
                    "SELECT COUNT(*) FROM pragma_table_info('validation_records') "
                    "WHERE name='segmentation'").fetchone()[0] > 0
                has_domain_triggers = conn2.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                    "AND name IN ('trg_strategy_status_domain', "
                    "'trg_strategy_status_insert_domain')").fetchone()[0] == 2
            finally:
                conn2.close()
            if is_head_columns and has_domain_triggers:
                target = "head"
            elif is_head_columns:
                target = _PRE_LIFECYCLE_REVISION
            else:
                target = _LEGACY_TERMINAL_REVISION
            command.stamp(cfg, target)
            stamped = True
            logger.info("存量库 stamp 至 %s（版本演进权归 Alembic）", target)

    command.upgrade(cfg, "head")
    if stamped:
        logger.info("存量库已升级至 alembic head（保数据零重建）")
