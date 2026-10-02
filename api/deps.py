"""路由依赖注入（FastAPI Depends 入口的单一来源）。

职能：把 app.state 上的 db_path/data_dir 经 Depends 提供给全部
/api/* 路由——路径在 app.py lifespan 装配时挂 state，路由零配置取用。
服务对象：全部路由模块（get_db_path/get_data_dir 是最广消费的两个依赖）。
职责边界：只做状态读取，不含业务逻辑。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request


def get_db_path(request: Request) -> Path:
    """从 app.state 取 SQLite 库路径（lifespan 装配时挂载）。"""
    return request.app.state.db_path


def get_data_dir(request: Request) -> Path:
    """从 app.state 取 Parquet 数据目录（lifespan 装配时挂载）。"""
    return request.app.state.data_dir
