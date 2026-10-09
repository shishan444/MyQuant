"""B3a.1 ORM 基建测试：模型导入、engine/session 工厂、模型↔存量轨对照。

模型↔legacy PRAGMA 全列对照是 3.2 Alembic baseline 的前置守护——
两侧任何一方的 schema 漂移都在这里红。
"""

import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]


class TestModelMetadata:
    """模型全量声明：11 legacy 表 + 8 研究域表（add-ai-research-mcp 1.2），导入无误。"""

    def test_table_count(self):
        from core.persistence.orm import Base
        assert len(Base.metadata.tables) == 19

    def test_expected_tables_present(self):
        from core.persistence.orm import Base
        expected = {
            "backtest_result", "dataset_meta", "evolution_history",
            "evolution_task", "generation_snapshot", "live_trading_task",
            "paper_equity_snapshot", "paper_trade", "paper_trading_task",
            "strategy", "verify_session",
            # 研究域（add-ai-research-mcp）
            "research_hypotheses", "research_log", "analysis_tasks",
            "formula_plugins", "model_plugins", "signal_registry",
            "feature_requests", "validation_records",
        }
        assert expected == set(Base.metadata.tables)

    def test_secondary_indexes(self):
        from core.persistence.orm import Base
        idx = {(t.name, i.name) for t in Base.metadata.sorted_tables for i in t.indexes}
        expected = {
            ("paper_equity_snapshot", "idx_equity_task_time"),
            ("paper_trade", "idx_paper_trade_task"),
            ("paper_trade", "idx_paper_trade_unique"),  # B1.3 幂等唯一索引
            ("strategy", "idx_strategy_best_score"),
            ("strategy", "idx_strategy_gene_signature"),
            ("strategy", "idx_strategy_source"),
            ("strategy", "idx_strategy_source_task_id"),
            ("verify_session", "idx_verify_session_created"),
            # 研究域（add-ai-research-mcp 1.2；幂等键见 core/analysis/DESIGN.md 设计卡）
            ("research_hypotheses", "ux_hypotheses_active_identity"),
            ("research_log", "ix_research_log_hypothesis"),
            ("analysis_tasks", "ix_analysis_tasks_status"),
            ("formula_plugins", "ux_formula_plugins_name_version"),
            ("model_plugins", "ux_model_plugins_name_version"),
            ("signal_registry", "ux_signal_registry_column_version"),
            ("feature_requests", "ix_feature_requests_status"),
            ("validation_records", "ux_validation_idem"),
            ("validation_records", "ix_validation_hypothesis"),
        }
        assert expected == idx

    def test_paper_trade_unique_constraint(self):
        from core.persistence.orm import Base
        t = Base.metadata.tables["paper_trade"]
        uq = [i for i in t.indexes if i.name == "idx_paper_trade_unique"]
        assert uq and uq[0].unique
        assert [c.name for c in uq[0].columns] == ["task_id", "bar_time", "action"]


class TestEngineFactory:
    """engine/session 工厂：连接配置统一（WAL + busy_timeout + FK）。"""

    def test_pragma_applied(self, tmp_path: Path):
        from core.persistence.orm import create_db_engine

        engine = create_db_engine(tmp_path / "f.db")
        try:
            with engine.connect() as conn:
                jm = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
                bt = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
                fk = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
            assert jm == "wal"
            assert bt == 5000
            assert fk == 0, "FK 强制保持旧轨默认 OFF（行为等价，见 engine.py 注记）"
        finally:
            engine.dispose()

    def test_session_factory_roundtrip(self, tmp_path: Path):
        from sqlalchemy import insert, select

        from core.persistence.orm import Base, create_db_engine, create_session_factory
        from core.persistence.orm.models import DatasetMeta

        engine = create_db_engine(tmp_path / "s.db")
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        try:
            with factory() as session:
                session.execute(insert(DatasetMeta).values(
                    dataset_id="d1", symbol="BTCUSDT", interval="4h",
                    parquet_path="/tmp/x.parquet", created_at="t", updated_at="t",
                ))
                session.commit()
                row = session.execute(
                    select(DatasetMeta.dataset_id, DatasetMeta.row_count)
                ).one()
            # server_default 生效 + expire_on_commit=False 提交后可读
            assert row == ("d1", 0)
        finally:
            engine.dispose()


def _read_schema(db: Path):
    conn = sqlite3.connect(str(db))
    out = {}
    for (t,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name != 'schema_version' ORDER BY name"
    ):
        out[t] = {c[1]: (c[2], c[3], c[4], c[5]) for c in conn.execute(f"PRAGMA table_info({t})")}
    conn.close()
    return out


def _legacy_schema(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext

    db = tmp_path / "legacy.db"
    init_db_ext(db)  # B3c.1 起 init 链含 live 表
    return _read_schema(db)


class TestModelMatchesLegacySchema:
    """ORM 元数据 DDL 与 legacy 轨（init_db_ext + live 表）全列对照。

    3.2 baseline 前置守护：列名/类型/default/notnull 全等；PK 列的
    notnull 差异豁免（SQLite TEXT PRIMARY KEY 怪癖：legacy 显示 0，
    ORM 渲染 NOT NULL——语义等价且更严格）。
    """

    def _orm_schema(self, tmp_path: Path):
        from sqlalchemy import create_engine

        from core.persistence.orm import Base
        db = tmp_path / "orm.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        engine.dispose()
        return _read_schema(db)

    def test_all_columns_identical(self, tmp_path: Path):
        a = self._orm_schema(tmp_path)
        b = _legacy_schema(tmp_path)
        # TD-10 换芯：init_db_ext 产物含 alembic_version（版本记账表，
        # 非 ORM 业务模型）——对照两侧排除迁移记账表
        b.pop("alembic_version", None)
        assert set(a) == set(b), f"表集差异: {set(a) ^ set(b)}"
        for t in sorted(a):
            assert set(a[t]) == set(b[t]), f"{t} 列集差异"
            for col in sorted(a[t]):
                oa, lb = a[t][col], b[t][col]
                if oa != lb:
                    if lb[3] == 1 and (oa[0], oa[2], oa[3]) == (lb[0], lb[2], lb[3]):
                        continue  # PK-notnull 怪癖豁免
                    pytest.fail(f"{t}.{col}: ORM={oa} legacy={lb}")


@pytest.mark.integration
class TestAlembicBaseline:
    """3.2 Alembic 接管：upgrade 建库/接管流程/幂等三验证。

    三轨归一的落点：Alembic 版本链为唯一演进轨（SQL 轨 005/006 标记
    文档性废弃、Python ALTER 轨冻结不再新增、live 表经模型纳入 baseline）。
    """

    @staticmethod
    def _alembic(*args: str, db_path: Path):
        import subprocess
        return subprocess.run(
            ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db_path}", *args],
            capture_output=True, text=True, check=True,
        )

    def test_upgrade_head_matches_legacy_schema(self, tmp_path: Path):
        """Alembic 新库 upgrade head 的业务表 schema 与 init_db_ext 轨全等
        （TD-10 换芯后 init_db_ext=upgrade head，两侧对称排除迁移记账表）。"""
        up = tmp_path / "up.db"
        self._alembic("upgrade", "head", db_path=up)
        legacy = _legacy_schema(tmp_path)
        legacy.pop("alembic_version", None)
        conn = sqlite3.connect(str(up))
        got = {}
        for (t,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT IN ('schema_version', 'alembic_version')"
        ):
            got[t] = {c[1]: (c[2], c[3], c[4], c[5]) for c in conn.execute(f"PRAGMA table_info({t})")}
        conn.close()
        assert set(got) == set(legacy)
        for t in sorted(legacy):
            assert set(got[t]) == set(legacy[t]), f"{t} 列集差异"
            for col in legacy[t]:
                ga, lb = got[t][col], legacy[t][col]
                if ga != lb:
                    if lb[3] == 1 and (ga[0], ga[2], ga[3]) == (lb[0], lb[2], lb[3]):
                        continue
                    pytest.fail(f"{t}.{col}: alembic={ga} legacy={lb}")

    def test_legacy_terminal_db_stamp_upgrade_idempotent(self, tmp_path: Path):
        """TD-10 换芯语义锚：无 alembic_version 的 legacy 终态形库
        （b7c4e9a2f1d3 形——生产存量库形态）→ init_db_ext → stamp 至
        legacy 终态 + upgrade head（真实跑后续迁移补列/索引）→ 数据
        保全、二次执行幂等（版本表短路）。"""
        from core.persistence.db_ext import init_db_ext

        db = tmp_path / "legacy_terminal.db"
        self._alembic("upgrade", "b7c4e9a2f1d3", db_path=db)  # legacy 终态形
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE alembic_version")     # 模拟 legacy 轨产物（无版本表）
        conn.execute(
            "INSERT INTO strategy (strategy_id, dna_json, symbol, timeframe, "
            "gene_signature, source, created_at, updated_at) VALUES "
            "('keepme', '{}', 'BTCUSDT', '4h', 'sig', 'manual', "
            "'2024-01-01', '2024-01-01')")
        conn.commit()
        row_before = conn.execute("SELECT COUNT(*) FROM strategy").fetchone()[0]
        conn.close()

        init_db_ext(db)   # stamp b7c4e9a2f1d3 → upgrade head

        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT COUNT(*) FROM strategy").fetchone()[0] == row_before, \
            "存量数据零损失"
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        seg = conn.execute(
            "SELECT COUNT(*) FROM pragma_table_info('validation_records') "
            "WHERE name='segmentation'").fetchone()[0]
        conn.close()
        assert len(ver) == 1
        assert seg == 1, "后续迁移（G7 五元 segmentation）真实补齐"
        # 终态业务表 schema == 干净 upgrade head 库
        fresh = tmp_path / "fresh.db"
        self._alembic("upgrade", "head", db_path=fresh)
        TestDatasetMetaRepository._assert_business_schema_equal(db, fresh)

        init_db_ext(db)   # 二次：已在轨幂等
        conn = sqlite3.connect(str(db))
        assert conn.execute("SELECT COUNT(*) FROM strategy").fetchone()[0] == row_before
        conn.close()

    def test_init_db_product_mixed_init_db_ext_no_column_clash(self, tmp_path: Path):
        """TD-10 边界①修复锚（d069c7d）：init_db（ORM create_all）产物
        为 head 全列形但无 alembic_version——stamp 分支的 head 形探测
        使其直接 stamp head 短路，不再撞 duplicate column；数据保全、
        二次幂等。"""
        from core.persistence.db import init_db, save_task, get_task
        from core.persistence.db_ext import init_db_ext
        from core.strategy.dna import StrategyDNA

        db = tmp_path / "mixed.db"
        init_db(db)   # ORM create_all：head 全列形、无 alembic_version
        save_task(db, "mix-001", 80.0, "profit_first", "BTCUSDT", "4h",
                  StrategyDNA())

        init_db_ext(db)   # 修复前：stamp b7c4e9a2f1d3+upgrade 撞列

        assert get_task(db, "mix-001") is not None, "混用路径数据保全"
        conn = sqlite3.connect(str(db))
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        conn.close()
        assert len(ver) == 1

        init_db_ext(db)   # 二次幂等
        assert get_task(db, "mix-001") is not None

    def test_adopt_legacy_db_stamps_head(self, tmp_path: Path):
        """接管流程：legacy 库（无 alembic_version）→ stamp head，schema 不动。"""
        from core.persistence.db_ext import init_db_ext

        db = tmp_path / "adopt.db"
        init_db_ext(db)
        before = _read_schema(db)

        from scripts.adopt_alembic_baseline import adopt_legacy_db
        adopt_legacy_db(db)

        conn = sqlite3.connect(str(db))
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        conn.close()
        assert ver, "接管后应有 alembic_version"
        after = _read_schema(db)
        # 业务 schema 不动（stamp 只写版本表）
        for t in before:
            assert before[t] == after.get(t), f"{t} 被 stamp 改动"

    def test_upgrade_then_autogenerate_is_empty(self, tmp_path: Path):
        """再生成幂等：upgrade 后的库 autogenerate 应零变更（无文件或空迁移体）。"""
        up = tmp_path / "idem.db"
        self._alembic("upgrade", "head", db_path=up)
        self._alembic("revision", "--autogenerate", "-m", "idempotence-check", db_path=up)
        import glob
        new = [f for f in glob.glob("alembic/versions/*idempotence*check*")]
        try:
            if not new:
                return  # 零变更时 alembic 不落文件——更强的幂等信号
            import re
            body = Path(new[0]).read_text()
            upgrade_body = body.split("def upgrade")[1].split("def downgrade")[0]
            assert not re.search(
                r"\b(create_table|drop_table|add_column|drop_column|"
                r"create_index|drop_index|alter)\b",
                upgrade_body,
            ), f"非空迁移（schema 漂移）: {upgrade_body[:200]}"
            assert "pass" in upgrade_body
        finally:
            for f in new:
                Path(f).unlink()


class TestDatasetMetaRepository:
    """3.3 试点域：repository ORM 实现与旧轨行为等价。"""

    def test_crud_roundtrip(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import dataset_meta as repo

        db = tmp_path / "repo.db"
        init_db_ext(db)
        repo.save_dataset_meta(
            db, dataset_id="d1", symbol="BTCUSDT", interval="4h",
            parquet_path="/tmp/a.parquet", row_count=100,
            quality_status="ok",
        )
        row = repo.get_dataset(db, "d1")
        assert row is not None
        assert row["symbol"] == "BTCUSDT"
        assert row["row_count"] == 100
        assert row["quality_status"] == "ok"

        repo.update_dataset_stats(db, dataset_id="d1", row_count=200, symbol="ETHUSDT")
        row = repo.get_dataset(db, "d1")
        assert row["row_count"] == 200
        assert row["symbol"] == "BTCUSDT", "白名单外字段不得被更新"
        assert row["updated_at"] >= row["created_at"]

        assert repo.get_dataset(db, "nope") is None
        # 全白名单外字段 no-op（不写库不刷 updated_at）
        repo.update_dataset_stats(db, dataset_id="d1", evil_column="x")
        assert repo.get_dataset(db, "d1")["row_count"] == 200, "白名单外更新零效果"
        repo.delete_dataset(db, "d1")
        assert repo.get_dataset(db, "d1") is None
        repo.delete_dataset(db, "d1"), "删除不存在无错误"

    def test_list_filter_and_order(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import dataset_meta as repo

        db = tmp_path / "repo.db"
        init_db_ext(db)
        for i, (sym, itv) in enumerate([("BTCUSDT", "4h"), ("ETHUSDT", "1h"), ("BTCUSDT", "1h")]):
            repo.save_dataset_meta(
                db, dataset_id=f"d{i}", symbol=sym, interval=itv,
                parquet_path=f"/tmp/{i}.parquet",
            )
        all_rows = repo.list_datasets(db)
        assert len(all_rows) == 3
        assert {r["dataset_id"] for r in repo.list_datasets(db, symbol="BTCUSDT")} == {"d0", "d2"}
        assert [r["dataset_id"] for r in repo.list_datasets(db, symbol="BTCUSDT", interval="1h")] == ["d2"]
        assert len(repo.list_datasets(db, limit=2)) == 2

    def test_duplicate_insert_raises_like_legacy(self, tmp_path: Path):
        """裸 INSERT 语义：重复主键 IntegrityError（与旧轨一致）。"""
        import sqlalchemy.exc

        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import dataset_meta as repo

        db = tmp_path / "repo.db"
        init_db_ext(db)
        repo.save_dataset_meta(db, dataset_id="dup", symbol="X", interval="4h", parquet_path="p")
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            repo.save_dataset_meta(db, dataset_id="dup", symbol="X", interval="4h", parquet_path="p")

    def test_adoption_path_on_copy_of_production_db(self, tmp_path: Path):
        """副本库迁移路径演练（TD-10 后新语义）：存量数据 → adopt →
        真实 stamp+upgrade 至 head（segmentation 五元列/equity 唯一
        索引等随链补齐）——数据行数保全、终态 schema 与干净 upgrade
        head 库全等。"""
        import shutil

        src = Path("data/quant.db")
        if not src.exists():
            pytest.skip("无生产库可演练")
        db = tmp_path / "copy.db"
        shutil.copy(src, db)
        conn = sqlite3.connect(str(db))
        counts_before = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for (t,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT IN "
                "('alembic_version', 'schema_version')")
        }
        conn.close()

        from scripts.adopt_alembic_baseline import adopt_legacy_db
        adopt_legacy_db(db)

        conn = sqlite3.connect(str(db))
        counts_after = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for (t,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT IN "
                "('alembic_version', 'schema_version')")
        }
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        conn.close()
        assert len(ver) == 1, "接管后版本表就位"
        assert counts_before == counts_after, "全部业务表行数零损失"
        # 终态业务表 schema == 干净 upgrade head 库（对照组）
        fresh = tmp_path / "fresh.db"
        TestAlembicBaseline._alembic("upgrade", "head", db_path=fresh)
        self._assert_business_schema_equal(db, fresh)

    @staticmethod
    def _assert_business_schema_equal(a: Path, b: Path) -> None:
        """接管库（a）对干净 upgrade head 库（b）的 schema 包含对照：
        表集相等；列集为包含关系（b ⊆ a——生产库允许历史宽列，
        如 evolution_task.n_workers；Alembic 链不删既有列）；共有列
        定义全等（PK-notnull 怪癖豁免）。"""
        def _schema(path: Path):
            conn = sqlite3.connect(str(path))
            out = {}
            for (t,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT IN "
                "('alembic_version', 'schema_version')"
            ):
                out[t] = {c[1]: (c[2], c[3], c[4], c[5])
                          for c in conn.execute(f"PRAGMA table_info({t})")}
            conn.close()
            return out
        sa, sb = _schema(a), _schema(b)
        assert set(sa) == set(sb), f"表集差异: {set(sa) ^ set(sb)}"
        # 历史宽差豁免清单（TD-10 换芯已知差异，均已上报）：
        # - PK-notnull 怪癖：legacy 产物 PRAGMA 显示 0、链库渲染 1
        #   （SQLite TEXT/INTEGER PRIMARY KEY——仅声明位差异）
        # - max_drawdown_limit default：legacy ALTER 产物无 DEFAULT、
        #   链定义 '0.10'——行为差（接管库新行 NULL vs 链库 0.10）。
        #   已裁留档不修（TD-10 边界②）：消费方全链 NULL 容忍实证
        #   （evaluation.py:63 `or 0.30` 缺省 / scorer.py:253/263/267
        #   显式 None 分支）——改存量库 DDL 超本战役范围
        _DEFAULT_DIFF = {"evolution_task.max_drawdown_limit"}
        for t in sorted(sb):
            missing = set(sb[t]) - set(sa.get(t, {}))
            assert not missing, f"{t} 缺列（链库有而接管库无）: {sorted(missing)}"
            for col in sorted(sb[t]):
                va, vb = sa[t][col], sb[t][col]
                if va == vb:
                    continue
                if (vb[3] == 1 and va[3] == 1
                        and (va[0], va[2]) == (vb[0], vb[2])):
                    continue  # PK-notnull 怪癖（pk 位=1，仅 notnull 声明位差）
                if f"{t}.{col}" in _DEFAULT_DIFF and va[3] == vb[3] == 0 \
                        and va[0] == vb[0] and va[2] is None:
                    continue  # 历史列 default 声明差（已上报）
                pytest.fail(f"{t}.{col}: 接管库={va} 干净库={vb}")


class TestStrategyRepositoryEquivalence:
    """B3b.1 行为等价锚定：gene_signature 去重是 strategy 域最易漂移的语义。"""

    def _save(self, db, sid, sig, score, symbol="BTCUSDT"):
        from core.persistence.repository import strategy as repo
        repo.save_strategy(
            db, strategy_id=sid, dna_json="{}", symbol=symbol,
            timeframe="4h", best_score=score, gene_signature=sig,
        )

    def test_dedup_higher_score_replaces(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import strategy as repo

        db = tmp_path / "s.db"
        init_db_ext(db)
        self._save(db, "s-old", "SIG-A", 50.0)
        self._save(db, "s-new", "SIG-A", 80.0)  # 高分替换

        rows = repo.list_strategies(db)
        assert len(rows) == 1, "同签名只保留一条"
        assert rows[0]["strategy_id"] == "s-new"
        assert rows[0]["best_score"] == 80.0

    def test_dedup_lower_score_skipped(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import strategy as repo

        db = tmp_path / "s.db"
        init_db_ext(db)
        self._save(db, "s-old", "SIG-A", 80.0)
        self._save(db, "s-new", "SIG-A", 50.0)  # 低分不替换

        rows = repo.list_strategies(db)
        assert len(rows) == 1
        assert rows[0]["strategy_id"] == "s-old"

    def test_count_by_tasks_zero_fills_and_filters(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import strategy as repo

        db = tmp_path / "s.db"
        init_db_ext(db)
        self._save(db, "s1", None, 90.0)  # 无签名不参与去重
        repo.update_strategy(db, strategy_id="s1", source="evolution", source_task_id="t1")
        repo.update_strategy(db, strategy_id="s1", best_score=90.0)

        got = repo.count_strategies_by_tasks(db, ["t1", "t2"], min_score=80)
        assert got == {"t1": 1, "t2": 0}, "缺失任务补 0，低于 min_score 不计"
