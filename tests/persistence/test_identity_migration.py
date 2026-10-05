"""0.3.3 G7 alembic 迁移 d4e7f9a2c5b8：回填与索引形制独立测试。

从旧 head（b7c4e9a2f1d3）建库插存量 → upgrade head → 断言回填实值、
行数对账、双实现同输出（迁移内联副本 vs 服务层单点——同步锚）。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest


def _alembic(db: Path, *args: str) -> None:
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", *args],
        capture_output=True, text=True, check=True,
    )


@pytest.fixture()
def legacy_db(tmp_path: Path) -> Path:
    db = tmp_path / "legacy.db"
    _alembic(db, "upgrade", "b7c4e9a2f1d3")  # 旧 head（五元迁移前）
    return db


def _insert_hyp_and_staged(db: Path, segs: list, mode: str = "explicit",
                           rec_id: str = "rec_m", rh: str = "rh_m") -> str:
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR IGNORE INTO research_hypotheses (hypothesis_id, status,"
        " conditions, conditions_hash, statement, data_ranges,"
        " ranges_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("hyp_m", "active", "{}", "hash_m", "迁移测试", "[]",
         "rhash_m", "2026-10-05T00:00:00",
         "2026-10-05T00:00:00"))
    conn.execute(
        "INSERT INTO validation_records (record_id, hypothesis_id,"
        " strategy_id, level, params, result, verdict, range_hash,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (rec_id, "hyp_m", "st_m", "staged_validation",
         json.dumps({"segmentation": mode, "segments": segs}),
         "{}", "pass", rh, "2026-10-05T00:00:00"))
    conn.commit()
    conn.close()
    return "rec_m"


class TestIdentityMigration:
    def test_backfill_and_rowcount(self, legacy_db):
        """回填实值（explicit 现算摘要 / auto 字面量）+ 行数对账。"""
        import sqlite3
        segs = [{"label": "a", "start": "2026-01-01T00:00:00+00:00",
                 "end": "2026-01-05T00:00:00+00:00"}]
        _insert_hyp_and_staged(legacy_db, segs, mode="explicit")
        _insert_hyp_and_staged(legacy_db, [], mode="auto",
                               rec_id="rec_m2", rh="rh_m2")
        import sqlite3
        conn = sqlite3.connect(legacy_db)
        before = conn.execute(
            "SELECT COUNT(*) FROM validation_records").fetchone()[0]
        conn.close()

        _alembic(legacy_db, "upgrade", "head")

        conn = sqlite3.connect(legacy_db)
        after = conn.execute(
            "SELECT COUNT(*) FROM validation_records").fetchone()[0]
        assert after == before  # 行数对账（迁移零增删）
        rows = dict(conn.execute(
            "SELECT json_extract(params,'$.segmentation'), segmentation"
            " FROM validation_records").fetchall())
        conn.close()
        canon = json.dumps(
            sorted(segs, key=lambda s: (s["start"], s["end"], s["label"])),
            sort_keys=True, ensure_ascii=False)
        expect_explicit = hashlib.sha256(
            canon.encode()).hexdigest()[:16]
        assert rows["explicit"] == expect_explicit
        assert rows["auto"] == "auto"

    def test_dual_implementation_same_output(self):
        from importlib.util import module_from_spec, spec_from_file_location
        from core.analysis.validation_service import segmentation_identity
        mig = Path("alembic/versions/"
                   "d4e7f9a2c5b8_validation_identity_five_tuple.py")
        spec = spec_from_file_location("mig_mod", mig)
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        segs = [{"label": "段一", "start": "2026-01-01T00:00:00+00:00",
                 "end": "2026-01-05T00:00:00+00:00"},
                {"label": "b", "start": "2026-01-05T00:00:00+00:00",
                 "end": "2026-01-09T00:00:00+00:00"}]
        assert (mod._segmentation_identity_copy("explicit", segs)
                == segmentation_identity("explicit", segs))
        assert mod._segmentation_identity_copy("auto", segs) == "auto"
        assert mod._segmentation_identity_copy("isolated", None) == ""
