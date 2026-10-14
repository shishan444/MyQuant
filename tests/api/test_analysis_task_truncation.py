"""TD-01 轮询截断披露测试（变更三 8.x · P3 组）。

两态诚实区分：截断=工程已知上限（result_truncated+truncation_note，
result 尽力而为）；畸形=数据损坏（result_invalid）。三披露键均条件
出现，正常路径响应键集逐字节不变。

构造手段：直插 analysis_tasks 行（受控 result_ref 长度与截断标记）
经真实路由轮询——覆盖轮询分派逻辑全部分支。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.persistence.db_ext import init_db_ext


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    return d


def _client(db: Path, tmp_path: Path) -> TestClient:
    from api.app import create_app
    app = create_app(db_path=db, data_dir=tmp_path / "market")
    return TestClient(app)


def _insert_task(db: Path, task_id: str, *, result_ref, truncated,
                 status="succeeded"):
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO analysis_tasks (task_id, template, params, "
            "status, reason, result_ref, result_truncated, created_at) "
            "VALUES (?, 't', '{}', ?, NULL, ?, ?, '2026-10-14T00:00:00Z')",
            (task_id, status, result_ref, truncated))


def test_p3_t01_truncated_disclosed(db, tmp_path):
    """P3-T01 验收锚：>20 万字符截断任务轮询 → result_truncated:true+
    truncation_note 文案；result 尽力而为（截断点恰逢非法边界时不附
    但不静默）。"""
    # 截断点落在字符串中间 → json.loads 必失败（result 不附）
    big = json.dumps({"k": "x" * 250_000})
    assert len(big) > 200_000
    _insert_task(db, "ant_trunc", result_ref=big[:200_000], truncated=1)
    with _client(db, tmp_path) as c:
        r = c.get("/api/research/analysis/tasks/ant_trunc")
    assert r.status_code == 200
    body = r.json()
    assert body["result_truncated"] is True
    assert "20 万字符" in body["truncation_note"]
    assert "缩小请求区间" in body["truncation_note"]
    assert "result" not in body, "截断点非法边界时 result 不附（不静默——有披露键）"
    assert "result_invalid" not in body, "截断态不进 invalid 判定"


def test_p3_t02_exact_limit_not_truncated(db, tmp_path):
    """P3-T02 边界：恰 200000 字符不截断（严格大于语义——写入侧判定，
    轮询侧以标记为准：truncated=0+完整可解析）。"""
    payload = '{"k": "' + "y" * (200_000 - 9) + '"}'  # 包装恰 9 字符
    assert len(payload) == 200_000
    _insert_task(db, "ant_exact", result_ref=payload, truncated=0)
    with _client(db, tmp_path) as c:
        r = c.get("/api/research/analysis/tasks/ant_exact")
    body = r.json()
    assert body["result"] == json.loads(payload)
    assert "result_truncated" not in body and "truncation_note" not in body


def test_p3_t03_normal_path_keyset_unchanged(db, tmp_path):
    """P3-T03 回归：正常任务（未截断可解析）响应键集与现状逐字节一致
    ——无任何新增键（result_truncated 列值 0 不出现在响应）。"""
    _insert_task(db, "ant_ok", result_ref='{"a": 1}', truncated=0)
    with _client(db, tmp_path) as c:
        r = c.get("/api/research/analysis/tasks/ant_ok")
    body = r.json()
    assert set(body) == {"task_id", "template", "params", "status",
                         "reason", "result_ref", "created_at",
                         "finished_at", "result"}
    assert body["result"] == {"a": 1}


def test_p3_t04_legacy_corrupt_result_invalid(db, tmp_path):
    """P3-T04：畸形存量（truncated=0+损坏 result_ref）→ result_invalid:
    true（区分截断态，不静默）。"""
    _insert_task(db, "ant_dirty", result_ref="{corrupt", truncated=0)
    with _client(db, tmp_path) as c:
        r = c.get("/api/research/analysis/tasks/ant_dirty")
    body = r.json()
    assert body["result_invalid"] is True
    assert "result" not in body
    assert "result_truncated" not in body


def test_p3_t05_truncated_parseable_result_best_effort(db, tmp_path):
    """P3-T05 补充：截断点恰逢合法 JSON 边界 → result 附（尽力而为
    保留）+截断披露键同时在。"""
    # 构造 len>200000 且前 200000 恰为合法 JSON：前段完整对象+尾段填充
    inner = json.dumps({"k": "z" * 199_990})  # 恰可解析的完整前段
    assert len(inner) <= 200_000
    big = inner + " " * (200_001 - len(inner)) + "tail"
    _insert_task(db, "ant_half", result_ref=big[:200_000], truncated=1)
    with _client(db, tmp_path) as c:
        r = c.get("/api/research/analysis/tasks/ant_half")
    body = r.json()
    assert body["result_truncated"] is True
    assert body["result"] == json.loads(inner), "合法边界时 result 尽力保留"


def test_p3_t06_schema_column_verified():
    """P3-T06 schema：PRAGMA 验 analysis_tasks.result_truncated 列
    （server_default 0+NOT NULL——在经迁移升级的存量库上验列）。"""
    import subprocess
    import tempfile
    db = Path(tempfile.mkdtemp()) / "mig.db"
    r = subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}",
         "upgrade", "head"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]
    with sqlite3.connect(str(db)) as conn:
        cols = {row[1]: (row[4], row[3]) for row in
                conn.execute("PRAGMA table_info(analysis_tasks)")}
    assert "result_truncated" in cols, cols.keys()
    default, notnull = cols["result_truncated"]
    assert default in ("0", "'0'") and notnull == 1


def test_p3_t07_pending_task_no_result_keys(db, tmp_path):
    """P3-T07 补充：非终态任务（pending/running 无 result_ref）轮询
    键集不含任何披露键；MCP get_analysis_task 为 REST 透传零改动
    （tests/mcpserver 既有覆盖）。"""
    _insert_task(db, "ant_pend", result_ref=None, truncated=0,
                 status="pending")
    with _client(db, tmp_path) as c:
        r = c.get("/api/research/analysis/tasks/ant_pend")
    body = r.json()
    assert body["status"] == "pending"
    assert "result" not in body and "result_truncated" not in body
    assert "result_invalid" not in body and "truncation_note" not in body
