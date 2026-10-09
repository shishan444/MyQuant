"""evolution 域对外兼容委托面（B3d.1 迁移后保留壳）。

职能：init_db 经 ORM 元数据建全部表（models.py 全量，非仅
evolution 三表——模型为唯一事实源）；其余 7 个同名函数签名不变
转发 repository.evolution（职责契约的权威描述在 repository 侧，
本模块只保证调用方零改动）。
服务对象：api/runner.py、api/routes/evolution.py、
core/services/evolution_service.py 及 tests（状态机守护/错误契约等）。
职责边界：不持 SQL 与连接——连接一律经 core.persistence.orm.engine
工厂。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


# B3d.2 退役注记：_connect/connect 已删——业务代码连接一律经
# core.persistence.orm.engine 工厂（WAL/busy_timeout 统一）；legacy
# init/ALTER 链的连接工具移至 db_ext（迁移工具属性，随 init 轨存续）。


def init_db(db_path: Path) -> None:
    """建全部表（IF NOT EXISTS 幂等；B3d.1 起经 ORM 元数据建表，模型为唯一事实源）。"""
    from core.persistence.orm import Base, create_db_engine
    engine = create_db_engine(db_path)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


def save_task(db_path: Path, task_id: str, target_score: float, template: str,
             symbol: str, timeframe: str, initial_dna, status: str = "pending") -> None:
    """创建进化任务（委托 repository.evolution，签名不变——D4 单次
    写入即入 pending 的语义见 repository 侧权威描述）。"""
    from core.persistence.repository import evolution as repo
    repo.save_task(db_path, task_id, target_score, template, symbol, timeframe,
                   initial_dna, status=status)


def update_task(db_path: Path, task_id: str, status: Optional[str] = None,
                champion_dna=None, stop_reason: Optional[str] = None,
                expected_statuses: Optional[tuple] = None) -> bool:
    """更新任务状态/冠军/停止原因（委托 repository，D3 条件更新语义不变）。"""
    from core.persistence.repository import evolution as repo
    return repo.update_task(db_path, task_id, status=status, champion_dna=champion_dna,
                            stop_reason=stop_reason,
                            expected_statuses=expected_statuses)


def get_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取进化任务行（委托 repository，不存在返回 None）。"""
    from core.persistence.repository import evolution as repo
    return repo.get_task(db_path, task_id)


def save_snapshot(db_path: Path, task_id: str, generation: int, best_score: float,
                  avg_score: float, best_dna, population, mutation_log: Optional[str] = None,
                  wf_months: Optional[str] = None) -> None:
    """保存代际全种群快照（委托 repository，INSERT OR REPLACE 复合主键幂等——resume 载体）。"""
    from core.persistence.repository import evolution as repo
    repo.save_snapshot(db_path, task_id, generation, best_score, avg_score, best_dna,
                       population, mutation_log=mutation_log, wf_months=wf_months)


def save_history(db_path: Path, task_id: str, generation: int, best_score: float,
                 avg_score: float, top3_summary: str) -> None:
    """保存代际轻量分数记录（委托 repository，UI 图表用，OR REPLACE 幂等）。"""
    from core.persistence.repository import evolution as repo
    repo.save_history(db_path, task_id, generation, best_score, avg_score, top3_summary)


def get_history(db_path: Path, task_id: str) -> List[Dict[str, Any]]:
    """取任务全部历史记录（委托 repository，按代际升序）。"""
    from core.persistence.repository import evolution as repo
    return repo.get_history(db_path, task_id)


def list_all_tasks(db_path: Path, status: Optional[str] = None, limit: int = 50,
                   offset: int = 0) -> List[Dict[str, Any]]:
    """列进化任务（委托 repository，created_at 倒序+status 过滤+分页）。"""
    from core.persistence.repository import evolution as repo
    return repo.list_all_tasks(db_path, status=status, limit=limit, offset=offset)


def count_all_tasks(db_path: Path, status: Optional[str] = None) -> int:
    """计进化任务总数（委托 repository，可按 status 过滤）。"""
    from core.persistence.repository import evolution as repo
    return repo.count_all_tasks(db_path, status=status)
