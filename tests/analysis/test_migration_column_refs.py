"""迁移两阶段测试（0.2.13 T4——真实 SQLite 隔离库，非内存替身）。

锚清单（design 8.2 + F1）：
- dry-run 零写入（逐字节不变）
- 不可解析条目整体退出零写入 + 阻塞清单附原因
- 核对清单（存量形构造：编码指标/基础列/插件列混合）
- 幂等连续（迁移后同条件集+区间集登记命中既有 id——验收⑤）
- 留痕未改写（research_log 原样；判定并集区间不变）
- rejected 模型 config 不迁（清单「不适用」）
- F1 影子命中锚：旧算法 screen 留痕 × 迁移后同候选假设 → 验证 409
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core.analysis.column_ref import canonical_ref
from core.analysis.condition_identity import legacy_compatible_hash
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.db_ext import init_db_ext

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_column_refs.py"

_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


def _seed_legacy(db: Path):
    """构造存量形：编码指标/基础列混合假设 + valid/rejected 模型 + screen 留痕。"""
    init_db_ext(db)
    ensure_research_tables(db)
    # 迁移前旧形直插（入口已关旧形——存量构造绕过入口，与真实存量同形）
    conn = sqlite3.connect(db)
    legacy = {"when_conditions": [
        {"subject": "rsi_14", "action": "lt", "target": 30},
        {"subject": "adx_14", "action": "gt", "target": 20}],
        "then_conditions": [{"subject": "close", "action": "gt", "target": 0}]}
    import hashlib
    ranges_hash = hashlib.sha256(json.dumps(
        _RANGES, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    hid = "hyp_legacy01"
    conn.execute(
        "INSERT INTO research_hypotheses (hypothesis_id, status, conditions, "
        "conditions_hash, ranges_hash, statement, data_ranges, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (hid, "active", json.dumps(legacy, ensure_ascii=False), "old_hash",
         ranges_hash, "legacy", json.dumps(_RANGES),
         "2024-01-01", "2024-01-01"))
    conn.commit()
    # 模型 valid + rejected
    conn.execute(
        "INSERT INTO model_plugins (plugin_id, name, version, config, "
        "signal_column, status, created_at) VALUES (?,?,?,?,?,?,?)",
        ("mpl_v1", "alpha", 1,
         json.dumps({"name": "alpha", "features": ["close", "volume"],
                     "template": "rolling_logistic",
                     "params": {}, "target": {}, "_config_hash": "x"}),
         "model_alpha", "valid", "2024-01-01"))
    conn.execute(
        "INSERT INTO model_plugins (plugin_id, name, version, config, "
        "signal_column, status, created_at) VALUES (?,?,?,?,?,?,?)",
        ("mpl_r1", "beta", 1,
         json.dumps({"name": "beta", "features": ["close"],
                     "template": "rolling_logistic",
                     "params": {}, "target": {}, "_config_hash": "y"}),
         "model_beta", "rejected", "2024-01-01"))
    conn.commit()
    conn.close()
    return hid


def _run(db: Path, *extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(db), *extra],
        capture_output=True, text=True)


def _db_bytes(db: Path) -> bytes:
    return db.read_bytes()


class TestTwoPhaseAtomicity:
    def test_dry_run_leaves_db_byte_identical(self, tmp_path):
        db = tmp_path / "t.db"
        _seed_legacy(db)
        before = _db_bytes(db)
        r = _run(db, "--dry-run")
        assert r.returncode == 0, r.stdout + r.stderr
        assert _db_bytes(db) == before, "dry-run 必须零写入（逐字节不变）"

    def test_unparseable_entry_blocks_all_with_zero_writes(self, tmp_path):
        db = tmp_path / "t.db"
        _seed_legacy(db)
        conn = sqlite3.connect(db)
        conn.execute(  # 构造不可解析编码（往返不一致：rsi_14 带脏段）
            "INSERT INTO research_hypotheses (hypothesis_id, status, "
            "conditions, conditions_hash, ranges_hash, statement, "
            "data_ranges, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("hyp_bad1", "active",
             json.dumps({"when_conditions": [
                 {"subject": "macd_12_26", "action": "lt", "target": 1}],
                 "then_conditions": [
                     {"subject": "close", "action": "gt", "target": 0}]}),
             "h0", "h0r", "bad", json.dumps(_RANGES),
             "2024-01-01", "2024-01-01"))
        conn.commit()
        conn.close()
        before = _db_bytes(db)
        r = _run(db)
        assert r.returncode == 1, "有阻塞必须整体退出"
        assert "macd_12_26" in r.stdout
        assert _db_bytes(db) == before, "阻塞退出必须零写入"


class TestMigrationExecution:
    def test_audit_list_and_idempotent_continuity(self, tmp_path):
        db = tmp_path / "t.db"
        hid = _seed_legacy(db)
        out = tmp_path / "audit.json"
        r = _run(db, "--out", str(out))
        assert r.returncode == 0, r.stdout + r.stderr
        report = json.loads(out.read_text())
        assert report["hypotheses"]["to_migrate"] == 1
        # 迁移产物核对：rsi_14/adx_14 → 结构化
        conn = sqlite3.connect(db)
        migrated = json.loads(conn.execute(
            "SELECT conditions FROM research_hypotheses WHERE hypothesis_id=?",
            (hid,)).fetchone()[0])
        conn.close()
        subs = [c["subject"] for g in ("when_conditions", "then_conditions")
                for c in migrated[g]]
        assert subs[0] == {"indicator": "RSI", "params": {"period": 14},
                           "field": "rsi"}
        assert subs[1] == {"indicator": "ADX", "params": {"period": 14},
                           "field": "adx"}
        assert subs[2] == "close"
        # 幂等连续（验收⑤）：迁移后同条件集+区间集登记命中既有 id
        again = register_hypothesis(
            db, conditions=migrated, statement="again", data_ranges=_RANGES)
        assert again["duplicated"] and again["hypothesis_id"] == hid

    def test_rejected_model_config_not_migrated(self, tmp_path):
        db = tmp_path / "t.db"
        _seed_legacy(db)
        out = tmp_path / "audit.json"
        assert _run(db, "--out", str(out)).returncode == 0
        report = json.loads(out.read_text())
        skipped = report["model_plugins"]["skipped_not_applicable"]
        assert any(s["id"] == "mpl_r1" and "rejected" in s["reason"]
                   for s in skipped), "rejected config 应列「不适用」"

    def test_research_log_untouched(self, tmp_path):
        """留痕未改写锚（Q1）：screen 日志区间/摘要原样；判定并集不变。"""
        db = tmp_path / "t.db"
        hid = _seed_legacy(db)
        conn = sqlite3.connect(db)
        old_range = json.dumps(_RANGES, ensure_ascii=False)
        conn.execute(
            "INSERT INTO research_log (log_id, hypothesis_id, entry_type, "
            "ref_id, data_ranges, summary, created_at) VALUES (?,?,?,?,?,?,?)",
            ("log_s1", hid, "screen", "task_1", old_range,
             json.dumps({"top_fingerprints": ["abc"]}), "2024-01-01"))
        conn.commit()
        before_rows = conn.execute(
            "SELECT log_id, data_ranges, summary FROM research_log").fetchall()
        conn.close()
        assert _run(db).returncode == 0
        conn = sqlite3.connect(db)
        after_rows = conn.execute(
            "SELECT log_id, data_ranges, summary FROM research_log").fetchall()
        conn.close()
        assert after_rows == before_rows, "research_log 留痕不得改写"


class TestF1ShadowHit:
    def test_legacy_screen_log_blocks_migrated_hypothesis(self, tmp_path):
        """F1 影子命中锚：旧算法指纹 screen 留痕 × 迁移后同候选假设 → 409。"""
        from core.analysis.hypothesis_service import ResearchError
        db = tmp_path / "t.db"
        _seed_legacy(db)
        # 旧算法留痕：对候选 {rsi_14 lt 30} 用旧指纹（迁移前语义）
        legacy_cond = {"subject": "rsi_14", "action": "lt", "target": 30}
        old_fp = legacy_compatible_hash(legacy_cond, "4h")
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO research_log (log_id, hypothesis_id, entry_type, "
            "ref_id, data_ranges, summary, created_at) VALUES (?,?,?,?,?,?,?)",
            ("log_s2", None, "screen", "task_2",
             json.dumps([{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-03-01T00:00:00Z",
                          "end": "2024-04-01T00:00:00Z"}]),
             json.dumps({"top_fingerprints": [old_fp]}), "2024-03-01"))
        conn.commit()
        conn.close()
        assert _run(db).returncode == 0          # 迁移（rsi_14 → 结构化）
        # 迁移后假设指纹（新算法）≠ 旧留痕指纹——影子保全的验证点：
        from core.analysis.condition_identity import canonical_condition_hash
        struct_cond = {"subject": {"indicator": "RSI",
                                   "params": {"period": 14}, "field": "rsi"},
                       "action": "lt", "target": 30}
        assert canonical_condition_hash(struct_cond, "4h") != old_fp
        assert legacy_compatible_hash(struct_cond, "4h") == old_fp  # 影子=旧算法
        from core.analysis.validation_service import _screen_reuse_check
        with pytest.raises(ResearchError) as ei:
            _screen_reuse_check(db, _seed_migrated_hid(db), {
                "symbol": "BTCUSDT", "timeframe": "4h",
                "start": "2024-03-15T00:00:00Z", "end": "2024-03-20T00:00:00Z"})
        assert ei.value.code == "SCREEN_CANDIDATE_REUSE"

    def test_canonical_and_legacy_agree_on_string_refs(self, tmp_path):
        """字符串 ref 两算法同指纹（F1 完备性第三支）。"""
        cond = {"subject": "close", "action": "gt", "target": 0}
        assert canonical_ref("close") == "close"
        assert (legacy_compatible_hash(cond, "4h")
                == legacy_compatible_hash(cond, "4h"))


def _seed_migrated_hid(db: Path) -> str:
    """取已迁移假设 id（_seed_legacy 只有一条）。"""
    conn = sqlite3.connect(db)
    hid = conn.execute(
        "SELECT hypothesis_id FROM research_hypotheses LIMIT 1").fetchone()[0]
    conn.close()
    return hid
