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

from core.analysis.condition_identity import canonical_condition_hash
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import (
    ResearchError,
    register_hypothesis,
)
from core.analysis.isolation import judged_ranges_union, overlaps_with_request
from core.analysis.validation_service import _screen_reuse_check
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
        out = register_hypothesis(
            db, conditions=_conditions(),
            statement="普通登记", data_ranges=[OTHER_RANGE])
        union = judged_ranges_union(db, out["hypothesis_id"])
        # 并集只含自报区间，screen 留痕（不挂假设）不在其中
        assert all("2026-01-01" not in r["start"] for r in union)

    def test_audit_channel_entry_type_filter(self, tmp_path):
        """审计查询通道：entry_type 过滤只返回 screen 留痕。"""
        db = _db(tmp_path)
        _log_screen(db, _make_screen_task(db), ["abc"])
        register_hypothesis(db, conditions=_conditions(),
                            statement="普通登记",
                            data_ranges=[OTHER_RANGE])
        screens = rr.list_research_log(db, entry_type="screen")
        assert len(screens) == 1
        all_regs = rr.list_research_log(db, entry_type="register")
        assert len(all_regs) == 1


class TestProvenanceRegistration:
    def test_provenance_creates_screen_use_in_union(self, tmp_path):
        """声明回链：登记事务内写 screen_use，区间进判定并集（零改动）。"""
        db = _db(tmp_path)
        task_id = _make_screen_task(db)
        out = register_hypothesis(
            db, conditions=_conditions(), statement="引用扫描候选",
            data_ranges=[OTHER_RANGE],  # 自报区间≠扫描区间
            provenance={"task_id": task_id,
                        "candidate_fingerprint": "abc123"})
        assert out["duplicated"] is False
        union = judged_ranges_union(db, out["hypothesis_id"])
        hits = overlaps_with_request(union, SCAN_RANGE["start"], SCAN_RANGE["end"])
        assert hits, "扫描区间应经 screen_use 进入判定并集"

    def test_screen_use_range_comes_from_task_record(self, tmp_path):
        """G1：screen_use 区间取自任务记录的 data_range，非 AI 自报。

        包四 GAP-10 泛化后 entry_type=analysis_ref（两类统一——
        screen_candidate 与 analysis_task 同族留痕；区间取数语义不变）。
        """
        db = _db(tmp_path)
        task_id = _make_screen_task(db)  # 任务区间 = SCAN_RANGE
        register_hypothesis(
            db, conditions=_conditions(), statement="引用扫描候选",
            data_ranges=[OTHER_RANGE],
            provenance={"task_id": task_id, "candidate_fingerprint": "abc"})
        uses = rr.list_research_log(db, entry_type="analysis_ref")
        assert len(uses) == 1
        logged = json.loads(uses[0]["data_ranges"])[0]
        assert logged["start"] == SCAN_RANGE["start"]
        assert logged["end"] == SCAN_RANGE["end"]
        assert f"task {task_id}" in uses[0]["summary"], \
            "summary 携带 task_id 与 ref_type（analysis_ref 留痕形制）"

    def test_analysis_task_provenance_registers(self, tmp_path):
        """包四 GAP-10 主锚（V3a）：analysis_task 引用网格统计任务（无
        指纹）——登记成功、analysis_ref 日志在场（summary 携带 task_id
        与 ref_type）、任务区间进判定并集、旧 screen_use 键零写入。"""
        db = _db(tmp_path)
        task_id = "ant_grid00000001"
        rr.save_analysis_task(
            db, task_id=task_id, template="analysis:condition_stats",
            params=json.dumps({"template": "condition_stats",
                               "data_range": SCAN_RANGE}))
        rr.update_analysis_task(db, task_id=task_id, status="succeeded")
        out = register_hypothesis(
            db, conditions=_conditions(), statement="引用网格统计来源",
            data_ranges=[OTHER_RANGE],
            provenance={"ref_type": "analysis_task", "task_id": task_id})
        assert out["duplicated"] is False
        refs = rr.list_research_log(db, entry_type="analysis_ref")
        assert len(refs) == 1
        assert f"task {task_id}" in refs[0]["summary"]
        assert "ref_type=analysis_task" in refs[0]["summary"]
        assert rr.list_research_log(db, entry_type="screen_use") == []
        union = judged_ranges_union(db, out["hypothesis_id"])
        assert overlaps_with_request(
            union, SCAN_RANGE["start"], SCAN_RANGE["end"]), \
            "网格任务区间应经 analysis_ref 进入判定并集"

    def test_legacy_two_keys_and_bad_ref_type(self, tmp_path):
        """V3c/V3d：旧两键形制（无 ref_type）零适配可读（补默认
        screen_candidate）；非法 ref_type 拒附值域。"""
        from api.schemas import AnalysisProvenance, ScreenProvenance
        assert ScreenProvenance is AnalysisProvenance, "别名不断裂（V3f）"
        m = AnalysisProvenance.model_validate(
            {"task_id": "t", "candidate_fingerprint": "fp"})
        assert m.ref_type == "screen_candidate"
        db = _db(tmp_path)
        # 旧两键走服务层直调路径同样补默认（兼容读端到端）
        task_id = _make_screen_task(db)
        out = register_hypothesis(
            db, conditions=_conditions(), statement="旧形制登记",
            data_ranges=[OTHER_RANGE],
            provenance={"task_id": task_id, "candidate_fingerprint": "abc"})
        assert out["duplicated"] is False
        with pytest.raises(ResearchError, match="ref_type"):
            register_hypothesis(
                db, conditions=_conditions(), statement="坏引用类型",
                # 换区间避开幂等拦截（provenance 校验在幂等步之后——
                # 同条件集同区间集的第二笔会先命中 duplicated 返回）
                data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1h",
                              "start": "2024-06-01", "end": "2024-07-01"}],
                provenance={"ref_type": "bogus", "task_id": task_id})

    def test_unknown_task_rejected_without_writes(self, tmp_path):
        """谱系引用不存在任务被拒：无假设行、无任何日志写入。"""
        db = _db(tmp_path)
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=_conditions(), statement="坏引用",
                data_ranges=[OTHER_RANGE],
                provenance={"task_id": "ant_ghost000000",
                            "candidate_fingerprint": "abc"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert rr.list_hypotheses(db, limit=10) == []
        assert rr.list_research_log(db) == []

    def test_idempotent_key_excludes_provenance(self, tmp_path):
        """幂等键不含谱系：带/不带 provenance 重复登记命中同一假设。"""
        db = _db(tmp_path)
        task_id = _make_screen_task(db)
        first = register_hypothesis(
            db, conditions=_conditions(), statement="带谱系",
            data_ranges=[OTHER_RANGE],
            provenance={"task_id": task_id,
                        "candidate_fingerprint": "abc"})
        second = register_hypothesis(
            db, conditions=_conditions(), statement="不带谱系",
            data_ranges=[OTHER_RANGE])
        assert first["duplicated"] is False
        assert second["duplicated"] is True
        assert second["hypothesis_id"] == first["hypothesis_id"]


class TestReverseCheck:
    def _hyp(self, db: Path, cond: dict | None = None) -> str:
        out = register_hypothesis(
            db, conditions=_conditions(cond), statement="复用核查假设",
            data_ranges=[OTHER_RANGE])
        return out["hypothesis_id"]

    def test_exact_reuse_rejected(self, tmp_path):
        """精确复用：同指纹 + 区间重叠 → SCREEN_CANDIDATE_REUSE 409。"""
        db = _db(tmp_path)
        fp = canonical_condition_hash(CANDIDATE_COND, SCAN_RANGE["timeframe"])
        _log_screen(db, _make_screen_task(db), [fp])
        hid = self._hyp(db)
        with pytest.raises(ResearchError) as ei:
            _screen_reuse_check(db, hid, SCAN_RANGE)
        assert ei.value.code == "SCREEN_CANDIDATE_REUSE"
        assert ei.value.status == 409
        assert ei.value.detail["screen_task_id"] == "ant_screen000001"
        assert fp in ei.value.detail["hit_fingerprints"]

    def test_modified_condition_passes(self, tmp_path):
        """实质修改条件（阈值不同→指纹不同）→ 放行。"""
        db = _db(tmp_path)
        fp = canonical_condition_hash(CANDIDATE_COND, SCAN_RANGE["timeframe"])
        _log_screen(db, _make_screen_task(db), [fp])
        modified = dict(CANDIDATE_COND, target=30)  # 阈值 25→30
        hid = self._hyp(db, modified)
        _screen_reuse_check(db, hid, SCAN_RANGE)  # 不抛即通过

    def test_disjoint_range_passes(self, tmp_path):
        """跨区间：指纹相同但验证区间与扫描区间无重叠 → 放行。"""
        db = _db(tmp_path)
        fp = canonical_condition_hash(CANDIDATE_COND, SCAN_RANGE["timeframe"])
        _log_screen(db, _make_screen_task(db), [fp])
        hid = self._hyp(db)
        _screen_reuse_check(db, hid, OTHER_RANGE)  # 不抛即通过

    def test_cross_timeframe_passes(self, tmp_path):
        """跨周期：同条件不同 timeframe → 指纹不同 → 放行（D4）。"""
        db = _db(tmp_path)
        fp_1h = canonical_condition_hash(CANDIDATE_COND, "1h")
        _log_screen(db, _make_screen_task(db), [fp_1h])
        hid = self._hyp(db)
        # 请求 4h 周期、时间重叠——但假设条件按 4h 算指纹与 1h 指纹不同
        req_4h = dict(SCAN_RANGE, timeframe="4h")
        _screen_reuse_check(db, hid, req_4h)  # 不抛即通过

    def test_corrupt_summary_skipped_not_blocking(self, tmp_path):
        """解析容错（G4）：损坏 summary 跳过该条，不挡验证。"""
        db = _db(tmp_path)
        rr.append_research_log(
            db, hypothesis_id=None, entry_type="screen",
            ref_id="ant_corrupt00001",
            data_ranges=json.dumps([SCAN_RANGE]),
            summary="not-a-json{")
        hid = self._hyp(db)
        _screen_reuse_check(db, hid, SCAN_RANGE)  # 不抛即通过

    def test_union_hit_takes_priority_form(self, tmp_path):
        """顺序保证（5.2）：screen_use 并集拦截先于反向核查——
        带 provenance 的假设在扫描区间上验证时，judged_ranges_union
        已命中 RANGE_OVERLAP 前置条件成立（本用例锁判定依据数据形态）。"""
        db = _db(tmp_path)
        task_id = _make_screen_task(db)
        out = register_hypothesis(
            db, conditions=_conditions(), statement="带谱系",
            data_ranges=[OTHER_RANGE],
            provenance={"task_id": task_id, "candidate_fingerprint": "abc"})
        union = judged_ranges_union(db, out["hypothesis_id"])
        assert overlaps_with_request(
            union, SCAN_RANGE["start"], SCAN_RANGE["end"])
        # 并集命中即 409 RANGE_OVERLAP（既有机制），无需到达反向核查


class TestScreenReusePagination:
    """CARD-01 链路 B：反向核查 screen 留痕分页取全量（原 limit=500 单页截断）。

    命中条目置于升序第 503 位（旧单页之外）：截断则查不到该条不 409，
    全量则 409——超页可见性锚。次序用直插 created_at 严格递增控制
    （append_research_log 的 _now() 无法指定位次）。
    """

    def test_超页screen日志末尾命中_仍409(self, tmp_path):
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import insert
        from core.persistence.orm.models import ResearchLog
        from core.persistence.repository.dataset_meta import _engine_for

        db = _db(tmp_path)
        fp = canonical_condition_hash(CANDIDATE_COND, SCAN_RANGE["timeframe"])
        noise_range = {"symbol": "ETHUSDT", "timeframe": "1h",
                       "start": "2026-01-01T00:00:00Z",
                       "end": "2026-04-01T00:00:00Z"}
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)

        def _row(i: int) -> dict:
            hit = i == 502  # 第 503 条：升序末尾，旧 limit=500 之外
            return dict(
                log_id=f"log_{i:04d}", hypothesis_id=None,
                entry_type="screen", ref_id=f"ant_pg_{i:04d}",
                data_ranges=json.dumps([SCAN_RANGE if hit else noise_range]),
                summary=json.dumps({"top_fingerprints": [fp] if hit else []}),
                created_at=(base + timedelta(seconds=i)).isoformat())

        with _engine_for(db).begin() as conn:
            for i in range(503):
                conn.execute(insert(ResearchLog).values(**_row(i)))

        hid = register_hypothesis(
            db, conditions=_conditions(), statement="超页核查",
            data_ranges=[OTHER_RANGE])["hypothesis_id"]
        with pytest.raises(ResearchError) as ei:
            _screen_reuse_check(db, hid, SCAN_RANGE)
        assert ei.value.code == "SCREEN_CANDIDATE_REUSE"
        assert ei.value.status == 409
        assert ei.value.detail["screen_task_id"] == "ant_pg_0502"


class TestScreenReuseTolerance:
    """阶段四盲区消解：反向核查循环的解析容错族（坏 JSON/坏区间形/
    非法条目逐条跳过不拖垮核查——可用性优先语义）。"""

    @pytest.mark.parametrize(
        ("data_ranges", "summary"),
        [
            ("{bad json", '{"top_fingerprints": ["fp"]}'),   # data_ranges 坏 JSON
            ('["plain-string"]', '{"top_fingerprints": ["fp"]}'),  # 非列表
            ('[123]', '{"top_fingerprints": ["fp"]}'),        # 非 dict 条目
            ('[{"start": "2026-01-01T00:00:00Z"}]',           # 缺 end 键
             '{"top_fingerprints": ["fp"]}'),
            ('[{"symbol": "BTCUSDT", "timeframe": "1h", '
             '"start": "bad-ts", "end": "2026-04-01T00:00:00Z"}]',  # 坏时间戳
             '{"top_fingerprints": ["fp"]}'),
            (json.dumps([SCAN_RANGE]), "not-a-json{"),       # summary 坏 JSON
            (json.dumps([SCAN_RANGE]), '["list-summary"]'),   # summary 非 dict
        ],
        ids=["ranges坏JSON", "ranges非列表", "条目非dict", "条目缺键",
             "坏时间戳", "summary坏JSON", "summary非dict"])
    def test_坏形日志逐条跳过_核查不炸(self, tmp_path, data_ranges, summary):
        from sqlalchemy import insert
        from core.persistence.orm.models import ResearchLog
        from core.persistence.repository.dataset_meta import _engine_for
        from datetime import datetime, timedelta, timezone

        db = _db(tmp_path)
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with _engine_for(db).begin() as conn:
            for i in range(3):
                conn.execute(insert(ResearchLog).values(
                    log_id=f"log_tol_{i}", hypothesis_id=None,
                    entry_type="screen", ref_id=f"ant_tol_{i}",
                    data_ranges=data_ranges, summary=summary,
                    created_at=(base + timedelta(seconds=i)).isoformat()))
        hid = register_hypothesis(
            db, conditions=_conditions(), statement="容错核查",
            data_ranges=[OTHER_RANGE])["hypothesis_id"]
        _screen_reuse_check(db, hid, SCAN_RANGE)  # 坏形全跳过，不抛


class TestScreenReusePrecheckTolerance:
    """阶段四盲区消解：反向核查前置段（假设级容错）。

    不可达判定（记录）：指纹计算与影子指纹的 except 分支不可达——
    登记校验已保证条件经 canonical 化（合法形），canonical/legacy
    两算法对合法条件不抛；分支为防御性兜底。"""

    def test_假设不存在_静默返回(self, tmp_path):
        db = _db(tmp_path)
        _screen_reuse_check(db, "hyp_ghost", SCAN_RANGE)  # 不炸即过

    def test_conditions坏JSON_静默返回(self, tmp_path):
        db = _db(tmp_path)
        hid = register_hypothesis(
            db, conditions=_conditions(), statement="坏JSON",
            data_ranges=[OTHER_RANGE])["hypothesis_id"]
        from sqlalchemy import update
        from core.persistence.orm.models import ResearchHypothesis
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(db).begin() as conn:
            conn.execute(update(ResearchHypothesis).where(
                ResearchHypothesis.hypothesis_id == hid).values(
                conditions="{bad json"))
        _screen_reuse_check(db, hid, SCAN_RANGE)  # 坏 JSON 静默返回

    def test_条件组全空_指纹集空静默返回(self, tmp_path):
        db = _db(tmp_path)
        hid = register_hypothesis(
            db, conditions=_conditions(), statement="空组",
            data_ranges=[OTHER_RANGE])["hypothesis_id"]
        from sqlalchemy import update
        from core.persistence.orm.models import ResearchHypothesis
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(db).begin() as conn:
            conn.execute(update(ResearchHypothesis).where(
                ResearchHypothesis.hypothesis_id == hid).values(
                conditions='{"when_conditions": [], "then_conditions": []}'))
        _screen_reuse_check(db, hid, SCAN_RANGE)  # 指纹集空返回


class TestAnalysisTaskSameCore:
    """包四 V3e 补（tester 审查）：两类引用的任务存在性核验同核——
    analysis_task 形态引用不存在任务同样拒且零写入（screen_candidate
    形态既有 test_unknown_task_rejected_without_writes 已证）。"""

    def test_analysis_task_unknown_rejected_no_writes(self, tmp_path):
        db = _db(tmp_path)
        with pytest.raises(ResearchError, match="分析任务"):
            register_hypothesis(
                db, conditions=_conditions(), statement="引用幽灵网格任务",
                data_ranges=[OTHER_RANGE],
                provenance={"ref_type": "analysis_task",
                            "task_id": "ant_ghost00001"})
        assert rr.list_research_log(db) == [], "拒绝路径零留痕"
