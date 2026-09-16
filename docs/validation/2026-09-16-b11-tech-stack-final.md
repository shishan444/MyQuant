# B11 技术栈终态对账（容器收尾）

容器：`refactor-myquant-backend-b1-b11` · 组 B11 · tag BE11 · 2026-09-16

## 引入记录（本容器新增依赖）

| 依赖 | 版本 | 引入组 | 用途 |
|---|---|---|---|
| SQLAlchemy | 2.0.x | B3a | repository 层（表访问唯一入口）；同步 Session（单写者裁决） |
| Alembic | 1.20.x | B3a | schema 演进唯一轨（baseline ae6e14d43dc5；adopt=先 legacy 补齐后 stamp） |
| python-socketio | 5.x | B8 | 四通道实时（四证明见 B8 evidence：无重连语义/双端成本/ASGI 生态/受控入口） |

## 不引入裁决（触发条件）

- **structlog/uv**：单用户本地工具，标准 logging 承担；触发=多用户/服务化部署
- **异步 ORM**：SQLite 单写者无收益；触发=迁移 PostgreSQL 等服务端 DB
- **config.json 加密**：本地单用户威胁模型（docs/security 图二）；触发=共享主机/多用户

## 项目级规则偏差清单（对齐 design.md「规则符合性边界」节）

| 偏差 | 触发条件（届时立项） |
|---|---|
| limit/offset 分页（无 cursor） | 第二消费者/开放 API |
| 无 /v1 版本前缀 | 同上 |
| float 金额 | 跨端金额运算/对账 |
| naive-Z 时间戳（B8 事件已防御：字符串透传无 Date 路径） | 新消费路径按 ISO 解析/跨时区 |
| 非进化域函数级事务（终态裁决） | 跨写原子性需求出现 |
| 域文档载体=openspec 工件（不建 DESIGN.md） | —（单事实源原则） |

## 结构终态速览

api/{app,runner,runner_progress,realtime,stream_control} + routes/{strategies 包五模块,config,data,evolution,trading,live_trading,validate,scene,discovery}；core/{persistence/{orm,repository,db,db_ext,legacy_init,timestamps},market_model,concurrency,services,evolution_service,...}；tests/ 九域目录+infra；alembic/ 版本链。

## 容器完成判据核验（14.2）

- 14 组全勾（tasks.md 56/56）
- 后端全量 pytest 2162 passed（--ignore=tests/e2e 口径；e2e=pytest_e2e.ini 独立口径）
- 前端三件套：tsc ✓ / vitest 314 ✓ / lint 零增量（75=75 基线）
- L3 全量 14 passed（默认 socketio 形态，前后端真实服务）
- 提交链 24 个（BE1..BE11 tag 锚点）；evidence 13 份落档 docs/validation/
- **终验清单移交用户**：合并 main（唯一合并点，用户约束）——建议以 --no-ff 合并保留任务边界
