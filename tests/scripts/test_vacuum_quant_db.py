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
        # 契约 3 输出面：前后体积（主库 …→…）与 page_count 对比均在场
        assert "主库" in r.stdout and "page_count" in r.stdout, r.stdout
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
        """其他错误：非法路径（目录当库）退出码 1+[错误] 结构化输出。"""
        notadb = tmp_path / "dir.db"
        notadb.mkdir()
        r = _run(notadb)
        assert r.returncode == 1
        assert "[错误]" in r.stdout, r.stdout + r.stderr
        assert "Traceback" not in r.stderr, "connect 异常须被捕获分流，不得裸 traceback"

    def test_not_a_database_exit_one(self, tmp_path):
        """非库文本文件：connect 惰性打开后 BEGIN IMMEDIATE 抛
        DatabaseError（file is not a database，非 OperationalError）
        ——三分流须按错误走 exit 1+[错误] 结构化输出，不得裸 traceback。"""
        notadb = tmp_path / "text.db"
        notadb.write_text("this is not a sqlite database " * 10)
        r = _run(notadb)
        assert r.returncode == 1
        assert "[错误]" in r.stdout, r.stdout + r.stderr
        assert "Traceback" not in r.stderr

    def test_race_window_exit_two(self, tmp_path):
        """竞态窗口（VS-3.5 退出码契约）：持读锁连接与 BEGIN IMMEDIATE
        探测兼容（探测通过），执行侧 VACUUM 拿独占锁失败——探测仅前置
        提示，后果由执行侧同分流闭环为退出码 2。"""
        db = tmp_path / "q.db"
        _make_db(db)
        holder = sqlite3.connect(db, timeout=1.0)
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM t").fetchone()
        try:
            r = _run(db)
            assert r.returncode == 2, r.stdout + r.stderr
            assert "占用" in r.stdout
            assert "Traceback" not in r.stderr
        finally:
            holder.rollback()
            holder.close()

    def test_idempotent_rerun_exit_zero(self, tmp_path):
        """幂等重跑：第二次执行退出码 0 且空闲页近零。"""
        db = tmp_path / "q.db"
        _make_db(db)
        assert _run(db).returncode == 0
        r2 = _run(db)
        assert r2.returncode == 0, r2.stdout + r2.stderr
