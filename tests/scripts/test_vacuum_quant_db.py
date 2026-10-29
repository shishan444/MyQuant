"""VACUUM 脚本测试（VS-3.5——tmp 构造库四路径）。"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/vacuum_quant_db.py").resolve()


def _make_db(path: Path, *, with_free_pages: bool = True) -> None:
    """构造库：建表+写行；with_free_pages 时删半行数制造空闲页。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany(
        "INSERT INTO t (v) VALUES (?)",
        [(f"{'x' * 200}{i}",) for i in range(2000)])
    conn.commit()
    if with_free_pages:
        conn.execute("DELETE FROM t WHERE id % 2 = 0")
        conn.commit()
    conn.close()


def _run(db: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db)],
        capture_output=True, text=True, timeout=60)


@pytest.mark.integration
class TestVacuumScript:
    def test_success_path_exit_zero_and_frees_pages(self, tmp_path):
        """成功路径：退出码 0+freelist 显著回收+输出含前后对比与
        checkpoint 先于 VACUUM（输出序断言）。"""
        db = tmp_path / "q.db"
        _make_db(db)
        r = _run(db)
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout.index("[checkpoint]") < r.stdout.index("[结果]"), \
            "checkpoint 须先于 VACUUM 结果输出"
        assert "freelist_count" in r.stdout and "VACUUM 完成" in r.stdout
        conn = sqlite3.connect(db)
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        conn.close()
        assert free == 0, "VACUUM 后空闲页应归零"

    def test_occupied_path_exit_two(self, tmp_path):
        """占用路径：并发连接持锁 BEGIN IMMEDIATE 探测触发退出码 2。"""
        db = tmp_path / "q.db"
        _make_db(db)
        holder = sqlite3.connect(db, timeout=1.0)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t (v) VALUES ('lock')")
        try:
            r = _run(db)
            assert r.returncode == 2
            assert "占用" in r.stdout
        finally:
            holder.rollback()
            holder.close()

    def test_other_error_exit_one(self, tmp_path):
        """其他错误：非法路径（目录当库）退出码 1。"""
        notadb = tmp_path / "dir.db"
        notadb.mkdir()
        r = _run(notadb)
        assert r.returncode == 1

    def test_idempotent_rerun_exit_zero(self, tmp_path):
        """幂等重跑：第二次执行退出码 0 且空闲页近零。"""
        db = tmp_path / "q.db"
        _make_db(db)
        assert _run(db).returncode == 0
        r2 = _run(db)
        assert r2.returncode == 0, r2.stdout + r2.stderr
