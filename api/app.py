"""FastAPI 应用装配点：生命周期、CORS、路由挂载与 socket.io 桥。

职能：create_app 是全工程唯一的应用工厂——初始化 DB schema、装配
evolution runner、research executor 与 socket.io 实时通道，
挂载全部 REST 路由；模块尾把 FastAPI 与 socket.io 组合为单一 ASGI
应用供 uvicorn 启动。
服务对象：全部 /api/* 路由（经 app.state 注入依赖）与 web 前端
（REST + /socket.io 实时推送）。
职责边界：只做装配与生命周期编排，业务逻辑在各路由模块与 core 层。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import RestAuthMiddleware, get_cors_origins, warn_if_no_api_key

logger = logging.getLogger("app")

from core.persistence.db_ext import init_db_ext
from .routes import config, data, evolution, strategies
from .routes import validate as validate_route
from .routes import scene as scene_route
from .routes import live_trading as live_trading_route
from .routes import paper_trading as paper_trading_route
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
    """构造并配置 FastAPI 应用（全工程唯一应用工厂）。

    Args:
        db_path: SQLite 库文件路径；缺省为项目根 data/quant.db。
        data_dir: Parquet 数据目录；缺省为项目根 data/market。
    """
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent
        db_path = project_root / "data" / "quant.db"
    if data_dir is None:
        project_root = Path(__file__).resolve().parent.parent
        data_dir = project_root / "data" / "market"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动段：初始化目录与 DB schema
        db_path.parent.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        init_db_ext(db_path)
        warn_if_no_api_key()

        # 进化域恢复：上次运行的中断/崩溃任务先回收再起 runner
        from .runner import EvolutionRunner, set_ws_push_fn, recover_stale_tasks, restart_crashed_tasks

        recover_stale_tasks(db_path)
        restart_crashed_tasks(db_path)

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

        # 路径挂 app.state：全部路由经 Depends(get_db_path/get_data_dir) 取用
        app.state.db_path = db_path
        app.state.data_dir = data_dir

        runner = EvolutionRunner(db_path=db_path, data_dir=data_dir)
        app.state.evolution_runner = runner

        # B8.2/8.4: socket.io 唯一实时通道（native WS 已退役）
        import os as _os
        from . import realtime
        realtime.capture_loop()
        _os.environ["MYQUANT_DB_PATH"] = str(db_path)

        def _make_push(namespace: str):
            """构造指定 namespace 的任务进度推送函数（room 按 task_id 定向）。

            B8.2/8.4：socket.io 单发实现（native WS 已退役）——runner 侧
            回调 → realtime.broadcast 定向 emit。
            """
            def _push(task_id: str, payload: dict) -> None:
                realtime.broadcast(namespace, room=task_id, event=payload.get("type", "message"), payload=payload)
            return _push
        _ws_push_evolution = _make_push("/evolution")

        set_ws_push_fn(_ws_push_evolution)

        # 纸盘交易新域调度器（rebuild-paper-trading 组 3）：启动期恢复
        # 扫描（running 残态：可信续跑/不可信转 error）→ 守护线程拾取。
        # 摆位纪律：本段须在 runner.start() 之前——段的延迟 import 链与
        # 恢复扫描开销若发生在已启动的 EvolutionRunner 之后，会经 GIL
        # 推迟 runner 首扫相位、落进测试的落行-请求窗口（缺陷④跨域
        # flaky 实测锁定）；放 runner 启动前则全部开销先于任何守护
        # 线程完成，且 capture_loop 已跑（push 可用）。
        from core.paper_trading.runtime.scheduler import PaperScheduler

        paper_scheduler = PaperScheduler(
            db_path=db_path, data_dir=data_dir, push=_make_push("/trading"))
        paper_scheduler.startup_recover()
        app.state.paper_scheduler = paper_scheduler
        paper_scheduler.start()

        runner.start()

        yield

        # 关停序：先断推送（避免 runner 停止后回调打到空目标），再停
        # evolution runner 与纸盘调度器
        set_ws_push_fn(None)
        runner.stop()
        runner.join(timeout=5.0)
        paper_scheduler.stop()
        paper_scheduler.join(timeout=5.0)

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

    # A2 准入中间件：Host 白名单（防 DNS rebinding）+ API key（可选启用）。
    # 注册顺序刻意先于 CORS——add_middleware 为 insert(0)，后注册者更外层，
    # CORS 必须在最外层短路 preflight（OPTIONS 无 Authorization，若先过
    # 认证层会 401 且 CORSMiddleware 收不到请求，浏览器判 preflight 失败）
    app.add_middleware(RestAuthMiddleware)

    # CORS 白名单：已知前端 origin（A2 收紧；MYQUANT_CORS_ORIGINS 可覆盖）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载全部业务路由（研究域路由因包内延迟装配置于其后）
    app.include_router(config.router)
    app.include_router(strategies.router)
    app.include_router(evolution.router)
    app.include_router(data.router)
    app.include_router(live_trading_route.router)
    # 纸盘交易新域端点族（rebuild-paper-trading 5.1）
    app.include_router(paper_trading_route.router)
    app.include_router(validate_route.router)
    app.include_router(scene_route.router)

    # 研究域路由（add-ai-research-mcp）
    from api.routes import research as research_route

    app.include_router(research_route.router)

    # 健康检查端点（version 来自包元数据，timestamp 为 UTC isoformat）
    @app.get("/api/health", response_model=HealthResponse)
    def health_check() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=APP_VERSION,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    return app


# uvicorn 缺省 app 实例：FastAPI + socket.io ASGI 组合
# （socket.io 端点在 /socket.io 路径；namespace 经连接参数路由）
_fastapi_app = create_app()

import socketio as _socketio_mod  # noqa: E402
from .realtime import sio as _realtime_sio  # noqa: E402

app = _socketio_mod.ASGIApp(_realtime_sio, other_asgi_app=_fastapi_app, socketio_path="socket.io")
