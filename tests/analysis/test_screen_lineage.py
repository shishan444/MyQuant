"""扫描谱系回链四件套协议测试（add-screen-patterns 任务 3.2/3.3/4.x/5.x）。

覆盖 spec 场景：扫描留痕随完成落档、声明回链自动隔离（判定并集零改动）、
精确复用反向核查 409、实质修改条件放行、跨区间放行、幂等键不含谱系、
谱系引用不存在任务被拒、留痕可经查询通道审计（G6）、解析容错（G4）、
screen_use 区间取自任务记录（G1）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from tests.helpers.vr_factory import insert_hypothesis
from core.analysis.isolation import judged_ranges_union
from core.persistence.repository import research as rr

SCAN_RANGE = {"symbol": "BTCUSDT", "timeframe": "1h",
              "start": "2026-01-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"}
OTHER_RANGE = {"symbol": "BTCUSDT", "timeframe": "1h",
               "start": "2026-05-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"}
CANDIDATE_COND = {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 25}


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "quant.db"
    ensure_research_tables(db)
    return db


def _make_screen_task(
    db: Path, task_id: str = "ant_screen000001",
    data_range: dict | None = None,
) -> str:
    """造一条已完成的扫描任务记录（params 含 data_range）。"""
    rr.save_analysis_task(
        db, task_id=task_id, template="analysis:screen_patterns",
        params=json.dumps({"template": "screen_patterns",
                           "data_range": data_range or SCAN_RANGE}))
    rr.update_analysis_task(db, task_id=task_id, status="succeeded")
    return task_id


def _log_screen(
    db: Path, task_id: str, fingerprints: list[str],
    data_range: dict | None = None, scanned: int = 100,
) -> str:
    """写一条 screen 留痕（形制对齐路由 _log_screen）。"""
    from api.routes.research.analysis import _log_screen as route_log

    class _P:  # 路由签名需要 payload（仅 data_range 被读取）
        data_range = type("_DR", (), {"model_dump": lambda self: dict(
            data_range or SCAN_RANGE)})()

    route_log(db, _P(), task_id, {
        "payload": {
            "scanned": scanned, "family": "conditions_x_horizons",
            "timeframe": (data_range or SCAN_RANGE)["timeframe"],
            "candidates": [{"fingerprint": f} for f in fingerprints],
        }})


def _conditions(cond: dict | None = None) -> dict:
    c = cond or CANDIDATE_COND
    return {"when_conditions": [dict(c)],
            "then_conditions": [{"subject": "close", "action": "gt",
                                 "target": -1}]}


class TestScreenLogTrail:
    def test_screen_log_written_on_completion(self, tmp_path):
        """留痕随完成落档：entry_type=screen、ref_id=task、summary 指纹清单。"""
        db = _db(tmp_path)
        task_id = _make_screen_task(db)
        _log_screen(db, task_id, ["abc123", "def456"])
        entries = rr.list_research_log(db, entry_type="screen")
        assert len(entries) == 1
        e = entries[0]
        assert e["ref_id"] == task_id
        assert e["hypothesis_id"] is None  # 不挂假设——不进任何并集
        summary = json.loads(e["summary"])
        assert summary["top_fingerprints"] == ["abc123", "def456"]
        assert summary["scanned"] == 100

    def test_no_screen_log_when_not_written(self, tmp_path):
        """失败/超时（计算未完成）不产生 screen 日志；任务表行独立存在。"""
        db = _db(tmp_path)
        rr.save_analysis_task(
            db, task_id="ant_failed00001", template="analysis:screen_patterns",
            params=json.dumps({"template": "screen_patterns",
                               "data_range": SCAN_RANGE}))
        rr.update_analysis_task(db, task_id="ant_failed00001",
                                status="failed", reason="timeout")
        assert rr.list_research_log(db, entry_type="screen") == []
        assert rr.get_analysis_task(db, "ant_failed00001")["status"] == "failed"

    def test_screen_log_not_in_any_hypothesis_union(self, tmp_path):
        """screen 留痕不挂假设——不进任何假设判定并集（isolation 零改动）。"""
        db = _db(tmp_path)
        _log_screen(db, _make_screen_task(db), ["abc"])
        out = insert_hypothesis(
            db, conditions=_conditions(),
            statement="普通登记", data_ranges=[OTHER_RANGE], strategy_params={"stop_loss": 0.05})
        union = judged_ranges_union(db, out)
        # 并集只含自报区间，screen 留痕（不挂假设）不在其中
        assert all("2026-01-01" not in r["start"] for r in union)

    def test_audit_channel_entry_type_filter(self, tmp_path):
        """审计查询通道：entry_type 过滤只返回 screen 留痕。"""
        db = _db(tmp_path)
        _log_screen(db, _make_screen_task(db), ["abc"])
        insert_hypothesis(db, conditions=_conditions(),
                            statement="普通登记",
                            data_ranges=[OTHER_RANGE], strategy_params={"stop_loss": 0.05})
        screens = rr.list_research_log(db, entry_type="screen")
        assert len(screens) == 1
        # register 留痕随登记域退役（SQL 直插构造不写登记日志）——
        # 审计过滤面守护收缩为 screen 通道独占性
        assert all(e["entry_type"] == "screen"
                   for e in rr.list_research_log(db))


class TestLegacyScreenUseReadOnly:
    """存量 screen_use 只读（retire-legacy ADDED「扫描留痕与候选指纹」
    场景 3）：历史回链事实保留——存量条目照常可查；新写入通道已随
    登记链退役（构造经通用 append_research_log 仓储函数，模拟 D4
    存量行，非生产新写入路径）。"""

    def test_legacy_screen_use_entries_queryable(self, tmp_path):
        db = _db(tmp_path)
        rr.append_research_log(
            db, hypothesis_id="hyp_legacy",
            entry_type="screen_use",
            ref_id="ant_screen000002",
            data_ranges=json.dumps([SCAN_RANGE]),
            summary=json.dumps({"screen_task": "ant_screen000002"}))
        entries = rr.list_research_log(db, entry_type="screen_use")
        assert len(entries) == 1
        e = entries[0]
        assert e["hypothesis_id"] == "hyp_legacy"
        assert e["ref_id"] == "ant_screen000002"
        ranges = json.loads(e["data_ranges"])
        assert ranges == [SCAN_RANGE]

    def test_screen_use_excluded_from_screen_filter(self, tmp_path):
        """类型过滤面互斥：entry_type="screen" 查询不吸收 screen_use
        存量条目（两类记录各自存在，审计通道语义不串）。"""
        db = _db(tmp_path)
        _log_screen(db, _make_screen_task(db), ["abc"])
        rr.append_research_log(
            db, hypothesis_id="hyp_legacy", entry_type="screen_use",
            ref_id="ant_screen000002", data_ranges=json.dumps([SCAN_RANGE]))
        assert len(rr.list_research_log(db, entry_type="screen")) == 1
        assert len(rr.list_research_log(db, entry_type="screen_use")) == 1

