# 验证报告 — G1 契约生成（refactor-myquant-frontend-f1-f7 阶段 A）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G1_

## 生成链

- 命令：`npm run generate:types`（web/）
- 链路：`scripts/export_openapi.py`（离线 `create_app().openapi()`，不启动 uvicorn、不触发 lifespan）→ `web/openapi.json`（入库，50 路径/76 模型）→ `openapi-typescript 7.13.0` → `src/types/generated/api.ts`（5127 行，@generated banner 由 perl 后处理插入）
- 幂等验证：连续两次生成 `md5` 对比，openapi.json 与 api.ts 双产物零 diff ✓
- spec 对应：`api-contract-derivation`（生成/禁手改/单源三个 Requirement 的 Scenario 全通过）

## 依赖准入记录（openapi-typescript）

- ERESOLVE：openapi-typescript@7.13.0 peer `typescript@^5.x` vs 项目 TS 6.0.3（上游未跟进）
- 判定：纯构建期 CLI（读 json 产 .ts 文本），peer 约束其库消费场景，不影响 CLI 生成；以 `--legacy-peer-deps` 安装，不动全项目 TS 版本；G3 迁 pnpm 后 peer 冲突降级为 warning 自然消解

## drift 清单（别名层落地暴露，逐项按后端真实形态修正）

**类型收窄断言**（后端声明可选/宽 string，实际恒有/恒枚举；注释逐一标注在 types/api.ts）：
- `DNA.signal_genes/logic_genes/risk_genes/execution_genes`（后端可选 → 收窄必填）
- `StrategyMetrics` 6 字段（后端可空 → 必填；metrics 挂载即全量）
- `TradingTask` 12 个可空字段（后端可选 → 必填可空）
- `EvolutionTask.status/mode/direction`、`LogicGenes.logic`、`RiskGenes.direction/sl_mode`、`ConditionInput.logic`、`RuleCondition.logic`（string → 判别联合）
- `Strategy.dna/metrics`、各 List 响应 `items`（元素收窄为域类型）

**dict 洞补形**（后端 `list[dict]`/`dict` 无元素 schema，按实际 JSON 形状收窄，后端补子模型后应改回纯别名）：
- `BacktestResponse.equity_curve/signals`、`ValidateResponse.distribution/concentration/extremes`、`OhlcvResponse.data`、`DNAModel.layers`、`EvolutionTaskResponse.champion_metrics/dimension_scores`

**无 schema 端点/通道（类型保留手写并注明来源）**：
- `/api/evolution/strategies`（DiscoveredStrategy）、`/api/strategies/batch-backtest/{id}`（BatchBacktest*）、`/api/data/chart-indicators`（ChartIndicatorsResponse）、`/api/trading/runner-status`（TradingRunnerStatus）——后端未声明 response_model
- WS 推送（GenerationUpdate/MutationRecord/EvolvedStrategy）——G2 事件契约冻结时收编 zod
- 纯前端类型（ChartIndicatorConfig/PaginatedResponse）——注释豁免

**消费处修正**（drift 修正实际触及）：
- `ProgressPanel.tsx`：删 `population_count` 死分支（后端契约无此字段，`>1` 条件恒 false——类型幻觉清除）
- `MetricsDashboard.tsx`：MetricsData 手写 9 字段子集 → TradingMetrics（生成源）
- `Verify.tsx`：HistoryView props 手写形状 → API 类型；describeCondition 签名收 null
- `TaskDetailDrawer.tsx`：best_fitness null 适配
- 测试构造补全（后端 default 必填字段）：Evolution.test（elite_ratio 等 8 字段）、Strategies.test（verify_count）、Trading.test/useTrading.test（execution_model）、fixtures（realized_pnl/unrealized_pnl）

**环境修复（npm 副作用，非任务内代码）**：
- `@testing-library/dom` 原为隐性传递依赖，install 修剪导致 `@testing-library/react` 类型断链（18 处测试编译错误）→ 显式声明为 devDependency

## knip 基线

- 配置：`web/knip.jsonc`（ignore 6 文件 + ignoreDependencies 4 + ignoreBinaries perl）
- 存量快照：`docs/validation/evidence-knip-baseline-g1.txt`（unused exports 37）
- 验证门判定：此后每组 `npx knip` 输出对比快照，无新增白名单外条目

## 四件套

| 项 | 结果 |
|---|---|
| `npx tsc -b` | 0 错误 |
| `npm run build` | ✓ built（319ms） |
| `npx vitest run` | 24 文件 222 全绿（229 − 删除的伪契约测试 7） |
| `npm run lint` | 78 errors（=F0 基线，零新增） |
| `npx knip` | 与基线快照一致，无新增 |
