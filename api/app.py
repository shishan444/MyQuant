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
import os
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


def _default_db_path(project_root: Path) -> Path:
    """db_path 缺省解析：MYQUANT_DB_PATH 置位则切库（e2e 隔离工具
    fixture 的临时库注入通路），未置位保持项目根 data/quant.db。
    svc.sh 常驻显式置位且钉死生产位（$APP_DIR/data/quant.db，解析
    路径与缺省逐位一致——shell 残留 env 不再静默劫持常驻）。显式
    传参优先于 env：参数非 None 不进缺省分支，api_client 显式传参
    形制不可被 env 污染。与 alembic/env.py 的同名 env 语义同源（该处
    仅作 attributes/-x 通道之后的第三级回退，两处取值一致）。
    """
    env_db = os.environ.get("MYQUANT_DB_PATH")
    if env_db:
        # 切库事实留痕（warning 级，与 FAPI_STREAM_DISABLED 旁路披露
        # 同形制：-m api 形态 root logger 无配置，info 会被默认
        # WARNING 门槛拦截而不可见）
        logger.warning("MYQUANT_DB_PATH 置位，业务库切换至 %s", env_db)
        return Path(env_db)
    return project_root / "data" / "quant.db"


def create_app(
    db_path: Path | None = None,
    data_dir: Path | None = None,
    fapi_stream_factory=None,
    guardian_factory=None,
) -> FastAPI:
    """构造并配置 FastAPI 应用（全工程唯一应用工厂）。

    Args:
        db_path: SQLite 库文件路径；缺省读 MYQUANT_DB_PATH 环境变量
            （e2e 隔离工具注入临时库的通路），未置位为项目根
            data/quant.db。
        data_dir: Parquet 数据目录；缺省为项目根 FAPI_DATA_DIR（环境
            变量缺省 data/market_fapi——全工程唯一活跃数据域；旧
            现货归档域已整体删除，其独有的 30m/3d 冻结快照四文件
            已迁入本域，无运行时写入者）。
        fapi_stream_factory: 行情流工厂（缺省生产 BinanceFapiStream；
            测试注入替身。FAPI_STREAM_DISABLED 环境变量置位时装配
            整体旁路——测试全域 conftest 旁路面）。
        guardian_factory: 数据保鲜守护器工厂（缺省生产
            DataFreshnessGuardian；测试注入替身。FAPI_STREAM_DISABLED
            置位时装配整体跳过且 factory 亦不调用——旁路优先）。
    """
    # 无条件求值一次：lifespan 内 config.yaml 读取同用（两参显式传参时
    # 分支内赋值会使 lifespan 引用未定义名——config 从未被打开）
    project_root = Path(__file__).resolve().parent.parent
    if db_path is None:
        db_path = _default_db_path(project_root)
    if data_dir is None:
        from core.data.fapi.rest import fapi_env

        raw_dir = fapi_env()["data_dir"]
        data_dir = raw_dir if raw_dir.is_absolute() else project_root / raw_dir

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

        # 受限并发（research-tooling-data-quality 卡 B）：config
        # evolution.max_concurrent_tasks 可配（缺省 2 上限 4——runner
        # 内钳制）；config 无段时用缺省
        import yaml as _yaml
        try:
            with open(project_root / "config.yaml") as _f:
                _cfg = _yaml.safe_load(_f) or {}
        except (OSError, _yaml.YAMLError) as _exc:
            # config 缺失/损坏：降级缺省并留痕（宽捕获会静默吞掉编程错误）
            _cfg = {}
            logger.warning(
                "config.yaml 读取失败（%s: %s），max_concurrent_tasks 用缺省 2",
                type(_exc).__name__, _exc,
            )
        # 文档层非映射（如整文档裸标量）：与 or {} 防 None 同族的类型面
        # 防御——否则 _cfg.get 处 AttributeError 启动崩溃
        if not isinstance(_cfg, dict):
            logger.warning(
                "config.yaml 文档层非映射（%s），max_concurrent_tasks 用缺省 2",
                type(_cfg).__name__,
            )
            _cfg = {}
        _maxw = (_cfg.get("evolution") or {}).get(
            "max_concurrent_tasks", 2)
        runner = EvolutionRunner(
            db_path=db_path, data_dir=data_dir, max_workers=_maxw)
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
        # MIN_NOTIONAL 启动装载（change paper-depletion-terminal 任务 1：
        # env>端点>内置三来源；先于调度器/执行器装配——kernel 拦截判定
        # 读装载表，失败回退内置任务行为不中断）。FAPI_STREAM_DISABLED
        # 置位（测试全域旁路面）时跳过端点拉取直接内置值——测试不进
        # 真实网络；装载行为本身由 test_min_notional 替身注入面全测
        import os as _os_mn

        from core.paper_trading.min_notional import load_min_notional

        _mn_fetch = None
        if _os_mn.environ.get("FAPI_STREAM_DISABLED"):
            _mn_fetch = lambda: {}  # noqa: E731 —— 旁路：端点源空→内置
        load_min_notional(fetch_exchange_info=_mn_fetch)
        from core.paper_trading.runtime.scheduler import PaperScheduler

        paper_scheduler = PaperScheduler(
            db_path=db_path, data_dir=data_dir, push=_make_push("/trading"))
        paper_scheduler.startup_recover()
        app.state.paper_scheduler = paper_scheduler
        paper_scheduler.start()

        # fapi 行情接入装配（change-1 任务 5）：WS 流（固定 12 路订阅
        # +事件按需路由到现存 feed 实例）+启动建库线程（12 键全量回补
        # /尾戳补齐）+数据保鲜守护器（add-data-freshness-guardian：
        # 应用级文件面保鲜——无纸盘任务时三族数据持续更新）。
        # FAPI_STREAM_DISABLED 置位时整体旁路（测试全域 conftest
        # 旁路面——生产无该变量恒装配）。
        fapi_stream = None
        fapi_backfill = None
        fapi_guardian = None
        import os as _os_fapi

        if _os_fapi.environ.get("FAPI_STREAM_DISABLED"):
            # 旁路面披露（批次三 CARD-05）：该变量仅测试全域 conftest
            # 置位（生产无此变量恒装配）——启动日志留痕防「生产误置/
            # 环境泄漏」时行情面静默缺席不可归因
            logger.warning(
                "FAPI_STREAM_DISABLED 置位——fapi 行情流/建库线程/保鲜"
                "守护器整体旁路（测试旁路面，生产环境出现此日志即为"
                "配置异常）")
        else:
            from core.data.fapi.rest import fapi_env as _fapi_env
            from core.paper_trading.runtime.feed import FixedKeyBackfill

            _env = _fapi_env()
            _proxies = (
                {"http": _env["proxy"], "https": _env["proxy"]}
                if _env["proxy"] else None)

            def _route_closed_bar(event):
                """固定订阅按需路由：无实例键丢弃（数据面恒定/决策面按需）。"""
                feed = paper_scheduler.registry.get(
                    event.symbol, event.timeframe)
                if feed is not None:
                    feed.on_closed_bar(event)

            # 单点构造（批次三 CARD-15）：两分支仅构造来源不同——
            # factory 变量归一后参数列表单写（双写是将来加参漏一侧的
            # 温床）；延迟 import 保持条件内（测试注入路径不 import
            # SDK 的语义不变）
            stream_factory = fapi_stream_factory
            if stream_factory is None:
                from core.data.fapi import BinanceFapiStream

                stream_factory = BinanceFapiStream
            fapi_stream = stream_factory(
                on_closed_bar=_route_closed_bar,
                ws_base_url=_env["ws_base"], proxies=_proxies)
            paper_scheduler.registry.fapi_stream = fapi_stream
            fapi_stream.start()
            fapi_backfill = FixedKeyBackfill(
                data_dir=data_dir, now_fn=None)
            paper_scheduler.registry.backfill = fapi_backfill
            fapi_backfill.start_background()

            # 数据保鲜守护器（契约 6 装配面）：缺省真实守护器；
            # guardian_factory 注入替身；registry.guardian 属性挂点
            # （沿 backfill 先例——属性引用非模块依赖）。守护器独立
            # 于纸盘任务存在（应用生命周期），首轮流启动即扫
            guardian_factory_ = guardian_factory
            if guardian_factory_ is None:
                from core.data.guardian import DataFreshnessGuardian

                guardian_factory_ = DataFreshnessGuardian
            fapi_guardian = guardian_factory_(data_dir)
            paper_scheduler.registry.guardian = fapi_guardian
            fapi_guardian.start_background()

        runner.start()

        yield

        # 关停序：先断推送（避免 runner 停止后回调打到空目标），再停
        # evolution runner 与纸盘调度器，再停 fapi 流/建库，随后停
        # 保鲜守护器（守护器停于 flush 前——避免 flush 后守护器再
        # 写），最后清落盘缓冲（SIGTERM 关停序——分级落盘的停机清
        # 缓冲面）
        set_ws_push_fn(None)
        runner.stop()
        runner.join(timeout=5.0)
        paper_scheduler.stop()
        paper_scheduler.join(timeout=5.0)
        if fapi_backfill is not None:
            fapi_backfill.stop()
        if fapi_stream is not None:
            fapi_stream.stop()
        if fapi_guardian is not None:
            fapi_guardian.stop()
        paper_scheduler.registry.flush_all()

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
    from api.routes import workbench as workbench_route
    from api.routes import integrity_workbench as integrity_route
    app.include_router(workbench_route.router)
    app.include_router(integrity_route.router)
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
