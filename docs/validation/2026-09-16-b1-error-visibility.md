# B1 吞错误导止血 — 修复前后行为对照（evidence）

容器：`refactor-myquant-backend-b1-b11` · 组 B1 · tag BE1 · 2026-09-16
验证：后端全量 pytest 2170 passed（基线 2158 + 新增复现测试 12），`--ignore=tests/e2e` 口径（e2e 为 `pytest_e2e.ini` 独立口径，需前后端服务）。
复现测试集中：`tests/test_b1_swallowed_errors.py`（红→绿）。

## 五处修复前后对照

| # | 锚点 | 修复前（症状） | 修复后（行为） |
|---|---|---|---|
| 1.1 | core/evolution/evaluation.py `build_requirements`（:41-44） | requirements_json 解析失败静默回退 `objective="sharpe"`——进化朝错误目标优化且用户无感知 | `logger.error`（含 task_id、原文、原因链）+ `raise ValueError from e`；runner 路径冒泡至 `_run_task` 兜底 → 任务 `stopped/error`；评估路径由 evaluate_dna 兜底记 0 分 + error 标记。`api/runner.py:_build_requirements` 为一行委托转发，同语义透传 |
| 1.2 | api/runner.py 快照写（:548-560） | `save_snapshot` 失败 `except: pass`——每代种群快照静默缺失 | `logger.error`（含 task_id、gen、原因链），进化继续（非关键路径降级可见） |
| 1.3 | core/trading/runner.py bar 循环（:376-408） | 游标在全部 bar 处理完后才前移——中途异常后下轮 poll 重放全部新 bar，paper_trade 事件重复落库 | ① 事件落库成功即 `df = new_df.iloc[:i+1]`（逐 bar 推进，重试只覆盖未完成 bar）；② paper_trade 加 `UNIQUE(task_id, bar_time, action)` + `INSERT OR IGNORE` + 存量去重迁移（保留最早行）——幂等兜底 |
| 1.4 | api/routes/strategies.py `mark_failed`（:1098-1105） | 失败状态写入自身失败 `except: pass`——session 永久卡 running 且无排查线索 | `logger.error`（含 session_id、原始错误、原因链）；不重抛（调用方已在异常处理路径） |
| 1.5 | api/routes/data.py 图表指标（:579-678） | 六处 `except: pass`（BOLL/RSI/RVOL/VWMA/MACD/KDJ）——图表层静默消失，「未开启」与「计算失败」不可区分 | 每层失败 `logger.warning`（含指标名、symbol/timeframe、原因链），该层缺失但其他层不受影响。EMA 的 `except: continue`（:552，锚点区间外同函数同类）一并修复——共七处 |

## 测试重锚定

- `tests/test_runner_requirements.py::test_malformed_json_falls_back` → `test_malformed_json_raises`：该测试锚定的「malformed → 静默 fallback」正是 B1.1 要消灭的错误行为，重锚定为 `pytest.raises(ValueError)`。

## 判定口径备注

- 重放根因边界：进程重启走 crash-out（既有裁决「crash is a bug, not a state to recover from」，runner.py:314-325），不产生重放；重放窗口仅为进程内异常后循环继续——B1.3 的逐 bar 推进 + 幂等键覆盖该窗口。
- DB 游标不做即时持久化：crash 路径无恢复消费者（重启即 crash-out），轮内异常由内存游标接管。
- 全量跑中 64 个 e2e 失败 + 2 个 TestWSPushReliability 失败均与 B1 无关：e2e 需前后端服务（独立口径）；WS 测试失败系 e2e 失败泄漏的全局状态干扰，排除 e2e 后同批测试全绿（已双向验证组合跑绿）。
