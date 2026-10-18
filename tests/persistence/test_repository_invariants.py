"""repository 层两族不变量锚（阶段四步骤4 属性补强）。

不是单点行为，而是对一类输入应保持的性质（TESTING_V3 方法表
「不变量与属性」）：
- 白名单族：update 白名单与模型列集的划分不变量——白名单 ∪
  身份列 = 模型全列且两集不交（列漂移即红）；
- updated_at 单调族：任意写序列 updated_at 不减且 >= created_at。

锚点分类（V3 四分类）：C 类（对拍/不变量锚）= 全部（白名单划分
闭包对拍、updated_at 单调性）；无 A/B/D 类。
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.orm import models
from core.persistence.repository import dataset_meta, live_trading, strategy


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

    def test_strategy更新白名单在模型列集内(self):
        allowed = set(strategy._ALLOWED_UPDATE_FIELDS)
        table_cols = {c.name for c in models.Strategy.__table__.columns}
        assert allowed <= table_cols, "白名单含模型外幽灵列"


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
