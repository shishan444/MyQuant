"""repository 层三族不变量锚（阶段四步骤4 属性补强）。

不是单点行为，而是对一类输入应保持的性质（TESTING_V3 方法表
「不变量与属性」）：
- 白名单族：update 白名单与模型列集的划分不变量——白名单 ∪
  身份列 = 模型全列且两集不交（列漂移即红）；
- 唯一索引幂等族：任意批量重放收敛到首写集（first-write-wins）；
- updated_at 单调族：任意写序列 updated_at 不减且 >= created_at。

锚点分类（V3 四分类）：C 类（对拍/不变量锚）= 全部（白名单划分
闭包对拍、幂等收敛性质、updated_at 单调性）；无 A/B/D 类。
"""

import random

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.orm import models
from core.persistence.repository import dataset_meta, live_trading, paper_trading, strategy


class TestUpdateWhitelistPartitionInvariants:
    """白名单划分不变量：白名单 ∪ 身份列 = 模型全列，两集不交。

    列增删时（新列进白名单或身份列）此处红——提醒同步白名单，
    防「新列不可更新」或「身份列可改写」两类静默漂移。
    """

    def test_live_trading白名单划分闭包(self):
        allowed = set(live_trading._ALLOWED_UPDATE_COLUMNS)
        table_cols = {c.name for c in models.LiveTradingTask.__table__.columns}
        identity = table_cols - allowed
        # 身份列契约：创建后不可改（含内部键 updated_at 在白名单侧）
        assert identity == {"task_id", "symbol", "timeframe", "dna_json",
                            "direction", "created_at"}, \
            "身份列集合漂移——白名单与身份划分需重新裁决"
        assert not (allowed & identity)

    def test_paper_trading白名单划分闭包(self):
        allowed = set(paper_trading._ALLOWED_UPDATE_COLUMNS)
        table_cols = {c.name for c in models.PaperTradingTask.__table__.columns}
        identity = table_cols - allowed
        assert identity == {"task_id", "symbol", "timeframe", "dna_json",
                            "direction", "created_at",
                            "initial_cash", "fee", "leverage",
                            "score_template", "strategy_name",
                            "confidence_sizing_enabled"}, \
            "paper 白名单外列集合漂移（初始参数列为创建时定死）"
        assert not (allowed & identity)

    def test_strategy更新白名单在模型列集内(self):
        allowed = set(strategy._ALLOWED_UPDATE_FIELDS)
        table_cols = {c.name for c in models.Strategy.__table__.columns}
        assert allowed <= table_cols, "白名单含模型外幽灵列"


class TestIdempotentReplayConvergence:
    """幂等族收敛性质：任意批量两轮重放，终态 = 首写集。"""

    def test_equity快照随机批量重放收敛首写集(self, tmp_path):
        db = tmp_path / "inv.db"
        init_db_ext(db)
        paper_trading.save_paper_trading_task(db, task_id="t1", dna_json="{}")
        rng = random.Random(42)
        batch = [{"timestamp": f"2026-01-01T{i:02d}:00",
                  "equity": float(rng.randint(90, 110)),
                  "balance": 100.0} for i in range(12)]
        first = [dict(s) for s in batch]
        paper_trading.save_equity_snapshots(db, "t1", batch)
        # 重放同一批（值扰动）+ 乱序
        rng.shuffle(batch)
        for s in batch:
            s["equity"] = 999.0
        paper_trading.save_equity_snapshots(db, "t1", batch)
        rows = paper_trading.list_equity_snapshots(db, "t1")
        assert len(rows) == len(first), "重放不得增行（唯一索引幂等）"
        assert [r["equity"] for r in rows] == [s["equity"] for s in first], \
            "first-write-wins：首写值胜出，重放值被忽略"

    def test_paper_trade随机键集重放收敛首写集(self, tmp_path):
        db = tmp_path / "inv.db"
        init_db_ext(db)
        paper_trading.save_paper_trading_task(db, task_id="t1", dna_json="{}")
        rng = random.Random(7)
        keys = [(f"2026-01-01T{i:02d}:00", a)
                for i in range(6) for a in ("open", "close")]
        for bar, action in keys:
            paper_trading.save_paper_trade(
                db, task_id="t1", bar_time=bar, side="buy", action=action,
                price=float(rng.randint(1, 100)), quantity=1.0)
        first_count = paper_trading.count_paper_trades(db, "t1")
        rng.shuffle(keys)
        for bar, action in keys:  # 重放同键集不同载荷
            paper_trading.save_paper_trade(
                db, task_id="t1", bar_time=bar, side="sell", action=action,
                price=999.0, quantity=9.0)
        assert paper_trading.count_paper_trades(db, "t1") == first_count, \
            "同键重放不得增行"
        assert all(t["price"] != 999.0
                   for t in paper_trading.list_paper_trades(db, "t1", limit=100)), \
            "first-write-wins：重放载荷被忽略"


class TestUpdatedAtMonotonicity:
    """updated_at 单调族：写后不减、恒 >= created_at。"""

    def test_live任务写序列单调(self, tmp_path):
        db = tmp_path / "inv.db"
        init_db_ext(db)
        live_trading.save_live_trading_task(
            db, task_id="t1", symbol="BTCUSDT", timeframe="4h",
            dna_json="{}", direction="long")
        import time
        prev = live_trading.get_live_trading_task(db, "t1")["updated_at"]
        for i in range(5):
            time.sleep(0.002)
            live_trading.update_live_trading_task(db, "t1", balance=1000.0 + i)
            row = live_trading.get_live_trading_task(db, "t1")
            assert row["updated_at"] >= prev, "写后 updated_at 不减"
            assert row["updated_at"] >= row["created_at"], "不得早于创建时刻"
            prev = row["updated_at"]

    def test_paper任务写序列单调(self, tmp_path):
        db = tmp_path / "inv.db"
        init_db_ext(db)
        paper_trading.save_paper_trading_task(db, task_id="t1", dna_json="{}")
        import time
        prev = paper_trading.get_paper_trading_task(db, "t1")["updated_at"]
        for i in range(5):
            time.sleep(0.002)
            paper_trading.update_paper_trading_task(db, "t1", equity=100.0 + i)
            row = paper_trading.get_paper_trading_task(db, "t1")
            assert row["updated_at"] >= prev
            assert row["updated_at"] >= row["created_at"]
            prev = row["updated_at"]
