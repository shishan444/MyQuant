"""研究域 repository 列表/迁移组合函数特征网（fix/mcp-research-upstream-review）。

修复期临时安全网（现状记录语义）：钉住 transition_hypothesis_with_promotion
（promote 四分支）、save_hypothesis_with_log 的 screen_use 谱系分支、
list_* 三函数的过滤组合与 offset/limit 边界、幂等/可见性/回收边界的
既有行为——上游修复触碰这些函数时，行为漂移在此显形。基准源：
core/persistence/DESIGN 卡语义经函数 docstring 与既有测试网
（test_research_repository.py）锚定。
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from sqlalchemy import insert, select

from core.persistence.orm.models import AnalysisTask, SignalRegistryEntry, Strategy
from core.persistence.repository import research as rr
from core.persistence.repository.dataset_meta import _engine_for


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """升级到 head 的临时库（研究域八表 + strategy 表就位）。"""
    db = tmp_path / "research_listing.db"
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", "upgrade", "head"],
        capture_output=True, text=True, check=True,
    )
    return db


def _hyp_kwargs(hypothesis_id: str = "hyp_1", chash: str = "ch1",
                rhash: str = "rh1") -> dict:
    return dict(
        hypothesis_id=hypothesis_id,
        conditions='{"when_conditions": []}',
        conditions_hash=chash,
        statement="特征网假设",
        data_ranges='[{"symbol": "BTCUSDT", "timeframe": "1h", "start": "2024-01-01", "end": "2024-06-01"}]',
        ranges_hash=rhash,
    )


def _insert_strategy(db_path: Path, strategy_id: str, parent_ids: str,
                     status: str = "draft") -> None:
    """直插 strategy 行（promotion 分支的最小输入，不走业务装配）。"""
    now = "2026-10-01T00:00:00"
    with _engine_for(db_path).begin() as conn:
        conn.execute(insert(Strategy).values(
            strategy_id=strategy_id, name=strategy_id, dna_json="{}",
            status=status, symbol="BTCUSDT", timeframe="4h",
            parent_ids=parent_ids, created_at=now, updated_at=now))


def _save_pass(db_path: Path, hypothesis_id: str, strategy_id: str,
               record_id: str) -> None:
    rr.save_validation_with_log(
        db_path, record_id=record_id, hypothesis_id=hypothesis_id,
        strategy_id=strategy_id, level="isolated_backtest", params="{}",
        result=None, verdict="pass", range_hash=f"rk_{record_id}")


def _promotion_logs(db_path: Path, hypothesis_id: str) -> list[dict]:
    return [e for e in rr.list_research_log(db_path, hypothesis_id=hypothesis_id)
            if e["entry_type"] == "promotion"]


class TestTransitionWithPromotion:
    """promote 组合函数四分支（调用方 hypothesis_service 的证据闸门地基）。"""

    def test_non_active_returns_zero_and_promotes_nothing(self, db_path: Path) -> None:
        """非 active（rows=0）时 promote 分支整体短路——无 promotion 日志。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        rr.transition_hypothesis_with_promotion(
            db_path, "hyp_1", target_status="refuted", terminal_ref="rec_1")

        out = rr.transition_hypothesis_with_promotion(
            db_path, "hyp_1", target_status="supported",
            terminal_ref="rec_2", promote=True)

        assert out["rows"] == 0 and out["promoted"] is False
        assert out["promoted_strategy_id"] is None and out["promotion_note"] is None
        assert _promotion_logs(db_path, "hyp_1") == [], "rows=0 路径不应落 promotion 日志"

    def test_promote_without_draft_logs_skip(self, db_path: Path) -> None:
        """谱系无 draft：假设照常定证，落 promotion 日志记跳过。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        out = rr.transition_hypothesis_with_promotion(
            db_path, "hyp_1", target_status="supported",
            terminal_ref="rec_1", promote=True)

        assert out["rows"] == 1 and out["promoted"] is False
        assert "谱系无 draft" in out["promotion_note"]
        logs = _promotion_logs(db_path, "hyp_1")
        assert len(logs) == 1 and logs[0]["ref_id"] == "rec_1"

    def test_draft_without_own_pass_kept_draft(self, db_path: Path) -> None:
        """draft 无自身 pass：保持 draft（证据绑定未满足），note 点名。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        _insert_strategy(db_path, "st_draft", parent_ids="hyp_1")

        out = rr.transition_hypothesis_with_promotion(
            db_path, "hyp_1", target_status="supported",
            terminal_ref="rec_1", promote=True)

        assert out["rows"] == 1 and out["promoted"] is False
        assert "st_draft" in out["promotion_note"] and "保持 draft" in out["promotion_note"]
        assert any("no own pass record" in e["summary"]
                   for e in _promotion_logs(db_path, "hyp_1"))

    def test_draft_with_own_pass_promoted_active(self, db_path: Path) -> None:
        """draft 挂自身 pass 记录：同事务转正（draft→active）。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        _insert_strategy(db_path, "st_draft", parent_ids="hyp_1")
        _save_pass(db_path, "hyp_1", "st_draft", "rec_pass")

        out = rr.transition_hypothesis_with_promotion(
            db_path, "hyp_1", target_status="supported",
            terminal_ref="rec_pass", promote=True)

        assert out["rows"] == 1 and out["promoted"] is True
        assert out["promoted_strategy_id"] == "st_draft"
        with _engine_for(db_path).connect() as conn:
            status = conn.execute(
                select(Strategy.status).where(
                    Strategy.strategy_id == "st_draft")).scalar_one()
        assert status == "active", "有自身 pass 的 draft 应转正为 active"
        assert any("promoted -> active" in e["summary"]
                   for e in _promotion_logs(db_path, "hyp_1"))

    def test_pass_on_other_strategy_does_not_promote(self, db_path: Path) -> None:
        """pass 挂在同假设的其他策略上（调包场景）：draft 不转正。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        _insert_strategy(db_path, "st_draft", parent_ids="hyp_1")
        _insert_strategy(db_path, "st_other", parent_ids="hyp_1", status="active")
        _save_pass(db_path, "hyp_1", "st_other", "rec_other")

        out = rr.transition_hypothesis_with_promotion(
            db_path, "hyp_1", target_status="supported",
            terminal_ref="rec_other", promote=True)

        assert out["promoted"] is False, "他人 pass 不得转正未验证的 draft"
        with _engine_for(db_path).connect() as conn:
            statuses = dict(conn.execute(
                select(Strategy.strategy_id, Strategy.status)).all())
        assert statuses["st_draft"] == "draft"

    def test_mixed_drafts_promote_only_evidenced(self, db_path: Path) -> None:
        """多 draft 混合：仅转正有自身 pass 的，其余保持并记日志。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        _insert_strategy(db_path, "st_a", parent_ids="hyp_1")
        _insert_strategy(db_path, "st_b", parent_ids="hyp_1")
        _save_pass(db_path, "hyp_1", "st_a", "rec_a")

        out = rr.transition_hypothesis_with_promotion(
            db_path, "hyp_1", target_status="supported",
            terminal_ref="rec_a", promote=True)

        assert out["promoted"] is True and out["promoted_strategy_id"] == "st_a"
        with _engine_for(db_path).connect() as conn:
            statuses = dict(conn.execute(
                select(Strategy.strategy_id, Strategy.status)).all())
        assert statuses["st_a"] == "active" and statuses["st_b"] == "draft"


class TestSaveHypothesisScreenUse:
    """screen_use 谱系分支：登记引用扫描候选时同事务追加第三条 insert。"""

    def test_screen_use_writes_lineage_log(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(
            db_path, **_hyp_kwargs(),
            screen_use={"ref_id": "ant_screen_1",
                        "data_ranges": '[{"symbol": "BTCUSDT", "timeframe": "1h", "start": "2024-01-01", "end": "2024-02-01"}]'})

        logs = rr.list_research_log(db_path, hypothesis_id="hyp_1")
        types = [e["entry_type"] for e in logs]
        assert types.count("register") == 1 and types.count("screen_use") == 1
        screen = next(e for e in logs if e["entry_type"] == "screen_use")
        assert screen["ref_id"] == "ant_screen_1"
        assert "2024-02-01" in screen["data_ranges"]


class TestListHypotheses:
    """status 过滤 + offset/limit 分页边界。"""

    @pytest.fixture(autouse=True)
    def _three_hypotheses(self, db_path: Path) -> None:
        for i in range(3):
            rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(
                hypothesis_id=f"hyp_{i}", rhash=f"rh_{i}"))
        rr.transition_hypothesis_with_promotion(
            db_path, "hyp_2", target_status="refuted", terminal_ref="rec_x")

    def test_status_filter(self, db_path: Path) -> None:
        active = rr.list_hypotheses(db_path, status="active")
        assert {h["hypothesis_id"] for h in active} == {"hyp_0", "hyp_1"}
        refuted = rr.list_hypotheses(db_path, status="refuted")
        assert {h["hypothesis_id"] for h in refuted} == {"hyp_2"}

    def test_offset_limit_slices(self, db_path: Path) -> None:
        page1 = rr.list_hypotheses(db_path, limit=2)
        assert len(page1) == 2
        page2 = rr.list_hypotheses(db_path, offset=2, limit=2)
        assert len(page2) == 1
        assert {h["hypothesis_id"] for h in page1 + page2} == {
            "hyp_0", "hyp_1", "hyp_2"}, "分页合响应覆盖全量且不重叠"

    def test_offset_beyond_end_returns_empty(self, db_path: Path) -> None:
        assert rr.list_hypotheses(db_path, offset=100, limit=10) == []

    def test_status_filter_combines_with_pagination(self, db_path: Path) -> None:
        one = rr.list_hypotheses(db_path, status="active", limit=1)
        assert len(one) == 1 and one[0]["status"] == "active"


class TestListResearchLog:
    """hypothesis_id / entry_type 双过滤组合 + 分页边界。"""

    @pytest.fixture(autouse=True)
    def _logs(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(chash="c", rhash="r1"))
        rr.save_hypothesis_with_log(
            db_path, **_hyp_kwargs(hypothesis_id="hyp_2", chash="c", rhash="r2"))
        rr.append_research_log(db_path, hypothesis_id="hyp_1",
                               entry_type="adhoc", summary="快照留痕")
        rr.append_research_log(db_path, hypothesis_id=None,
                               entry_type="screen", ref_id="ant_s",
                               summary="全局扫描")

    def test_entry_type_filter(self, db_path: Path) -> None:
        screens = rr.list_research_log(db_path, entry_type="screen")
        assert len(screens) == 1 and screens[0]["hypothesis_id"] is None

    def test_combined_filter(self, db_path: Path) -> None:
        adhoc = rr.list_research_log(db_path, hypothesis_id="hyp_1",
                                     entry_type="adhoc")
        assert len(adhoc) == 1 and adhoc[0]["summary"] == "快照留痕"
        empty = rr.list_research_log(db_path, hypothesis_id="hyp_2",
                                     entry_type="adhoc")
        assert empty == []

    def test_offset_limit_slices(self, db_path: Path) -> None:
        all_logs = rr.list_research_log(db_path)
        assert len(all_logs) == 4
        first2 = rr.list_research_log(db_path, limit=2)
        rest = rr.list_research_log(db_path, offset=2, limit=10)
        assert len(first2) == 2 and len(rest) == 2
        assert {e["log_id"] for e in first2 + rest} == {
            e["log_id"] for e in all_logs}, "分页窗口平铺无重叠"

    def test_offset_beyond_end_returns_empty(self, db_path: Path) -> None:
        assert rr.list_research_log(db_path, offset=100) == []


class TestListValidationRecords:
    """hypothesis_id / strategy_id 过滤组合 + 分页边界。"""

    @pytest.fixture(autouse=True)
    def _records(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(chash="c", rhash="r1"))
        rr.save_hypothesis_with_log(
            db_path, **_hyp_kwargs(hypothesis_id="hyp_2", chash="c", rhash="r2"))
        for i, (hyp, st) in enumerate([("hyp_1", "st_a"), ("hyp_1", "st_b"),
                                       ("hyp_2", "st_a")]):
            rr.save_validation_with_log(
                db_path, record_id=f"rec_{i}", hypothesis_id=hyp, strategy_id=st,
                level="isolated_backtest", params="{}", result=None,
                verdict="pass", range_hash=f"rk_{i}")

    def test_hypothesis_filter(self, db_path: Path) -> None:
        rows = rr.list_validation_records(db_path, hypothesis_id="hyp_1")
        assert {r["record_id"] for r in rows} == {"rec_0", "rec_1"}

    def test_strategy_filter(self, db_path: Path) -> None:
        rows = rr.list_validation_records(db_path, strategy_id="st_a")
        assert {r["record_id"] for r in rows} == {"rec_0", "rec_2"}

    def test_combined_filter(self, db_path: Path) -> None:
        rows = rr.list_validation_records(db_path, hypothesis_id="hyp_1",
                                          strategy_id="st_b")
        assert {r["record_id"] for r in rows} == {"rec_1"}

    def test_no_filter_returns_all(self, db_path: Path) -> None:
        assert len(rr.list_validation_records(db_path)) == 3

    def test_offset_limit_slices(self, db_path: Path) -> None:
        assert len(rr.list_validation_records(db_path, limit=2)) == 2
        assert len(rr.list_validation_records(db_path, offset=2, limit=5)) == 1
        assert rr.list_validation_records(db_path, offset=99) == []


class TestFindValidationIdentityNone:
    """strategy_id=None 的幂等键查询（adhoc 验证通道）。"""

    def test_none_strategy_matches_none_record(self, db_path: Path) -> None:
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs())
        rr.save_validation_with_log(
            db_path, record_id="rec_none", hypothesis_id="hyp_1",
            strategy_id=None, level="random_window", params="{}",
            result=None, verdict="inconclusive", range_hash="rk_none")

        found = rr.find_validation_by_identity(db_path, "hyp_1", None, "rk_none")
        assert found is not None and found["record_id"] == "rec_none"
        assert found["verdict"] == "inconclusive"
        # strategy_id 传值不命中 None 记录（幂等键三元组精确匹配）
        assert rr.find_validation_by_identity(db_path, "hyp_1", "st_a", "rk_none") is None


class TestSignalRegistryVisibility:
    """only_visible 过滤（strategy_visible=0 的下架列不可见）。"""

    def test_only_visible_excludes_hidden(self, db_path: Path) -> None:
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p1", name="vis", version=1,
            expression="zscore(close, 20)", output_columns='["vis_col"]',
            entry_id="e1", column_name="vis_col")
        with _engine_for(db_path).begin() as conn:
            conn.execute(insert(SignalRegistryEntry).values(
                entry_id="e2", source="formula", plugin_id="p2",
                column_name="hidden_col", version=1,
                strategy_visible=0, created_at="2026-10-01T00:00:00"))

        visible = rr.list_signal_registry_entries(db_path, only_visible=True)
        assert {r["column_name"] for r in visible} == {"vis_col"}
        assert {r["column_name"]
                for r in rr.list_signal_registry_entries(db_path)} == {
            "vis_col", "hidden_col"}, "缺省不过滤，全量可见"


class TestAnalysisTaskNoopAndBoundary:
    """update 空参 no-op 与 stale 回收的严格小于边界。"""

    def test_update_with_all_none_is_noop(self, db_path: Path) -> None:
        rr.save_analysis_task(db_path, task_id="ant_1",
                              template="t", params="{}")
        rr.update_analysis_task(db_path, task_id="ant_1", status="running")
        rr.update_analysis_task(db_path, task_id="ant_1")  # 全 None

        t = rr.get_analysis_task(db_path, "ant_1")
        assert t["status"] == "running" and t["finished_at"] is None

    def test_get_missing_returns_none(self, db_path: Path) -> None:
        assert rr.get_analysis_task(db_path, "ant_none") is None

    def test_stale_strictly_before_threshold(self, db_path: Path) -> None:
        """created_at < 阈值为严格小于：等值行不回收。"""
        with _engine_for(db_path).begin() as conn:
            for tid, created in [("ant_eq", "2026-06-01T00:00:00"),
                                 ("ant_after", "2026-07-01T00:00:00"),
                                 ("ant_done", "2026-05-01T00:00:00")]:
                status = "failed" if tid == "ant_done" else "running"
                conn.execute(insert(AnalysisTask).values(
                    task_id=tid, template="t", params="{}",
                    status=status, created_at=created))

        stale = rr.list_stale_running_tasks(
            db_path, created_before="2026-06-01T00:00:00")
        assert {t["task_id"] for t in stale} == set(), "等值与晚于阈值均不回收"

        stale2 = rr.list_stale_running_tasks(
            db_path, created_before="2026-08-01T00:00:00")
        assert {t["task_id"] for t in stale2} == {"ant_eq", "ant_after"}, \
            "仅早于阈值的 running 行回收，failed 终态不重复回收"


class TestFeatureRequestRepository:
    """CARD-06 收口资产：功能需求单 repository 三函数（路由层内联 SQL 迁入）。"""

    def _save(self, db_path: Path, rid: str, status: str = "open",
              created: str = "2026-10-01T00:00:00") -> None:
        rr.save_feature_request(db_path, request_id=rid, title=f"t-{rid}",
                                payload='{"k": 1}', status=status,
                                created_at=created)

    def test_save_list_count_roundtrip(self, db_path: Path) -> None:
        self._save(db_path, "fr_1")
        self._save(db_path, "fr_2", created="2026-10-01T01:00:00")

        rows = rr.list_feature_requests(db_path)
        assert [r["request_id"] for r in rows] == ["fr_1", "fr_2"], \
            "created_at 升序稳定排序"
        assert rows[0]["payload"] == '{"k": 1}' and rows[0]["status"] == "open"
        assert rr.count_feature_requests(db_path) == 2
        assert rr.count_feature_requests(db_path, status="closed") == 0

    def test_status_filter_and_pagination(self, db_path: Path) -> None:
        self._save(db_path, "fr_1", status="open", created="2026-10-01T00:00:00")
        self._save(db_path, "fr_2", status="closed", created="2026-10-01T01:00:00")
        self._save(db_path, "fr_3", status="open", created="2026-10-01T02:00:00")

        open_rows = rr.list_feature_requests(db_path, status="open")
        assert {r["request_id"] for r in open_rows} == {"fr_1", "fr_3"}
        assert rr.count_feature_requests(db_path, status="open") == 2, \
            "count 过滤口径与 list 一致"
        assert len(rr.list_feature_requests(db_path, limit=2)) == 2
        rest = rr.list_feature_requests(db_path, offset=2, limit=5)
        assert [r["request_id"] for r in rest] == ["fr_3"]
        assert rr.list_feature_requests(db_path, offset=99) == []


class TestListPluginsRepository:
    """CARD-06 收口资产：插件审计清单同构查询（source 分派 + name 过滤）。"""

    def test_source_dispatch_and_name_filter(self, db_path: Path) -> None:
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="pf_1", name="oi_mom",
            version=1, expression="zscore(close, 20)",
            output_columns='["oi_mom"]', entry_id="e_f", column_name="oi_mom")
        rr.save_plugin_with_registry(
            db_path, source="model", plugin_id="pm_1", name="roll_model",
            version=1, config="{}", signal_column="model_col",
            entry_id="e_m", column_name="model_col")

        formulas = rr.list_plugins(db_path, source="formula")
        assert {r["plugin_id"] for r in formulas} == {"pf_1"}
        assert formulas[0]["name"] == "oi_mom"
        models = rr.list_plugins(db_path, source="model")
        assert {r["plugin_id"] for r in models} == {"pm_1"}
        # name 精确过滤：命中同名族、未命中空集
        assert {r["plugin_id"]
                for r in rr.list_plugins(db_path, source="formula",
                                         name="oi_mom")} == {"pf_1"}
        assert rr.list_plugins(db_path, source="formula", name="nope") == []

    def test_version_audit_order(self, db_path: Path) -> None:
        """同名多版本按 version 升序（审计读序）。"""
        for v in (1, 2):
            rr.save_plugin_with_registry(
                db_path, source="formula", plugin_id=f"pf_v{v}", name="same",
                version=v, expression="zscore(close, 20)",
                output_columns='["same"]', entry_id=f"e_v{v}",
                column_name=f"same_v{v}")
        rows = rr.list_plugins(db_path, source="formula", name="same")
        assert [r["version"] for r in rows] == [1, 2]


class TestKeysetPaginationRepository:
    """CARD-07 修复资产：list_* 分页双轨 + count 配套（repository 直测）。

    feature_requests 的 created_at 外部可控，作 keyset 语义证明位；
    缺省 asc + offset/limit 原样（iter_all_pages 消费方契约不动）；
    cursor 严格比较（created_at 同值时主键决胜）。
    """

    def _save(self, db_path: Path, rid: str, created: str) -> None:
        rr.save_feature_request(db_path, request_id=rid, title=rid,
                                payload="{}", status="open",
                                created_at=created)

    @pytest.fixture(autouse=True)
    def _three_rows(self, db_path: Path) -> None:
        # fr_b/fr_c 同 created_at（主键决胜路径），fr_a 更早
        self._save(db_path, "fr_a", "2026-10-01T00:00:00")
        self._save(db_path, "fr_b", "2026-10-01T01:00:00")
        self._save(db_path, "fr_c", "2026-10-01T01:00:00")

    def test_default_asc_offset_limit_unchanged(self, db_path: Path) -> None:
        rows = rr.list_feature_requests(db_path)
        assert [r["request_id"] for r in rows] == ["fr_a", "fr_b", "fr_c"], \
            "缺省 asc 稳定排序（created_at 同值主键决胜）"
        assert [r["request_id"] for r in
                rr.list_feature_requests(db_path, offset=1, limit=1)] == ["fr_b"]

    def test_desc_order(self, db_path: Path) -> None:
        rows = rr.list_feature_requests(db_path, order="desc")
        assert [r["request_id"] for r in rows] == ["fr_c", "fr_b", "fr_a"]

    def test_desc_cursor_walk(self, db_path: Path) -> None:
        """desc 续页：cursor=(created_at, pk) 严格小于比较，无重无漏。"""
        page1 = rr.list_feature_requests(db_path, order="desc", limit=1)
        assert page1[0]["request_id"] == "fr_c"
        cur = (page1[0]["created_at"], page1[0]["request_id"])
        page2 = rr.list_feature_requests(db_path, order="desc",
                                         limit=2, cursor=cur)
        assert [r["request_id"] for r in page2] == ["fr_b", "fr_a"], \
            "同 created_at 时 pk 严格小于决胜（fr_c 后是 fr_b）"

    def test_asc_cursor_walk(self, db_path: Path) -> None:
        """asc 续页：cursor 严格大于比较。"""
        first = rr.list_feature_requests(db_path, limit=1)
        assert first[0]["request_id"] == "fr_a"
        cur = (first[0]["created_at"], first[0]["request_id"])
        rest = rr.list_feature_requests(db_path, limit=5, cursor=cur)
        assert {r["request_id"] for r in rest} == {"fr_b", "fr_c"}

    def test_count_filters_match_list(self, db_path: Path) -> None:
        """三 count 函数过滤口径与对应 list 一致。"""
        rr.save_hypothesis_with_log(db_path, **_hyp_kwargs(chash="c", rhash="r1"))
        rr.save_hypothesis_with_log(
            db_path, **_hyp_kwargs(hypothesis_id="hyp_2", chash="c", rhash="r2"))
        rr.transition_hypothesis_with_promotion(
            db_path, "hyp_2", target_status="refuted", terminal_ref="rec_x")
        rr.append_research_log(db_path, hypothesis_id="hyp_1",
                               entry_type="adhoc", summary="x")
        rr.save_validation_with_log(
            db_path, record_id="rec_1", hypothesis_id="hyp_1",
            strategy_id="st_a", level="isolated_backtest", params="{}",
            result=None, verdict="pass", range_hash="rk_1")

        assert rr.count_hypotheses(db_path) == 2
        assert rr.count_hypotheses(db_path, status="active") == 1, \
            "count 与 list_hypotheses 同过滤口径"
        assert rr.count_research_log(db_path, entry_type="adhoc") == 1
        assert rr.count_research_log(db_path, hypothesis_id="hyp_1") == 3, \
            "register + adhoc + validate（save_validation_with_log 双写日志）"
        assert rr.count_validation_records(
            db_path, hypothesis_id="hyp_1") == 1
        assert rr.count_validation_records(
            db_path, hypothesis_id="hyp_1", strategy_id="st_b") == 0
