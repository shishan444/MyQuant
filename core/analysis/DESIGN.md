# core/analysis 域文档（add-ai-research-mcp）

> 域语义权威：设计卡（幂等/并发/事务用例深度）随代码演进；能力契约
> （长期归档）在 openspec specs。两层互补不重复。

## 词汇索引

| 词汇 | 含义 |
|---|---|
| 判定并集 judged_ranges | 假设自报 data_ranges ∪ 其全部研究日志区间（漏报被日志拦截） |
| warmup 推导式 | `max(200, 策略用到指标的最大周期 × 3)`（D5；lookback 依据在 registry_meta） |
| 幂等键（假设） | `(conditions_hash, ranges_hash)` 且 status='active'（部分唯一索引承载） |
| 幂等键（验证） | `(hypothesis_id, strategy_id, range_hash)`（唯一索引承载；随机窗口含 seed） |
| 分量证据 | level='component' 的验证记录（params.column=插件列名），组合策略加严门输入 |
| 退化形态 | 无 signal_sources/composition 的存量 DNA——走原路径，行为逐位一致 |
| walk-forward | 滚动拟合逐段预测，产出全序列样本外信号（模型信号唯一生成路径） |

## 不变量

- **INV-R1** 假设区间登记后不可变；条件集与验证证据绑定（验证的是确切
  条件，非陈述措辞）。
- **INV-R2** 假设终态（supported/refuted/superseded）不得再验证或迁移；
  迁移必带真实证据引用。
- **INV-R3** 判定并集宁可多拒不可漏拦（自报∪日志，强制自动记录）。
- **INV-R4** 公式白名单封闭；列名必须来自可用信号集；shift 仅非负常量。
- **INV-R5** 模型管道时序切分内置：训练不含未来，特征只用 t-1 及以前。

## 设计卡

### 卡1 register_hypothesis
- 调用者：AI（经 MCP 研究执行档）；意图：登记假设，取得隔离验证资格
- 前置：conditions 为合法 WHEN/THEN 结构（subject 在可用信号集——含
  指标输出列形制 `前缀_数字` 与插件列）；区间合法且 symbol/timeframe
  在数据目录
- 幂等：`(conditions_hash, ranges_hash)` 且 active——同条件不同措辞=
  同一假设；重复返回既有 id（duplicated）
- 并发：部分唯一索引先到者胜，冲突转重查返回
- 失败：422 RESEARCH_CONDITION_INVALID / RANGE_INVALID / SYMBOL_NOT_FOUND
- 双写：假设+register 日志同一事务（repository 组合函数）

### 卡2 transition_hypothesis
- 前置：假设存在；evidence_ref 真实（supported/refuted→验证记录；
  superseded→接替假设 id）
- 状态机：active→supported(ref=pass)/refuted(ref=fail)/superseded(ref=新id)；
  终态拒绝
- 幂等：同目标+同 ref → 幂等返回不重复写日志
- 并发：条件更新 WHERE status='active'，rowcount 0 → 重查判读

### 卡3 run_isolated_validation
- 前置：假设 active；请求区间与判定并集无交；warmup 可得；组合策略
  分量证据齐备（前置检查，先于回测执行）
- 判定流：终态拒 → 幂等 → 并集求交（409 附 judged_ranges+overlaps）→
  分量证据（409 附缺失清单）→ warmup 推导（不足 422）→ 前扩装载算
  指标切回请求区间回测 → 门槛机械判定（组合用收紧 profile）→ 双落
- 幂等：`(hypothesis_id, strategy_id, range_hash)`

### 卡4 register_formula_plugin
- 前置：静态校验三关（ast 白名单/列引用/负向 shift）通过
- 幂等：同名同表达式且 valid → 返回既有版本；同名新表达式 → 版本递增
- rejected：只留插件记录（reject_reason），不进 signal_registry
- 双写：插件+注册+日志同一事务

### 卡5 register_model_plugin
- 前置：配置边界（模板/参数/目标）；特征列在可用信号集
- 状态机：pending → valid（训练成功+注册）| rejected（训练失败/超时，
  原因落库不静默丢失）
- 异步：202+任务句柄（线程池执行器），轮询 GET /analysis/tasks/{id}
- 幂等：(name, config_hash) 且 valid → 返回既有
- 防作弊：信号唯一生成路径是 fit_predict_walk_forward（INV-R5）

### 卡6 translate_hypothesis
- 前置：假设存在；存在 verdict=pass 的验证记录（信任链不跳级）；
  条件在可表达子集（指标列 subject + 阈值/穿越 action + 数值 target）
- 拒绝：跨币种 / 基础列或插件列 subject / 分布形态 action（附原因+
  慢通道建议）
- 幂等：谱系 parent_ids 含该假设的既有策略 → 返回既有 id
- 翻译源：conditions JSON 唯一（statement 不参与）

## 依赖方向（D13）

```
mcpserver/ ──> api/routes/research/ ──> core/analysis ──> core/signals
                                         |      ^              ^
                                         |      (禁：core 不得 |
                                         v       反向依赖)     |
                                   features/discovery/        |
                                   validation/strategy        |
                                         ^                     |
                                         └── strategy ─────────┘ (类型引用)
```

- backtest 零新增依赖：插件信号经组装器产 SignalSet，走引擎现成
  `signal_set=` 注入点（tests/analysis/test_strategy_v2.py 静态断言钉死）。
- 执行模型：CPU 密集计算走 ResearchExecutor 线程池（快路径 <1s 同步；
  守护超时先到者胜）；API 启动回收孤儿 running → failed(interrupted)。

## 事务与恢复

- 双写原子：save_hypothesis_with_log / save_validation_with_log /
  save_plugin_with_registry / register_existing_plugin_signal——每个组合
  函数一个 engine.begin 覆盖双表（原子性测试在
  tests/persistence/test_research_repository.py）。
- 任务状态构词：pending|running|succeeded|failed（timeout/interrupted
  为 failed 的 reason）。
- schema 自愈：ensure_research_tables（幂等 create_all 仅研究域八表；
  schema 演进唯一轨仍是 Alembic）。
