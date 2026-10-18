"""repository/strategy.py 特征测试网（增量锚）。

test_orm_infra.py TestStrategyRepositoryEquivalence 已锚 active 去重
（高分替换/低分跳过）与 count_by_tasks；本文件补其余行为面：
- save 的 draft 免去重（M4：草稿无绩效，纯 insert，同签名不吞）；
- find_by_gene_signature（translate 同签名分流依据）；
- list/count 的 status 语义（None=默认视图排除 draft；显式值精确匹配）
  与过滤/排序校验；
- update 白名单与 delete 缺失静默；
- update_strategy_verify_stats 滚动统计（COALESCE/MAX 增量公式）。

锚点分类（V3 四分类）：B 类（行为契约锚）= draft 免去重/status
语义/白名单/删除静默；C 类 = verify_stats 滚动增量公式手算锚；
无 A/D 类。
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import strategy as repo


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "strategy.db"
    init_db_ext(path)
    return path


def _strategy(db, sid, *, signature=None, status="active", score=None, **kw):
    kw.setdefault("dna_json", '{"v": 1}')
    kw.setdefault("symbol", "BTCUSDT")
    kw.setdefault("timeframe", "4h")
    kw.setdefault("source", "manual")
    repo.save_strategy(db, strategy_id=sid, gene_signature=signature,
                       status=status, best_score=score, **kw)


class TestDraftExemptFromDedup:
    """draft 免去重（improve-mcp-agent-loop M4）。"""

    def test_同签名draft并存_不吞插入(self, db):
        _strategy(db, "s-active", signature="sig-1", score=80.0)
        _strategy(db, "s-draft", signature="sig-1", status="draft")
        both = repo.find_by_gene_signature(db, "sig-1")
        assert {r["strategy_id"] for r in both} == {"s-active", "s-draft"}, \
            "草稿纯 insert——同签名不被去重吞掉"

    def test_同签名active低分_仍被去重吞(self, db):
        _strategy(db, "s-keep", signature="sig-2", score=80.0)
        _strategy(db, "s-low", signature="sig-2", score=10.0)
        rows = repo.find_by_gene_signature(db, "sig-2")
        assert {r["strategy_id"] for r in rows} == {"s-keep"}, \
            "active 低分插入被去重语义忽略"


class TestFindByGeneSignature:
    """find_by_gene_signature：含 status 的全量同签名行。"""

    def test_命中多行与未命中(self, db):
        _strategy(db, "a", signature="sig-1")
        _strategy(db, "b", signature="sig-1", status="draft")
        _strategy(db, "c", signature="sig-2")
        assert [r["strategy_id"] for r in repo.find_by_gene_signature(db, "sig-1")] == \
            ["a", "b"], "不过滤 status（translate 分流自判）"
        assert repo.find_by_gene_signature(db, "nope") == []


class TestListCountStatusSemantics:
    """list/count：status=None 排除 draft；显式值精确匹配。"""

    @pytest.fixture(autouse=True)
    def _seed(self, db):
        _strategy(db, "act1", signature="s-1", score=50.0)
        _strategy(db, "act2", signature="s-2", score=70.0)
        _strategy(db, "drf1", signature="s-3", status="draft")

    def test_默认视图排除draft(self, db):
        ids = {r["strategy_id"] for r in repo.list_strategies(db)}
        assert ids == {"act1", "act2"}
        assert repo.count_strategies(db) == 2

    def test_显式status精确匹配(self, db):
        assert {r["strategy_id"] for r in repo.list_strategies(db, status="draft")} == \
            {"drf1"}
        assert repo.count_strategies(db, status="draft") == 1

    def test_过滤维度与排序校验(self, db):
        assert repo.count_strategies(db, symbol="BTCUSDT") == 2
        with pytest.raises(ValueError, match="sort_by"):
            repo.list_strategies(db, sort_by="evil; drop")
        with pytest.raises(ValueError, match="sort_order"):
            repo.list_strategies(db, sort_order="sideways")

    def test_score正序与limit(self, db):
        rows = repo.list_strategies(db, sort_by="best_score", sort_order="asc")
        assert [r["best_score"] for r in rows] == [50.0, 70.0]
        assert len(repo.list_strategies(db, limit=1)) == 1

    def test_空task_ids返回空dict(self, db):
        assert repo.count_strategies_by_tasks(db, []) == {}

    def test_qualified过滤(self, db):
        # save_strategy 不写 qualified 列（021 评分制度列）——直写构造
        import sqlite3
        _strategy(db, "q1", signature="s-q1")
        _strategy(db, "q0", signature="s-q0")
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE strategy SET qualified = 1 WHERE strategy_id = 'q1'")
        conn.execute("UPDATE strategy SET qualified = 0 WHERE strategy_id = 'q0'")
        conn.commit()
        conn.close()
        assert {r["strategy_id"] for r in repo.list_strategies(db, qualified=True)} == {"q1"}
        assert repo.count_strategies(db, qualified=False) == 1


class TestUpdateDelete:
    """update 白名单与 delete 缺失静默。"""

    def test_update白名单内生效(self, db):
        _strategy(db, "s1", signature="sig-1")
        repo.update_strategy(db, strategy_id="s1", name="renamed",
                             notes="ok", evil_column="x")
        row = repo.get_strategy(db, "s1")
        assert row["name"] == "renamed"
        assert row["notes"] == "ok"

    def test_全白名单外_noop(self, db):
        _strategy(db, "s1", signature="sig-1")
        repo.update_strategy(db, strategy_id="s1", evil_column="x")
        assert repo.get_strategy(db, "s1")["name"] is None

    def test_删除不存在无错误(self, db):
        repo.delete_strategy(db, "ghost")  # 不抛错即通过


class TestUpdateConnPassthrough:
    """update_strategy conn 可选透传（CARD-03，形制同 paper.create_task）：
    传入走外层事务（回滚不落库/提交落库）；白名单与 status 拒绝在
    透传路径同样生效。缺省自开单事务行为由 TestUpdateDelete 组承载。"""

    def test_透传外层回滚不落库(self, db):
        from core.persistence.orm.engine import engine_for

        _strategy(db, "s1", signature="sig-1")
        engine = engine_for(db)
        with engine.begin() as conn:
            repo.update_strategy(db, strategy_id="s1", name="in_tx",
                                conn=conn)
            conn.rollback()
        assert repo.get_strategy(db, "s1")["name"] is None, \
            "透传更新随外层回滚不落库"

    def test_透传提交落库(self, db):
        from core.persistence.orm.engine import engine_for

        _strategy(db, "s1", signature="sig-1")
        engine = engine_for(db)
        with engine.begin() as conn:
            repo.update_strategy(db, strategy_id="s1", name="committed",
                                conn=conn)
        assert repo.get_strategy(db, "s1")["name"] == "committed"

    def test_透传路径status键仍拒绝(self, db):
        from core.persistence.orm.engine import engine_for

        from core.strategy.lifecycle.machine import LifecycleError

        _strategy(db, "s1", signature="sig-1")
        engine = engine_for(db)
        with engine.begin() as conn:
            with pytest.raises(LifecycleError):
                repo.update_strategy(db, strategy_id="s1", status="active",
                                     conn=conn)


class TestVerifyStatsRolling:
    """update_strategy_verify_stats：COALESCE/MAX 增量滚动。"""

    def test_首轮None分支与次轮增量(self, db):
        _strategy(db, "s1", signature="sig-1")
        repo.update_strategy_verify_stats(
            db, "s1", new_score=80.0, verify_star=1,
            verified_at="2026-01-01T00:00:00+00:00")
        row = repo.get_strategy(db, "s1")
        assert row["verify_count"] == 1
        assert row["verify_avg_score"] == 80.0, "首轮 avg=NULL 分支取新分"
        assert row["verify_best_score"] == 80.0, "首轮 best=NULL 分支取新分"
        assert row["verify_star"] == 1
        assert row["last_verified_at"] == "2026-01-01T00:00:00+00:00"

        repo.update_strategy_verify_stats(
            db, "s1", new_score=60.0, verify_star=2,
            verified_at="2026-01-02T00:00:00+00:00")
        row = repo.get_strategy(db, "s1")
        assert row["verify_count"] == 2
        assert row["verify_avg_score"] == pytest.approx(70.0), "(80*1+60)/2"
        assert row["verify_best_score"] == 80.0, "max(80, 60) 保持"
        assert row["verify_star"] == 2, "star 直接覆写不滚动"

    def test_高分刷新best(self, db):
        _strategy(db, "s1", signature="sig-1")
        for score in (50.0, 90.0):
            repo.update_strategy_verify_stats(
                db, "s1", new_score=score, verify_star=1,
                verified_at="2026-01-01T00:00:00+00:00")
        assert repo.get_strategy(db, "s1")["verify_best_score"] == 90.0


class TestWriteSurfaceLockdown:
    """三写点收编·repository 侧两项（build-strategy-lifecycle 1.3）：
    save 初始态白名单（函数级 422 先于 DB 触发器——双层守卫层②面）
    +update 对 status 键显式拒绝（此前静默过滤，行为变化点：写面
    收敛后全库 status 唯一写点=生命周期迁移服务 CAS）。"""

    @pytest.mark.parametrize("bad", ["paper", "candidate", "live", "archived"])
    def test_save初始态白名单外结构化拒绝(self, db, bad):
        from core.strategy.lifecycle.machine import LifecycleError
        with pytest.raises(LifecycleError) as ei:
            repo.save_strategy(db, strategy_id="st_bad", dna_json="{}",
                               symbol="BTCUSDT", timeframe="4h", status=bad)
        assert ei.value.code == "STRATEGY_INITIAL_STATUS_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["status"] == bad
        assert sorted(ei.value.detail["allowed"]) == ["active", "draft"]

    def test_save白名单内两值放行(self, db):
        # 签名错开：同签名 active 行会被 save 去重吞（既有语义）
        _strategy(db, "st_d", status="draft", signature="sig-wl-d")
        _strategy(db, "st_a", status="active", signature="sig-wl-a")
        assert repo.get_strategy(db, "st_d")["status"] == "draft"
        assert repo.get_strategy(db, "st_a")["status"] == "active"

    def test_update传status键显式拒绝(self, db):
        from core.strategy.lifecycle.machine import LifecycleError
        _strategy(db, "st_lock")
        with pytest.raises(LifecycleError) as ei:
            repo.update_strategy(db, strategy_id="st_lock",
                                 status="archived", name="x")
        assert ei.value.code == "STRATEGY_STATUS_WRITE_FORBIDDEN"
        assert ei.value.status == 422
        # 拒绝非静默：整个调用失败，无部分写入（name 亦未落）
        row = repo.get_strategy(db, "st_lock")
        assert row["status"] == "active"
        assert row["name"] is None
