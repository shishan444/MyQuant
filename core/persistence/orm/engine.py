"""SQLAlchemy engine/session 工厂（B3a.1）。

统一连接配置：WAL + busy_timeout（消灭 `_connect` 双源的连接语义分叉——
B3d 旧轨退役后本工厂是唯一连接入口）。SQLite 同步模式；单写者下不引入
异步（容器裁决）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# 与旧轨 _connect 的 busy_timeout 对齐（毫秒）
BUSY_TIMEOUT_MS = 5000


def create_db_engine(db_path: Path) -> Engine:
    """创建绑定单库文件的 engine，统一注入 SQLite PRAGMA。

    - journal_mode=WAL：库级持久属性，读写不互斥
    - busy_timeout：写锁竞争时等待而非立即失败
    - foreign_keys=ON：与 ORM ForeignKey 声明配套（旧轨未开，模型已如实
      声明存量 FK，开启后对既有数据无影响——插入侧由应用保证）
    - check_same_thread=False：连接池线程复用必需（runner 后台线程与
      API 线程各自拿连接，同一连接可能先后被不同线程使用）
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建 session 工厂。

    expire_on_commit=False：函数级事务（repository 提交后对象继续可读，
    契约见 design.md 事务语义裁决）；autoflush=False：查询不隐式刷写，
    写入显式 flush/commit——与 per-call commit 的行为等价迁移纪律一致。
    """
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
