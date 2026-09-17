# 链路覆盖矩阵（Phase 0 审计产出）

- 变更容器：`verify-myquant-capability-integrity`
- 审计日期：2026-09-16
- 证据基线：分支 `feature/verify-capability-integrity` @ bb8a2c4（B1-B11 之后）

## 1. 审计方法与口径

**断言强度分级**：

| 级 | 定义 | 判定特征（断言代码形态） |
|---|---|---|
| L0 | 不崩 | `to_be_visible` / `len(html)>100` / `status_code == 200` / console 无错 |
| L1 | 有值（含 mock 往返） | 字段存在且类型对；`assert data["x"] == mock值`（mock 进 mock 出=传输管道验证，非计算验证） |
| L2 | 值对 | 数值正确且与相邻步骤自洽（落盘↔入库↔列表计数一致；指标=独立参照判定值；状态机与触发动作一致）；**要求核心计算交界处零 mock** |

**锚点粒度**：`测试文件::测试函数`；抽查格附断言行号。单元级测试不算链路格覆盖，但在「步骤旁证」列引用（单元层已验证的局部行为，链路层不重复验证其算法，只验证交界与自洽）。

**标注纪律**：每格标注基于断言代码实际形态检视，非印象。

## 2. 链路主清单（双来源交叉）

### 2.1 用户旅程来源（8 页面 → 10 链）

| # | 链路 | 页面 | 核心操作 |
|---|---|---|---|
| A | 数据导入链 | DataManagement | 选 symbol/周期 → 导入 → 落盘 → 入库 → 列表 → 预览 → 删除 |
| B | 单回测链 | Lab | 构建 DNA → 单回测 → 指标+评分展示 |
| C | 对比链 | Lab | 多 DNA compare |
| D | 进化闭环链 | Evolution | 创建任务 → running → 代际推进 → 冠军 → pause/resume/stop → 终态 |
| E | 验证链 | Strategies/Verify | 发起 verify → 会话 → 分段进度 → 汇总 → 历史可取 |
| F | 批量链 | BatchBacktest | batch stream → 进度 → 结果 → 达标率 |
| G | 模拟盘链 | Trading | 创建任务 → bar 推进 → 事件 → equity/metrics/trades → stop/restart |
| H | 实盘链 | （后端域） | live-trading 全链 —— **本批豁免**（见 5.2） |
| I | 发现/预测链 | （后端域） | discovery patterns/predict/similar、validate —— **本批豁免**（见 5.2） |
| J | 配置链 | Settings | config GET/PUT（S1 凭证掩码） |

### 2.2 API 面来源（50 路径 → 链路映射）

来自 `create_app().openapi()['paths']` 实测（FastAPI 0.137 惰性路由，`app.routes` 不可靠，openapi 为对外契约权威）。

| 路径 | 链路 | 后端测试触达 | 前端静态引用 |
|---|---|---|---|
| GET/PUT /api/config | J | tests/infra/test_b9_s1_credentials.py（单元） | ✓ |
| GET /api/data/available-sources | A | tests/data/test_fetcher.py（mock） | ✓ |
| POST /api/data/import | A | tests/api/test_api.py:613（patch import_csv） | ✓ |
| POST /api/data/import-batch | A | test_api.py:735/767（patch import_csv_batch） | ✓ |
| GET /api/data/datasets | A | test_api.py（真栈） | ✓ |
| DELETE/GET /api/data/datasets/{id} | A | test_api.py（真栈） | ✓（动态拼接） |
| GET /api/data/datasets/{id}/preview | A | test_api.py（真栈） | ✓ |
| GET /api/data/datasets/{id}/ohlcv | A | test_api.py（真栈） | ✓ |
| GET /api/data/ohlcv/{symbol}/{timeframe} | A-旁支 | 未触达 → 裁决：**豁免**（与 preview/ohlcv 重复能力，历史接口） | 无静态引用 |
| GET /api/data/chart-indicators/{sym}/{tf} | A-旁支 | 未触达 → 裁决：**豁免**（图表衍生指标，依赖列缓存，非主链） | 无静态引用 |
| POST /api/strategies/backtest | B | test_api.py:253/326（patch 引擎+评分） | ✓ |
| POST /api/strategies/compare | C | tests/api/test_api_compare.py（t=5） | ✓ |
| POST /api/strategies/verify | E | **无端点级**（schema/processor 单元级） | ✓ |
| POST /api/strategies/verify/stream | E | tests/api/test_api.py WS 段（形态待查——组 3 实施时锚定） | ✓ |
| GET /api/strategies/verify/sessions(+results) | E | 无 → 补 | ✓ |
| GET /api/strategies/verify/history | E | 无 → 补 | ✓ |
| POST /api/strategies/batch-backtest/stream | F | 仅 tests/e2e/test_v014（冒烟） | ✓ |
| GET /api/strategies/batch-backtest/{result_id} | F | 仅 e2e v014（冒烟） | ✓（动态拼接） |
| GET/POST /api/strategies、GET/PUT/DELETE /api/strategies/{id} | B 前置 | test_api.py CRUD 段（真栈） | ✓ |
| GET/POST /api/evolution/tasks(+子资源×7) | D | test_api.py 创建段（真栈 L1）；推进/冠军/终态无端点级 | ✓ |
| POST /api/evolution/tasks/{id}/pause,resume,stop | D | tests/evolution/test_evolution_service*.py（service 单元级） | ✓ |
| GET /api/evolution/runner-status | D | 无 → 补（D 链步骤内） | ✓ |
| GET/POST /api/trading/tasks、GET equity/metrics/trades | G | **无端点级**（trading 域 15 文件全单元级） | ✓ |
| POST /api/trading/tasks/{id}/stop,restart；DELETE | G | 无 → 补 | ✓ |
| GET /api/trading/runner-status | G | 无 → 补 | ✓ |
| GET/POST /api/live-trading/tasks(+子资源) | H | tests/live/test_live_api.py（MOCK） | 无 → 豁免 |
| POST /api/discovery/patterns,predict,similar | I | tests/prediction 单元级 | 无 → 豁免 |
| POST /api/validate、/api/validate/rules、/api/validate/scene、GET triggers | I | 未触达 → 豁免 | ✓（validation.ts） |
| GET /api/scene/types | I-旁支 | 未触达 → 豁免（场景配置只读枚举） | ✓ |
| POST /api/validate | I | 同上 | 同上 |
| GET /api/health | 基础设施 | test_api.py | — |

**未触达路由裁决汇总**（spec 场景「API 面无遗漏」）：

- **补测**（进入 Phase 1 清单）：verify 三端点、batch 双端点、trading 六端点、evolution 推进/runner-status——均属五主链
- **显式豁免**：`/api/data/ohlcv`（重复能力）、`/api/data/chart-indicators`（衍生旁支）、live-trading 全部（H 链豁免）、discovery/validate/scene 全部（I 链豁免）

## 3. 覆盖矩阵（链路 × 步骤）

### 链 A 数据导入链

| 步骤 | 覆盖测试 | 强度 | 锚点 | 步骤旁证（单元级） |
|---|---|---|---|---|
| POST import（含落盘+meta 入库） | test_api.py::test_import_dataset | **L1** | tests/api/test_api.py:613 patch import_csv——模块交界 mock，mock 往返 | data/test_csv_importer.py t=37（导入器单元数值断言 14） |
| parquet 落盘内容正确 | （链路级空白） | **空白** | — | 同上（单元级） |
| dataset_meta 入库字段 | （链路级空白） | **空白** | — | persistence 域（ORM 单元） |
| GET datasets 列表含新集 | test_api.py datasets 段 | L1 | 真栈但断言字段存在级 | — |
| GET preview 数值=落盘数值 | （空白） | **空白** | — | — |
| DELETE 后列表消失 | test_api.py | L1 | — | — |

### 链 B 单回测链

| 步骤 | 覆盖测试 | 强度 | 锚点 | 步骤旁证 |
|---|---|---|---|---|
| POST /strategies 创建 DNA | test_api.py CRUD 段 | L1 | 真栈 | — |
| POST backtest 引擎计算 | test_api.py::test_backtest_strategy | **L1** | tests/api/test_api.py:253-254 patch `_bt_engine_mod`+`score_strategy`——**核心计算交界 mock** | backtest/ 56 文件（引擎单元层 L2 旁证，782 测试） |
| 指标数值正确 | （链路级空白） | **空白** | — | 同上 |
| 评分展示 | 同 mock 段 | L1 | mock 往返 | scoring 单元 |
| 页面渲染数字 | web/e2e/main-paths.spec.ts Lab 段 | L0 | toBeVisible 无业务值断言 | — |

### 链 C 对比链

| 步骤 | 覆盖测试 | 强度 | 锚点 |
|---|---|---|---|
| POST compare 双 DNA 指标对齐 | test_api_compare.py t=5 | L1（抽查：1 数值断言） | tests/api/test_api_compare.py |

→ 裁决：C 并入 B 链测试资产（同引擎同断言器，compare 端点在 B 链 L2 测试中作为第二入口复验）。

### 链 D 进化闭环链

| 步骤 | 覆盖测试 | 强度 | 锚点 | 步骤旁证 |
|---|---|---|---|---|
| POST tasks 创建 | test_api.py::test_create_evolution_task | L1 | 真栈真 DB，断言字段值（pending/symbol） | — |
| running 推进 | （链路级空白） | **空白** | — | evolution/test_evolution_service*.py 状态机单元（pause/stop/resume + TaskNotFound/IllegalTransition） |
| 代际推进事件序 | （空白） | **空白** | — | runner_progress 单元（B2 建） |
| 冠军产出 | （空白） | **空白** | — | evolution/test_champion.py 单元 |
| history/strategies 可取 | （空白） | **空白** | — | — |
| 终态迁移一致性 | （空白） | **空白** | — | service 单元级 |
| 页面 | web/e2e Evolution 段 | L0 | toBeVisible | — |

### 链 E 验证链

| 步骤 | 覆盖测试 | 强度 | 锚点 |
|---|---|---|---|
| schema 校验 | test_api_verify.py 前段 t=14 | L1 | pydantic 默认值断言 |
| POST verify 端点 | **（无）** | **空白** | — |
| 会话/分段/汇总 | test_verify_processor.py（MOCK，t=3） | L1 | 处理器单元 |
| sessions/results/history 端点 | **（无）** | **空白** | — |
| stream 序列 | （待锚定） | 待查 | B8 socketio 域 |
| 页面 | web/e2e Verify 段 | L0 | — |

### 链 F 批量链

| 步骤 | 覆盖测试 | 强度 | 锚点 |
|---|---|---|---|
| POST batch stream 全程 | tests/e2e/test_v014_comprehensive.py | **L0** | 冒烟（num=0） |
| GET result 结果 | 同上 | L0 | 同上 |
| 页面 | web/e2e BatchBacktest 段 | L0 | — |

### 链 G 模拟盘链

| 步骤 | 覆盖测试 | 强度 | 锚点 | 步骤旁证 |
|---|---|---|---|---|
| POST trading/tasks 创建 | **（无端点级）** | **空白** | — | trading/ 15 文件单元（259 测试，数值断言密度最高域） |
| bar 推进→事件 | （空白） | **空白** | — | paper_trade 单元（B1.3 幂等） |
| equity/metrics/trades 可取 | （空白） | **空白** | — | — |
| stop/restart/终态 | （空白） | **空白** | — | service 单元 |
| 页面 | web/e2e Trading 段 + tests/e2e v015 | L0 | t=10 num=1 | — |

### 链 H 实盘链（豁免）/ 链 I 发现预测链（豁免）/ 链 J 配置链

| 链 | 覆盖 | 强度 | 处置 |
|---|---|---|---|
| H | live/ 11 文件（api MOCK/executor/oms/gateway 单元级较厚） | 单元级 | 本批豁免；实盘接入独立容器（规划 v2 已冻结） |
| I | prediction/ 7 文件单元级 | 单元级 | 本批豁免；登记独立跟进 |
| J | infra/test_b9_s1_credentials.py | 单元级 L2 旁证（掩码+PUT 哨兵） | 豁免（已够用，B9 刚验证） |

## 4. G-2 实测修正

预估「链路层 L2 覆盖率≈0」→ **实测成立，且更精确**：

1. 链路级驱动骨架**已存在**：tests/api/（TestClient 真栈真 DB，api_client fixture 注入 tmp db+data_dir，无 fake runner）——非从零开始
2. 但两处结构性缺陷：
   - **核心计算交界被 mock**：backtest 引擎/评分/import 函数在路由模块处 patch——断言是「mock 进 mock 出」的管道往返（L1），从不验证计算
   - **多数链路无端点级覆盖**：E/G 链端点级空白，F 链仅冒烟，D 链只有创建
3. 全 10 链**无一格**满足 L2（零核心 mock + 数值跨步自洽）

## 5. Phase 1 补测清单（0.3 冻结）

### 5.1 销项清单（按 空白>L0>L1 × 链路价值 排序）

| 序 | 链路 | 起点 | 目标 | 关键断言（L2 内涵） | 对应任务 |
|---|---|---|---|---|---|
| 1 | A 数据导入 | 空白×3+L1×3 | 全链 L2 | 落盘 parquet 数值=源 CSV 数值；meta 行=导入参数；列表含新集且计数+1；preview 数值=落盘数值 | 3.1 |
| 2 | B 单回测（含 C 入口） | 空白×2+L1+L0 | 全链 L2 | 指标=裁判判定值（组 1）；compare 入口同值（四入口全等的前两入口） | 3.2 |
| 3 | D 进化闭环 | 空白×5+L1+L0 | 创建→终态 L2 | 状态机迁移=触发动作；代际事件序完整无丢序；冠军=评分极值；终态后 history 可取 | 3.3 |
| 4 | E 验证 | 空白端点×3 | 全链 L2 | 会话→分段→汇总数值自洽；与单回测口径 G-1 裁决落地 | 3.4 |
| 5 | F 批量 | L0 | L2 | stream 序列完整（progress 次=total）；汇总=逐段聚合 | 3.4 |
| 6 | G 模拟盘 | 空白×4 | 全链 L2 | bar 推进零重复事件（B1.3 实战）；权益守恒恒等式；metrics=equity 派生 | 3.5 |

### 5.2 显式豁免（终态记录）

| 项 | 理由 | 跟进 |
|---|---|---|
| H 实盘链 | 实盘接入独立容器（规划 v2 冻结，触及红线待启动） | 独立容器 |
| I 发现/预测/validate 链 | 非核心量化闭环主路径；prediction 域单元层已厚 | 登记独立跟进 |
| J 配置链 | B9 刚完成 S1 验证（单元 L2 旁证） | 无 |
| /api/data/ohlcv、/api/data/chart-indicators | 重复能力/衍生旁支 | 无 |
| web/e2e 既有 14 条 | L0 保留为冒烟门；L2 由 Phase 3 闭环升级 | 4.x |

---
*矩阵为容器期工件：组 3 完成后逐格回填终态（3.6），随后归档。*
