"""legacy_init 存量库升级路径特征锚（阶段四步骤2 盲区消解）。

覆盖新库路径不可达、仅存量升级触达的行为面：
- 各 _apply_* 的 ALTER 补列（新库由 ORM create_all 先建全列，
  PRAGMA guard 恒命中「已存在」侧——ALTER 行只有旧形库走到）；
- migration 019 去重执行体（同 gene_signature 保留最高分、
  NULL 分殿后）；
- migration 020 名称重算体（无名行按 dna 重算；坏 dna 行级
  continue 跳过不阻塞）。

不可达判定（调用图依据）：
- _get_applied_versions 的表不存在 except（63-65）：调用序上
  _ensure_schema_version_table 先建 schema_version 表（init_db_ext
  :367-368），后继查询时表必存在——防御冗余，无输入路径。
- _dedup 的 delete_ids 空分支（472->461）：dup_sigs 由
  HAVING COUNT(*) > 1 求得，同签名至少 2 行、rows[1:] 必非空
  ——分支恒真侧，False 侧无输入路径。
- _dedup/_recompute 的整体 except（479-480/510-511）：需 SELECT/
  UPDATE 内部错误（表列缺失时 PRAGMA 先行、数据坏值走行级
  except），无常规输入路径——防御降级通道（docstring 自述
  「失败降级不阻塞升级」为其存在语义）。

锚点分类（V3 四分类）：B 类（行为契约锚）= 升级补列/去重/重算；
另载不可达判定 4 组（调用图依据，见模块 docstring）；无 A/D 类。
"""

import sqlite3

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import find_by_gene_signature
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
)


def _dna_json():
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
    )
    return dna.to_json()


# 旧形 evolution_task（2024 年中形态：无任何扩展列）——NOT NULL 基础列集
_LEGACY_EVOLUTION_TASK = """
CREATE TABLE evolution_task (
    task_id      TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'running',
    target_score REAL NOT NULL,
    score_template TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    initial_dna  TEXT NOT NULL,
    champion_dna TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    stop_reason  TEXT
)
"""

# strategy 旧形（去重/重算所需列 + 缺 ext 列族：metrics_json/status
# 与 verify 五列由迁移 8/24/25 补齐）
_LEGACY_STRATEGY = """
CREATE TABLE strategy (
    strategy_id TEXT PRIMARY KEY,
    name TEXT, dna_json TEXT, source TEXT, source_task_id TEXT,
    symbol TEXT, timeframe TEXT, best_score REAL, generation INTEGER,
    parent_ids TEXT, tags TEXT, notes TEXT,
    gene_signature TEXT, created_at TEXT, updated_at TEXT
)
"""

# backtest_result 旧形（缺 session_id——迁移 23 补齐）
_LEGACY_BACKTEST_RESULT = """
CREATE TABLE backtest_result (
    result_id TEXT PRIMARY KEY,
    strategy_id TEXT, symbol TEXT, timeframe TEXT,
    data_start TEXT, data_end TEXT, init_cash REAL, fee REAL, slippage REAL,
    total_return REAL, sharpe_ratio REAL, max_drawdown REAL,
    win_rate REAL, total_trades INTEGER, total_score REAL,
    template_name TEXT, dimension_scores TEXT, equity_curve TEXT,
    trades_json TEXT, run_source TEXT, created_at TEXT
)
"""

# paper_trading_task 旧形（缺 execution_model/bars_held/
# position_open_cost/pending_decision_json/confidence_sizing_enabled/
# prediction_dna_json 六列——迁移 13-18 逐列补齐）
_LEGACY_PAPER_TASK = """
CREATE TABLE paper_trading_task (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    strategy_name TEXT, symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
    timeframe TEXT NOT NULL DEFAULT '4h',
    initial_cash REAL NOT NULL DEFAULT 100000,
    fee REAL NOT NULL DEFAULT 0.001,
    leverage INTEGER NOT NULL DEFAULT 1,
    direction TEXT NOT NULL DEFAULT 'long',
    dna_json TEXT NOT NULL,
    score_template TEXT DEFAULT 'explorer',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)
"""


def _legacy_db(tmp_path, *, with_tasks=True, strategy_rows=()):
    """手工构造旧形库（跳过 ORM 建表），返回路径。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    if with_tasks:
        conn.execute(_LEGACY_EVOLUTION_TASK)
        conn.execute(
            "INSERT INTO evolution_task VALUES "
            "('t1','running',80.0,'profit_first','BTCUSDT','4h','{}',NULL,"
            "'2024-01-01','2024-01-01',NULL)")
    conn.execute(_LEGACY_STRATEGY)
    for row in strategy_rows:
        conn.execute(
            "INSERT INTO strategy (strategy_id, name, dna_json, symbol, "
            "timeframe, best_score, gene_signature, source, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", row)
    conn.execute(_LEGACY_BACKTEST_RESULT)
    conn.execute(_LEGACY_PAPER_TASK)
    conn.commit()
    conn.close()
    return db


def _cols(db, table):
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


class TestLegacyUpgradeAlters:
    """旧形库升级：PRAGMA guard「缺列」侧 ALTER 补列执行。"""

    def test_旧形任务表补齐扩展列族(self, tmp_path):
        db = _legacy_db(tmp_path)
        init_db_ext(db)
        cols = _cols(db, "evolution_task")
        # 各迁移族的代表性列（_ALTER 主族/_CONSTRAINT/_MTF/_PROGRESS/
        # _SCORING/_FITNESS/_OOS 族）——init_db_ext 26 步迁移的实际覆盖面
        expected = {
            "indicator_pool", "mode",      # constraint/mtf 族
            "current_phase", "progress_json", "heartbeat_at",  # progress 族
            "best_fitness",               # fitness 族
            "oos_fitness", "oos_qualified",  # oos 族
            "current_generation", "population_size",  # alter 主族
        }
        missing = expected - cols
        assert not missing, f"旧形库升级后仍缺列: {missing}"
        # 观察（非断言）：strategy_threshold 不在 init_db_ext 迁移族——
        # 仅 ORM 新建库含此列；历史库若依赖 init_db_ext 升级不会补齐
        # 该列（归 Alembic adopt 轨职责，非本测试面）

    def test_升级幂等_二次跑不炸(self, tmp_path):
        db = _legacy_db(tmp_path)
        init_db_ext(db)
        init_db_ext(db)  # 二次：guard 全命中已存在侧
        assert "indicator_pool" in _cols(db, "evolution_task")

    def test_旧形三表扩展列补齐(self, tmp_path):
        """strategy/backtest_result/paper_trading_task 的 ext 列族
        （迁移 8/23/24/25/13-18）对旧形库逐列 ALTER 补齐。"""
        db = _legacy_db(tmp_path)
        init_db_ext(db)
        assert {"metrics_json", "status", "verify_count", "verify_star"} \
            <= _cols(db, "strategy"), "strategy ext/verify 列族"
        assert "session_id" in _cols(db, "backtest_result")
        assert {"execution_model", "bars_held", "position_open_cost",
                "pending_decision_json", "confidence_sizing_enabled",
                "prediction_dna_json"} <= _cols(db, "paper_trading_task"), \
            "paper 六扩展列（迁移 13-18）"


class TestMigration19Dedup:
    """migration 019：同 gene_signature 去重（保留最高分、NULL 殿后）。"""

    def test_重复签名保留最高分删除其余(self, tmp_path):
        db = _legacy_db(tmp_path, strategy_rows=[
            ("keep", "n1", _dna_json(), "BTCUSDT", "4h", 90.0, "sig-A", "manual",
             "2024-01-01", "2024-01-01"),
            ("low", "n2", _dna_json(), "BTCUSDT", "4h", 30.0, "sig-A", "manual",
             "2024-01-01", "2024-01-01"),
            ("noscore", "n3", _dna_json(), "BTCUSDT", "4h", None, "sig-A", "manual",
             "2024-01-01", "2024-01-01"),
            ("other", "n4", _dna_json(), "BTCUSDT", "4h", 50.0, "sig-B", "manual",
             "2024-01-01", "2024-01-01"),
        ])
        init_db_ext(db)
        rows = find_by_gene_signature(db, "sig-A")
        assert [r["strategy_id"] for r in rows] == ["keep"], \
            "同签名保留最高分一条（NULL 分殿后不入保留位）"
        assert len(find_by_gene_signature(db, "sig-B")) == 1

    def test_无重复签名_去重零效果(self, tmp_path):
        db = _legacy_db(tmp_path, strategy_rows=[
            ("solo", "n1", _dna_json(), "BTCUSDT", "4h", 10.0, "sig-A", "manual",
             "2024-01-01", "2024-01-01"),
        ])
        init_db_ext(db)
        assert len(find_by_gene_signature(db, "sig-A")) == 1


class TestMigration20RecomputeNames:
    """migration 020：无名策略按 dna 重算名；坏 dna 行跳过不阻塞。"""

    def test_无名行重算_坏dna行跳过(self, tmp_path):
        db = _legacy_db(tmp_path, strategy_rows=[
            # 无名 + 有效 dna → 重算得名
            ("noname", None, _dna_json(), "BTCUSDT", "4h", 10.0, "sig-A", "manual",
             "2024-01-01", "2024-01-01"),
            # 坏 dna_json → 行级 continue，name 保持原值
            ("baddna", "keep-me", "not-json", "BTCUSDT", "4h", 10.0, None, "manual",
             "2024-01-01", "2024-01-01"),
        ])
        init_db_ext(db)
        conn = sqlite3.connect(str(db))
        rows = dict(conn.execute(
            "SELECT strategy_id, name FROM strategy").fetchall())
        conn.close()
        assert rows["noname"], "无名行须被重算出名称"
        assert rows["baddna"] == "keep-me", "坏 dna 行保持原名（行级跳过）"
