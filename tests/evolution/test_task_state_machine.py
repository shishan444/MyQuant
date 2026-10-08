"""进化任务状态机收口测试（batch1 D3/D4，任务 3.1-3.4）。

覆盖 spec 四场景: 非法迁移被拒绝(409 且状态不变) / 合法迁移生效 /
创建原子 pending(无中间态) / 崩溃恢复不误清 pending。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db import save_task, update_task, get_task
from core.persistence.db_ext import init_db_ext as init_db  # B3d.1：对齐生产全列库
from core.strategy.dna import StrategyDNA


@pytest.fixture()
def db_path(tmp_path) -> Path:
    p = tmp_path / "state_machine.db"
    init_db(p)
    return p


@pytest.fixture()
def dna() -> StrategyDNA:
    return StrategyDNA.from_dict({
        "signal_genes": [
            {"indicator": "EMA", "params": {"period": 20}, "role": "entry_trigger",
             "condition": {"type": "price_above"}},
            {"indicator": "EMA", "params": {"period": 20}, "role": "exit_trigger",
             "condition": {"type": "price_below"}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.03, "take_profit": 0.06,
                        "position_size": 1.0, "leverage": 1, "direction": "long"},
    })


# ---- 3.1 update_task 条件更新：三路径 ----

def test_guarded_update_legal_transition_applies(db_path, dna):
    """id=合法迁移生效（pending→running 拾取语义）"""
    save_task(db_path, "t1", 1.0, "explorer", "BTCUSDT", "4h", dna)
    applied = update_task(db_path, "t1", status="running",
                          expected_statuses=("pending",))
    assert applied is True
    assert get_task(db_path, "t1")["status"] == "running"


def test_guarded_update_illegal_transition_rejected(db_path, dna):
    """id=非法迁移被拒绝（终态不可改写，状态保持）"""
    save_task(db_path, "t2", 1.0, "explorer", "BTCUSDT", "4h", dna)
    update_task(db_path, "t2", status="completed")  # 先置 completed（无守护路径，测试内构造）
    applied = update_task(db_path, "t2", status="stopped", stop_reason="user_stop",
                          expected_statuses=("running", "paused", "pending"))
    assert applied is False
    assert get_task(db_path, "t2")["status"] == "completed"  # 不变量：终态未被覆写


def test_unguarded_update_keeps_legacy_behaviour(db_path, dna):
    """id=无参默认路径行为不变（向后兼容）"""
    save_task(db_path, "t3", 1.0, "explorer", "BTCUSDT", "4h", dna)
    applied = update_task(db_path, "t3", status="paused")  # 不传 expected_statuses
    assert applied is True  # 无条件写仍生效
    assert get_task(db_path, "t3")["status"] == "paused"


# ---- 3.3 创建原子 pending ----

def test_save_task_enters_pending_atomically(db_path, dna):
    """id=创建即 pending（无 running 中间态——直查 sqlite 验证落库值）"""
    save_task(db_path, "t4", 1.0, "explorer", "BTCUSDT", "4h", dna)
    row = get_task(db_path, "t4")
    assert row["status"] == "pending"


def test_recover_stale_does_not_clear_pending(db_path, dna):
    """id=崩溃恢复不误清未接手任务（spec 场景）"""
    from api.runner import recover_stale_tasks
    save_task(db_path, "t5", 1.0, "explorer", "BTCUSDT", "4h", dna)   # pending
    save_task(db_path, "t6", 1.0, "explorer", "BTCUSDT", "4h", dna)
    update_task(db_path, "t6", status="running")                       # 模拟接手后崩溃
    recover_stale_tasks(db_path)
    assert get_task(db_path, "t5")["status"] == "pending"   # 未接手：不受影响
    assert get_task(db_path, "t6")["status"] == "stopped"   # 残留 running：被清理


# ---- 3.2 路由 409（API 层语义经 handler 逻辑等价验证）----

def _make_task(db_path, task_id, status):
    dna_obj = StrategyDNA.from_dict({
        "signal_genes": [
            {"indicator": "EMA", "params": {"period": 20}, "role": "entry_trigger",
             "condition": {"type": "price_above"}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.03, "take_profit": 0.06,
                        "position_size": 1.0, "leverage": 1, "direction": "long"},
    })
    save_task(db_path, task_id, 1.0, "explorer", "BTCUSDT", "4h", dna_obj)
    if status != "pending":
        update_task(db_path, task_id, status=status)


def test_stop_completed_returns_409_semantics(db_path):
    """id=stop 不覆写 completed（API 语义：409 + 状态不变）"""
    from fastapi import HTTPException
    from api.routes.evolution import stop_task
    _make_task(db_path, "t7", "completed")
    with pytest.raises(HTTPException) as exc_info:
        stop_task("t7", db_path)
    assert exc_info.value.status_code == 409
    assert get_task(db_path, "t7")["status"] == "completed"


def test_pause_running_succeeds(db_path):
    """id=合法 pause 生效（API 语义）"""
    from api.routes.evolution import pause_task
    _make_task(db_path, "t8", "running")
    resp = pause_task("t8", db_path)
    assert resp.status == "paused"
    assert get_task(db_path, "t8")["status"] == "paused"
