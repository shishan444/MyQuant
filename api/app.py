"""FastAPI application entry point with CORS, lifecycle, and route mounting."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("app")

from core.persistence.db_ext import init_db_ext
from .routes import config, data, evolution, strategies
from .routes import validate as validate_route
from .routes import scene as scene_route
from .routes import discovery as discovery_route
from .routes import trading as trading_route
from .routes import live_trading as live_trading_route
from .schemas import HealthResponse

try:
    from importlib.metadata import version as _pkg_version
    APP_VERSION = _pkg_version("MyQuant")
except Exception:  # 包未安装（非 editable 运行）时回退常量，与 pyproject 同步维护
    APP_VERSION = "0.16.0"


def create_app(
    db_path: Path | None = None,
    data_dir: Path | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Path to the SQLite database file.
                 Defaults to ``data/quant.db`` relative to project root.
        data_dir: Path to the data directory for Parquet files.
                  Defaults to ``data/market`` relative to project root.
    """
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent
        db_path = project_root / "data" / "quant.db"
    if data_dir is None:
        project_root = Path(__file__).resolve().parent.parent
        data_dir = project_root / "data" / "market"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup: initialize database
        db_path.parent.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        init_db_ext(db_path)

        # Start EvolutionRunner background thread
        from .runner import EvolutionRunner, set_ws_push_fn, recover_stale_tasks, restart_crashed_tasks

        recover_stale_tasks(db_path)
        restart_crashed_tasks(db_path)

        # Start TradingRunner background thread
        from core.trading.runner import TradingRunner, set_trading_ws_push_fn, recover_stale_trading_tasks

        recover_stale_trading_tasks(db_path)

        # Research executor（add-ai-research-mcp 1.4）：研究域表自愈 →
        # 中断任务回收 → 线程池执行器单例（SIGTERM 时随进程释放）
        from core.analysis.executor import (
            ensure_research_tables,
            init_executor,
            recover_stale_analysis_tasks,
        )

        ensure_research_tables(db_path)
        recover_stale_analysis_tasks(db_path)
        app.state.research_executor = init_executor(db_path)

        # Store paths on app state for dependency injection
        app.state.db_path = db_path
        app.state.data_dir = data_dir

        runner = EvolutionRunner(db_path=db_path, data_dir=data_dir)
        app.state.evolution_runner = runner

        trading_runner = TradingRunner(db_path=db_path, data_dir=data_dir)
        app.state.trading_runner = trading_runner

        # Wire WS push: runner -> manager.push (async) via asyncio.run_coroutine_threadsafe
        event_loop = asyncio.get_event_loop()

        # B8.2/8.4: socket.io 唯一实时通道（native WS 已退役）
        import os as _os
        from . import realtime
        realtime.capture_loop()
        _os.environ["MYQUANT_DB_PATH"] = str(db_path)

        def _make_push(namespace: str):
            """B8.2: native WS（manager.push，room 按 task_id）+ socket.io 定向
            namespace emit 双发——切换验证后 native 侧退役（11.4）。"""
            def _push(task_id: str, payload: dict) -> None:
                realtime.broadcast(namespace, room=task_id, event=payload.get("type", "message"), payload=payload)
            return _push
        _ws_push_evolution = _make_push("/evolution")
        _ws_push_trading = _make_push("/trading")

        def _on_ws_push_done(future: asyncio.Future) -> None:
            """Log any exception from the WS push coroutine."""
            try:
                future.result()
            except Exception:
                logger.warning("WS push coroutine failed", exc_info=True)

        set_ws_push_fn(_ws_push_evolution)
        set_trading_ws_push_fn(_ws_push_trading)
        runner.start()
        trading_runner.start()

        yield

        # Shutdown: disconnect WS push first, then stop runners
        set_ws_push_fn(None)
        set_trading_ws_push_fn(None)
        runner.stop()
        trading_runner.stop()
        runner.join(timeout=5.0)
        trading_runner.join(timeout=5.0)

        # 研究执行器停止（守护扫描停、不等待在途计算）；重置单例供测试复用
        from core.analysis import executor as _research_exec
        if _research_exec._EXECUTOR is not None:
            _research_exec._EXECUTOR.shutdown()
            with _research_exec._EXECUTOR_LOCK:
                _research_exec._EXECUTOR = None

    app = FastAPI(
        title="MyQuant API",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # CORS: allow all origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(config.router)
    app.include_router(strategies.router)
    app.include_router(evolution.router)
    app.include_router(data.router)
    app.include_router(trading_route.router)
    app.include_router(live_trading_route.router)
    app.include_router(validate_route.router)
    app.include_router(scene_route.router)
    app.include_router(discovery_route.router)

    # 研究域路由（add-ai-research-mcp）
    from api.routes import research as research_route

    app.include_router(research_route.router)

    # Health check endpoint
    @app.get("/api/health", response_model=HealthResponse)
    def health_check() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=APP_VERSION,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    return app


# Default app instance for uvicorn：FastAPI + socket.io ASGI 组合
# （socket.io 端点在 /socket.io 路径；namespace 经连接参数路由）
_fastapi_app = create_app()

import socketio as _socketio_mod  # noqa: E402
from .realtime import sio as _realtime_sio  # noqa: E402

app = _socketio_mod.ASGIApp(_realtime_sio, other_asgi_app=_fastapi_app, socketio_path="socket.io")
