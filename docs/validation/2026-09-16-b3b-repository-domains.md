# B3b 中风险域迁移 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B3b · tag BE3b · 2026-09-16
验证：后端全量 pytest **2159 passed** + 新增 strategy 等价锚定 3 例（共 17 ORM 基建用例）。

## 4.1 三域迁移（15 公开函数，B3b.0 冻结映射）

| 域 | 函数 | 等价锚点 |
|---|---|---|
| strategy（8） | save/get/count_by_tasks/count/list/update/delete（+私有 `_build_strategy_where` 随迁为 `_strategy_filters`） | gene_signature 去重语义（高分替换/低分跳过，ORM 版单事务原子化）；tags LIKE、qualified 布尔过滤；list 排序白名单 ValueError；count_by_tasks GROUP BY+补零 |
| backtest_result（3） | save/get/list | 裸 INSERT；list 排序经 `getattr` 限定列名（合法输入行为等价，非法输入消除 SQL 注入面——旧轨动态拼列） |
| verify_session（4） | save/get/update/list | update 非 None 字段语义；list created_at 倒序 |

db_ext 同名函数一行委托（签名不变、调用方零改动、函数级事务）。

## 实施发现与修复（本组两个重要坑）

1. **engine FK 强制回退**：B3a.1 的 `PRAGMA foreign_keys=ON` 是行为变更——存量写入序不保证父行先在（如 backtest_result 早于 strategy 落库），TestBacktestResult 两用例红即实证。修正=FK 保持 SQLite 默认 OFF（与旧轨 `_connect` 行为等价）；模型 ForeignKey 仅为元数据声明，不构成运行时约束。engine.py 注记裁决理由。
2. **alembic `fileConfig` 日志污染**（测试基建坑）：`command.stamp` → env.py `fileConfig(config_file_name)` 默认 `disable_existing_loggers=True`——进程内既有 logger 全部 `disabled=True`，此后日志静默丢弃。全量序（b1<orm 字母序）掩盖了它；子集组合暴露（4 个 caplog 测试红）。修复=env.py 显式 `disable_existing_loggers=False`。诊断方法：诊断脚本打印 logger.propagate/disabled 链 + root.handlers。
