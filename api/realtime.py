"""socket.io 实时服务端（B8.2：WS 通道桥接，契约=web/src/realtime/contract.md）。

- namespace /trading；room=task_id（/evolution 与 /streams 已随对应
  域退役——retire-legacy B1/任务 11）
- 连接参数形制（前端 connection.ts 已冻结）：io('/{channel}',
  { query: { task_id } })——connect 时按 query.task_id 入 room
- 业务事件经 `broadcast()` 桥接（manager.push 单点旁路），payload 与
  native WS 完全一致（onAny 消费，type 字段判别）；时间字段字符串
  原样透传（B8.0 语义点④）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import parse_qs

import socketio

from .auth import check_socket_auth, get_cors_origins

logger = logging.getLogger(__name__)

sio = socketio.AsyncServer(
    async_mode="asgi", cors_allowed_origins=get_cors_origins(),
)

# 主 event loop 引用（app startup 捕获）——runner 线程跨线程 emit 用
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def capture_loop() -> None:
    """在 app startup（loop 内）捕获主循环引用。"""
    global _main_loop
    _main_loop = asyncio.get_running_loop()


def broadcast(namespace: str, room: str, event: str, payload: dict) -> None:
    """向 namespace 内 room 广播（任意线程可调；无 loop 时安全 no-op）。"""
    if _main_loop is None:
        return  # loop 未捕获（app 未 startup，如直连单元测试）——安全 no-op
    try:
        asyncio.run_coroutine_threadsafe(
            sio.emit(event, payload, namespace=namespace, room=room),
            _main_loop,
        )
    except Exception:
        logger.warning("sio broadcast failed (%s/%s)", namespace, room, exc_info=True)


def _join_by_query(sid: str, environ: dict, namespace: str) -> None:
    """按连接 query（task_id / session_id）入 room。"""
    qs = parse_qs(environ.get("QUERY_STRING") or "")
    for key in ("task_id", "session_id"):
        room = (qs.get(key) or [None])[0]
        if room:
            sio.enter_room(sid, room, namespace=namespace)
            return


@sio.on("connect", namespace="/evolution")
async def _connect_evolution(sid: str, environ: dict) -> bool | None:
    if not check_socket_auth(environ):
        return False  # A2：设置 API key 时 connect 须带同值 token
    _join_by_query(sid, environ, "/evolution")
    await _emit_task_snapshot(sid, environ, "/evolution")


@sio.on("connect", namespace="/trading")
async def _connect_trading(sid: str, environ: dict) -> bool | None:
    if not check_socket_auth(environ):
        return False
    _join_by_query(sid, environ, "/trading")
    await _emit_task_snapshot(sid, environ, "/trading")


async def _emit_task_snapshot(sid: str, environ: dict, namespace: str) -> None:
    """B8.4：连接即下发重连恢复快照（native WS 语义迁移，contract 消费纪律 4）。

    快照读库为同步调用（SQLite 本地读，connect 一次性）——to_thread 卸载。
    """
    import os
    room = (parse_qs(environ.get("QUERY_STRING") or {}).get("task_id") or [None])[0]
    if not room:
        return
    db_path = os.environ.get("MYQUANT_DB_PATH")
    if db_path is None:
        return  # 测试上下文无库——跳过快照

    def _read() -> dict | None:
        from core.persistence.repository import evolution as evo
        from core.persistence.repository import paper
        if namespace == "/evolution":
            row = evo.get_task(db_path, room)
            if row is None or row.get("status") not in ("running", "paused", "pending", "stopped"):
                return None
            return {
                "type": "task_snapshot", "task_id": room, "status": row.get("status"),
                "current_generation": row.get("current_generation", 0),
                "best_score": row.get("best_score"), "target_score": row.get("target_score"),
                "max_generations": row.get("max_generations"),
                "current_phase": row.get("current_phase"),
            }
        # /trading：纸盘新域快照（rebuild-paper-trading 5.2，design §7.2
        # 新 schema——旧 paper_trading 表读取面切换，旧仓库已随旧域
        # 退役移除，frontend-switch-and-retire R.1）。
        # 任务存在即发（五态 status 自解释——终态重连端可感知任务已结束）。
        # equity 派生与 AccountSnapshot.equity 同式（P0 权益修复联动）：
        # balance 开仓已扣 margin，持仓中须加回 position_margin——逐仓
        # 口径 equity=balance+margin+未实现盈亏；空仓 margin 项为 0；
        # 账户组 NULL（未运行）→None 透传非 0 假值
        row = paper.get_task(db_path, room)
        if row is None:
            return None
        balance = row.get("balance")
        unrealized = row.get("unrealized_pnl")
        margin = row.get("position_margin")
        equity = (None if balance is None
                  else balance + (margin or 0.0) + (unrealized or 0.0))
        return {
            "type": "task_snapshot", "task_id": room,
            "status": row.get("status"),
            "equity": equity,
            "balance": balance,
            "total_trades": row.get("total_trades", 0),
        }
    snapshot = await asyncio.to_thread(_read)
    if snapshot is not None:
        await sio.emit("task_snapshot", snapshot, namespace=namespace, room=room)



# 断开时 room 成员随 sid 自动清理——无需显式 handler
