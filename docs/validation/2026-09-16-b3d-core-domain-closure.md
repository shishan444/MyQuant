# B3d 核心域收口 — evidence（ORM 绞杀者完成汇总）

容器：`refactor-myquant-backend-b1-b11` · 组 B3d · tag BE3d · 2026-09-16
验证：后端全量 pytest **2162 passed**；api 层裸 SQL 终检零命中（`conn.execute|conn2.execute|_get_connection|sqlite3.connect` 全零）。

## 6.1 evolution 域迁移（10+9 函数）

- db.py 十函数一行委托；D3 条件更新以 `status.in_()` + `rowcount` 表达——7 个状态机守护测试（test_task_state_machine.py）零漂移
- init_db 经 ORM 元数据建表（模型为唯一事实源）；SQL 轨（migrations/*.sql）文档性退役（008 裸 ALTER 对 ORM 建列撞 duplicate column 实证）
- 实施发现（测试环境历史分裂）：裸 `save_task` 9 列在旧缺列库下 `SELECT *` 缺键 → 调用方 `.get(key, default)` 走 default；全列库下 NULL → `.get` 拿 None **绕过 default**。生产语义本就是全列（路由创建写全值），测试对齐 init_db_ext；test_stop_during_data_loading 重锚定为 stop 检查序列第 5 次触发（旧「第二次 compute」是缺列库异常 fallback 偶发产物；真跑进化触发 vectorbt merge_dicts 死循环——**既有病，另案立项**，与容器改动正交，stash 对照实证）

## 6.2 裸 SQL 收口（api/ 层零裸 SQL）

| 收口点 | 落位 |
|---|---|
| api/runner.py 八处（heartbeat/phase/progress/recover/restart/find_pending/代际游标/冠军指标×2/OOS/乐观锁 best_score） | repository/evolution.py 增量函数 |
| core/trading/runner.py 三处（recover/find_pending/_get_task） | repository/paper_trading.py 增量函数 |
| api/routes/evolution.py 两处（任务配置 UPDATE、top snapshots 查询） | repo.update_task_config / list_top_snapshots |
| api/routes/strategies.py 一处（verify 滚动统计 COALESCE/MAX） | repo.update_strategy_verify_stats（SQLite 标量 max，greatest 不存在） |
| `_get_connection`/`connect`/`db.py._connect` | 删除；`_connect` 移至 db_ext（legacy init/ALTER 链专用，迁移工具属性） |

## ORM 绞杀者终态（B3a-d 汇总）

- 七域 repository（dataset_meta/strategy/backtest_result/verify_session/paper_trading/live_trading/evolution）+ orm/（11 表模型+engine 工厂）；db.py=纯委托层；db_ext=委托层+legacy init/ALTER 链（存量接管用）
- 三轨归一：建表权=ORM 模型、演进权=Alembic（baseline ae6e14d43dc5）、业务访问权=repository；连接统一=WAL+busy_timeout 经 engine 工厂
- 三重守护全绿：全量 2162 + ORM↔legacy PRAGMA 对照（test_orm_infra.py）+ L3 复跑（BE3c 时 14 passed）
