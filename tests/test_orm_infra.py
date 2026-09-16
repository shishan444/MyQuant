"""B3a.1 ORM 基建测试：模型导入、engine/session 工厂、模型↔存量轨对照。

模型↔legacy PRAGMA 全列对照是 3.2 Alembic baseline 的前置守护——
两侧任何一方的 schema 漂移都在这里红。
"""

import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]


class TestModelMetadata:
    """模型全量声明：11 表 + 7 二级索引，导入无误。"""

    def test_table_count(self):
        from core.persistence.orm import Base
        assert len(Base.metadata.tables) == 11

    def test_expected_tables_present(self):
        from core.persistence.orm import Base
        expected = {
            "backtest_result", "dataset_meta", "evolution_history",
            "evolution_task", "generation_snapshot", "live_trading_task",
            "paper_equity_snapshot", "paper_trade", "paper_trading_task",
            "strategy", "verify_session",
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
            assert fk == 1
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
    from core.live_trading.persistence import init_live_trading_tables
    from core.persistence.db_ext import init_db_ext

    db = tmp_path / "legacy.db"
    init_db_ext(db)
    init_live_trading_tables(db)
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
        """Alembic 新库 upgrade head 的业务表 schema 与 legacy 轨全等。"""
        up = tmp_path / "up.db"
        self._alembic("upgrade", "head", db_path=up)
        legacy = _legacy_schema(tmp_path)
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

    def test_adopt_legacy_db_stamps_head(self, tmp_path: Path):
        """接管流程：legacy 库（无 alembic_version）→ stamp head，schema 不动。"""
        from core.live_trading.persistence import init_live_trading_tables
        from core.persistence.db_ext import init_db_ext

        db = tmp_path / "adopt.db"
        init_db_ext(db)
        init_live_trading_tables(db)
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
        """副本库迁移路径演练：存量数据 → adopt → repository 读写共存无损失。"""
        import shutil

        src = Path("data/quant.db")
        if not src.exists():
            pytest.skip("无生产库可演练")
        db = tmp_path / "copy.db"
        shutil.copy(src, db)
        before = _read_schema(db)

        from scripts.adopt_alembic_baseline import adopt_legacy_db
        adopt_legacy_db(db)

        # 存量数据经 repository 可读（与旧轨读到的行一致）
        from core.persistence import db_ext
        from core.persistence.repository import dataset_meta as repo
        legacy_rows = db_ext.list_datasets(db)
        repo_rows = repo.list_datasets(db)
        assert len(legacy_rows) == len(repo_rows)
        assert {r["dataset_id"] for r in repo_rows} == {r["dataset_id"] for r in legacy_rows}
        # 业务 schema 不动
        after = _read_schema(db)
        for t in before:
            if t == "alembic_version":
                continue
            assert before[t] == after.get(t), f"{t} 被 adopt 改动"
