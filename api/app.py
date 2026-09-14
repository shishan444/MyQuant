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
from .routes import config, data, evolution, strategies, ws
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
        from core.live_trading.persistence import init_live_trading_tables
        init_live_trading_tables(db_path)

        # Start EvolutionRunner background thread
        from .runner import EvolutionRunner, set_ws_push_fn, recover_stale_tasks, restart_crashed_tasks
        from .routes.ws import get_manager

        recover_stale_tasks(db_path)
        restart_crashed_tasks(db_path)

        # Start TradingRunner background thread
        from core.trading.runner import TradingRunner, set_trading_ws_push_fn, recover_stale_trading_tasks

        recover_stale_trading_tasks(db_path)

        # Store paths on app state for dependency injection
        app.state.db_path = db_path
        app.state.data_dir = data_dir

        runner = EvolutionRunner(db_path=db_path, data_dir=data_dir)
        app.state.evolution_runner = runner

        trading_runner = TradingRunner(db_path=db_path, data_dir=data_dir)
        app.state.trading_runner = trading_runner

        # Wire WS push: runner -> manager.push (async) via asyncio.run_coroutine_threadsafe
        event_loop = asyncio.get_event_loop()

        def _ws_push(task_id: str, payload: dict) -> None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    get_manager().push(task_id, payload), event_loop
                )
                future.add_done_callback(_on_ws_push_done)
            except Exception:
                logger.warning("WS push schedule failed for task %s", task_id, exc_info=True)

        def _on_ws_push_done(future: asyncio.Future) -> None:
            """Log any exception from the WS push coroutine."""
            try:
                future.result()
            except Exception:
                logger.warning("WS push coroutine failed", exc_info=True)

        set_ws_push_fn(_ws_push)
        set_trading_ws_push_fn(_ws_push)
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
    app.include_router(ws.router)
    app.include_router(trading_route.router)
    app.include_router(live_trading_route.router)
    app.include_router(validate_route.router)
    app.include_router(scene_route.router)
    app.include_router(discovery_route.router)

    # Health check endpoint
    @app.get("/api/health", response_model=HealthResponse)
    def health_check() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=APP_VERSION,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    return app


# Default app instance for uvicorn
app = create_app()
