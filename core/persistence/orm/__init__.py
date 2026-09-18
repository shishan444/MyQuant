"""ORM 层（B3a 引入）：模型全量声明 + engine/session 工厂。

repository 迁移（B3b-d）逐域接入；本包不改变既有 `_connect` 旧轨行为
（绞杀者策略：行为等价、调用方零改动）。
"""
from core.persistence.orm.engine import BUSY_TIMEOUT_MS, create_db_engine, create_session_factory
from core.persistence.orm.models import Base

__all__ = ["Base", "BUSY_TIMEOUT_MS", "create_db_engine", "create_session_factory"]
