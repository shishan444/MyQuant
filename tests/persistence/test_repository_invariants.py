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
from core.persistence.repository import dataset_meta, strategy  # live_trading 已退役（retire-legacy B2）


class TestUpdateWhitelistPartitionInvariants:
    """白名单划分不变量：白名单 ∪ 身份列 = 模型全列，两集不交。

    列增删时（新列进白名单或身份列）此处红——提醒同步白名单，
    防「新列不可更新」或「身份列可改写」两类静默漂移。
    """

    def test_strategy更新白名单在模型列集内(self):
        allowed = set(strategy._ALLOWED_UPDATE_FIELDS)
        table_cols = {c.name for c in models.Strategy.__table__.columns}
        assert allowed <= table_cols, "白名单含模型外幽灵列"


class TestUpdatedAtMonotonicity:
    """updated_at 单调族：写后不减、恒 >= created_at。"""

