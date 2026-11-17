# B3c 写密集域迁移 — evidence

容器：`refactor-myquant-backend-b1-b11` · 组 B3c · tag BE3c · 2026-09-16
验证：后端全量 pytest **2162 passed**（`--ignore=tests/e2e` 口径）+ **L3 复跑 14 passed**（BE-P2 Phase 末，`web/e2e/main-paths.spec.ts` 前后端真实服务）——ORM 绞杀者三重守护（全量测试 + schema 对照 + L3）至此三重全绿。

## 5.1 两域迁移（B3c.0 冻结映射）

| 域 | 函数 | 等价锚点 |
|---|---|---|
| paper_trading（12） | task 侧 save/get/update/list/count/delete + trade 侧 save/list/count + equity 侧 save/list + compute_trading_metrics | save_task 硬编码初值（pending/v2/balance=initial_cash）；save_trade INSERT OR IGNORE（B1.3 幂等）；save_equity_snapshots 批量 OR IGNORE；update kwargs 白名单；**delete 三表级联单事务**；metrics 公式单源 core.scoring 不动 |
| live_trading（6） | save/get/update/list/delete/update_heartbeat | save INSERT OR REPLACE（创建/重置）；update kwargs 直接为列；heartbeat 复用 update |

## live 表收编（`_connect` 双源消灭）

- live 建表并入 init_db_ext migration 26（`_create_live_trading_tables`，idempotent）
- `core/live_trading/persistence.py` **整文件退役**（152 行，含独立 `_connect`/`_now_iso` 双源）
- 调用方改向：`api/routes/live_trading.py`、`core/live_trading/runner.py`、app startup 冗余 init 删除（init_db_ext 已覆盖）、adopt 脚本与 ORM↔legacy 对照测试同步
- 测试重锚定：test_live_persistence/test_live_runner/test_live_api 的 import 源与建表调用

## 调用方零改动原则的例外记录

live 域是纯表访问层文件（无其他逻辑）且调用方仅 3 处——整文件退役直接改 import，不留委托壳（design.md B3c.0 裁决）。路由 create_task 内的防御性 `init_live_trading_tables` 调用属旧轨残留，删除（建表职责归 init 链）。
