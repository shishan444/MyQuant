"""假设服务入口校验特征测试（修复期特征网：记录现状行为）。

hypothesis_service 的登记/迁移主链路由 test_strategy_v2 / test_mtf_-
conditions / test_draft_lifecycle 承接；本文件补两处零覆盖：
allowed_subject_columns（subject 可引用列清单——此前全域零引用）与
transition_hypothesis 的 superseded 证据校验分支。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.column_ref import BASE_SUBJECT_COLUMNS
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import (
    ResearchError,
    allowed_subject_columns,
    register_hypothesis,
    transition_hypothesis,
)
from core.persistence.db_ext import init_db_ext


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _register(db: Path, statement: str = "s") -> str:
    return register_hypothesis(
        db,
        conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        },
        statement=statement,
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2024-01-01T00:00:00Z",
                      "end": "2024-02-01T00:00:00Z"}],
    )["hypothesis_id"]


class TestAllowedSubjectColumns:
    def test_空库_基础列全集与注册表前缀集(self, db: Path) -> None:
        cols, prefixes = allowed_subject_columns(db)

        # 精确列全集 ⊇ 八个基础列（open/high/low/close/volume/trades/
        # open_interest/funding_rate）
        assert BASE_SUBJECT_COLUMNS <= cols
        # 前缀集 = 指标注册表 output_fields 拍平（单一事实源锚——
        # 前缀集不得另立来源）
        from core.features.registry import INDICATOR_REGISTRY
        expected = {f for defn in INDICATOR_REGISTRY.values()
                    for f in defn.output_fields}
        assert prefixes == expected
        assert prefixes  # 非空（注册表非空）

    def test_已注册插件列_并入精确列集(self, db: Path) -> None:
        from core.analysis.plugin_service import register_formula_plugin
        before_cols, _ = allowed_subject_columns(db)
        register_formula_plugin(db, name="fxa", expression="zscore(close, 20)")

        cols, _ = allowed_subject_columns(db)

        added = cols - before_cols
        # 现状：version=1 注册列名 = fx_<name>（并入 subject 可引用集）
        assert added == {"fx_fxa"}

    def test_前缀集不含基础列(self, db: Path) -> None:
        # 前缀集仅指标输出字段——基础列走精确列集，两集职责分离
        _, prefixes = allowed_subject_columns(db)
        assert not (prefixes & BASE_SUBJECT_COLUMNS)


class TestTransitionSuperseded:
    def test_接替假设不存在_拒绝(self, db: Path) -> None:
        hid = _register(db)
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="superseded",
                                  evidence_ref="hyp_ghost")
        assert ei.value.code == "RESEARCH_EVIDENCE_NOT_FOUND"

    def test_接替假设存在_迁移成功(self, db: Path) -> None:
        succ = _register(db, statement="接替者")
        hid = _register(db, statement="被接替")

        out = transition_hypothesis(db, hid, target_status="superseded",
                                    evidence_ref=succ)

        assert out["hypothesis_id"] == hid
        assert out["status"] == "superseded"
        assert out["idempotent"] is False
        # superseded 非转正态
        assert out["promoted"] is False

    def test_被接替后即终态_不可再迁移(self, db: Path) -> None:
        succ = _register(db, statement="接替者")
        hid = _register(db, statement="被接替")
        transition_hypothesis(db, hid, target_status="superseded",
                              evidence_ref=succ)

        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="abandoned",
                                  evidence_ref="说明文本")
        assert ei.value.code == "RESEARCH_HYPOTHESIS_TERMINAL"
        assert ei.value.status == 409

    def test_超页证据记录末尾pass_可定证迁移(self, db: Path) -> None:
        """CARD-01 链路 D：迁移证据核验分页取全量（原 limit=200 单页截断）。

        引用记录置于升序第 201 位（旧单页之外）：截断则证据不可达
        （RESEARCH_EVIDENCE_NOT_FOUND），全量则定证成功。次序用直插
        created_at 严格递增控制。"""
        import json as _json
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import insert
        from core.persistence.orm.models import ValidationRecord
        from core.persistence.repository.dataset_meta import _engine_for

        hid = _register(db, statement="超页证据")
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)
        far_id = "rec_pg_0200"  # 第 201 条：升序末尾，旧 limit=200 之外

        def _row(i: int) -> dict:
            hit = i == 200
            return dict(
                record_id=f"rec_pg_{i:04d}", hypothesis_id=hid,
                strategy_id=None,
                level="isolated",
                params=_json.dumps({"warmup": 200, "seed": i}),
                result=None,
                verdict="pass" if hit else "fail",
                range_hash=f"rh_pg_{i:04d}",
                components=None,
                created_at=(base + timedelta(seconds=i)).isoformat())

        with _engine_for(db).begin() as conn:
            for i in range(201):
                conn.execute(insert(ValidationRecord).values(**_row(i)))

        out = transition_hypothesis(db, hid, target_status="supported",
                                    evidence_ref=far_id)
        assert out["status"] == "supported"
        assert out["idempotent"] is False


class TestRegisterValidationGaps:
    """阶段四盲区消解：登记校验链拒绝面参数化直测。"""

    _BASE = dict(
        conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        },
        statement="s",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2024-01-01T00:00:00Z",
                      "end": "2024-02-01T00:00:00Z"}])

    @pytest.mark.parametrize(
        ("mutate", "code"),
        [
            (lambda kw: kw["conditions"].update(when_conditions=[]),
             "when 空"),
            (lambda kw: kw["conditions"].update(when_conditions="x"),
             "when 非列表"),
            (lambda kw: kw["conditions"].update(then_conditions=[]),
             "then 空"),
            (lambda kw: kw["conditions"]["when_conditions"].append(
                {"subject": "close"}), "条件缺键"),
            (lambda kw: kw.update(data_ranges=[]), "ranges 空"),
            (lambda kw: kw["data_ranges"][0].pop("symbol"), "range 缺键"),
            (lambda kw: kw["data_ranges"][0].update(
                start="2024-03-01T00:00:00Z"), "start>=end"),
            (lambda kw: kw["data_ranges"].append(
                {"symbol": "ETHUSDT", "timeframe": "4h",
                 "start": "2024-01-01T00:00:00Z",
                 "end": "2024-02-01T00:00:00Z"}), "跨 symbol"),
        ],
        ids=["when空", "when非列表", "then空", "条件缺键", "ranges空",
             "range缺键", "start>=end", "跨symbol"])
    def test_校验拒绝面(self, db: Path, mutate, code) -> None:
        kw = {k: (dict(v) if isinstance(v, dict) else
                  ([dict(r) for r in v] if isinstance(v, list) else v))
              for k, v in self._BASE.items()}
        kw["conditions"] = {
            "when_conditions": [dict(self._BASE["conditions"]["when_conditions"][0])],
            "then_conditions": [dict(self._BASE["conditions"]["then_conditions"][0])]}
        mutate(kw)
        with pytest.raises(ResearchError):
            register_hypothesis(db, **kw)

    def test_条件带未知timeframe_拒(self, db: Path) -> None:
        kw = {**self._BASE}
        kw["conditions"] = {
            "when_conditions": [dict(
                self._BASE["conditions"]["when_conditions"][0],
                timeframe="7m")],
            "then_conditions": [dict(
                self._BASE["conditions"]["then_conditions"][0])]}
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(db, **kw)
        assert ei.value.code == "TIMEFRAME_UNKNOWN"

    def test_基准周期未知_拒(self, db: Path) -> None:
        """该校验在条件 timeframe 键的方向校验块内触发（纯 2d 基准
        无 timeframe 键条件不进入该面——现状结构）。"""
        kw = {**self._BASE}
        kw["data_ranges"] = [dict(self._BASE["data_ranges"][0], timeframe="2d")]
        kw["conditions"] = {
            "when_conditions": [dict(
                self._BASE["conditions"]["when_conditions"][0],
                timeframe="4h")],
            "then_conditions": [dict(
                self._BASE["conditions"]["then_conditions"][0])]}
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(db, **kw)
        assert ei.value.code == "TIMEFRAME_UNKNOWN"
        assert "基准周期" in ei.value.message

    def test_provenance_三拒与坏params容错(self, db: Path) -> None:
        base = dict(self._BASE)
        # 形制缺键
        with pytest.raises(ResearchError, match="provenance"):
            register_hypothesis(db, **base,
                                provenance={"task_id": "t1"})
        # 任务不存在
        with pytest.raises(ResearchError, match="扫描任务"):
            register_hypothesis(db, **base,
                                provenance={"task_id": "ghost_task",
                                            "candidate_fingerprint": "fp"})
        # 任务在但 params 坏 JSON（容错为 {}）→ 区间事实缺失拒
        from core.persistence.repository import research as rr
        rr.save_analysis_task(db, task_id="task_badjson",
                              template="analysis:screen_patterns",
                              params="{not json")
        with pytest.raises(ResearchError, match="缺区间事实"):
            register_hypothesis(db, **base,
                                provenance={"task_id": "task_badjson",
                                            "candidate_fingerprint": "fp"})


class TestTransitionGaps:
    """阶段四盲区消解：迁移入口分支与并发幂等路径。"""

    def test_非法目标态_拒(self, db: Path) -> None:
        hid = _register(db)
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="bogus",
                                  evidence_ref="x")
        assert ei.value.code == "RESEARCH_TRANSITION_INVALID"

    def test_空证据引用_拒(self, db: Path) -> None:
        hid = _register(db)
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="abandoned",
                                  evidence_ref="")
        assert ei.value.code == "RESEARCH_EVIDENCE_NOT_FOUND"

    def test_假设不存在_404(self, db: Path) -> None:
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, "hyp_ghost", target_status="abandoned",
                                  evidence_ref="x")
        assert ei.value.code == "RESEARCH_HYPOTHESIS_NOT_FOUND"
        assert ei.value.status == 404

    def test_并发竞争_rows零_重查已迁_幂等返回(self, db: Path,
                                                  monkeypatch) -> None:
        """rows==0 并发分支：另一请求先迁移——重查已到目标态即幂等
        返回（repository 层 mock 单点注入竞争窗口）。"""
        hid = _register(db)
        succ = _register(db, statement="接替者")
        transition_hypothesis(db, hid, target_status="superseded",
                              evidence_ref=succ)

        from core.persistence.repository import research as rr
        real = rr.transition_hypothesis_with_promotion

        def racing(db_path, *a, **kw):
            real(db_path, *a, **kw)      # 实际迁移发生
            return {"rows": 0, "promoted": False}  # 但返回竞争窗口值

        monkeypatch.setattr(rr, "transition_hypothesis_with_promotion",
                            racing)
        out = transition_hypothesis(db, hid, target_status="superseded",
                                    evidence_ref=succ)
        assert out["idempotent"] is True
        assert out["status"] == "superseded"

    def test_并发竞争_rows零_重查他态_409(self, db: Path,
                                             monkeypatch) -> None:
        """rows==0 且重查为其它终态——并发冲突 409。"""
        hid = _register(db, statement="竞争A")
        other = _register(db, statement="竞争B")
        # 目标态 supported 的证据核验先于迁移——须有真实 pass 记录
        from core.persistence.repository import research as rr
        rr.save_validation_with_log(
            db, record_id="rec_race", hypothesis_id=hid, strategy_id=None,
            level="isolated_backtest", params="{}", result=None,
            verdict="pass", range_hash="rh_race")

        real = rr.transition_hypothesis_with_promotion  # patch 前捕获

        def racing(db_path, *a, **kw):
            # 竞争窗口内：hid 已被 other 接替（非目标态 supported）
            real(db_path, hid, target_status="superseded",
                 terminal_ref=other, superseded_by=other, promote=False)
            return {"rows": 0, "promoted": False}

        monkeypatch.setattr(rr, "transition_hypothesis_with_promotion",
                            racing)
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="supported",
                                  evidence_ref="rec_race")
        assert ei.value.code == "RESEARCH_HYPOTHESIS_TERMINAL"
        assert ei.value.status == 409
