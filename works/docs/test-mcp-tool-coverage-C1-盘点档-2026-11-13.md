# test-mcp-tool-coverage C-1 测量面扩展与缺口盘点档（2026-11-13）

> 大白话总起：本次要做的事是把 mcpserver 包纳入 pytest 覆盖率测量面，并盘点 42 个 MCP 工具注册分支的测试触达缺口。盘点结论出乎任务书预期——测量面纳入后既有测试已把 mcpserver/tools.py 行级覆盖拉到 100%（0 missing 行），42 个工具全部已有真实调用与专属行为断言；实测差集只剩 2 处分支缺口（branch partial）。任务书预判的「5 个零触达必补」工具，实测已被 tests/api/test_research_tooling.py 的既有用例覆盖，不需要重复补。需要拍板的问题在文末。

## 一、测量面扩展（C-1 动作 1）

- pytest.ini addopts 由 `--cov=core --cov=api` 增列 `--cov=mcpserver`（全行：`-v --tb=short --cov=core --cov=api --cov=mcpserver --cov-report=term-missing --cov-branch --cov-fail-under=96`）。
- `--cov-fail-under=96` 未动（上位约束：下调无用户授权）。

## 二、局部基线实测（C-1 动作 2）

跑法：mcpserver 触达面全集 50 个测试文件（`grep -rln 'mcpserver' tests/` 枚举，排除 conftest 与下划线非测试文件），两种口径各一次，证据落 `/tmp/mcp-c1-local-baseline.log`（行级）与 `/tmp/mcp-c1-local-baseline-branch.log`（分支级，与全局 addopts 同口径含 `--cov-branch`）。

结果（两口径均 852 passed, 1 skipped）：

| 文件 | Stmts | Miss | Branch | BrPart | 缺口 |
|---|---|---|---|---|---|
| mcpserver/tools.py（本 change 范围） | 348 | **0** | 122 | **2** | `259->261`、`718->720` |
| mcpserver/server.py（范围外） | 49 | 1 | 12 | 1 | :102（`__main__` 入口行，非两注册函数面） |
| mcpserver/api_client.py / errors.py / capability.py | 133 | 0 | 46 | 0 | 无 |

两处 branch partial 归属：

1. `259->261`＝`list_paper_tasks` 的 `if status:` 缺省侧（不传 status 时不带键）——readonly 域，C-2 补。
2. `718->720`＝`query_indicator` 的 `if indicator is not None:` 缺省侧（不传 indicator 时不带键，走 groups 互斥透传面）——research 域，C-3 补。

## 三、42 工具触达与断言归属逐名盘点（C-1 动作 3）

行级 0 missing 的成因逐名核对如下——每工具至少一条专属行为断言（端点/参数/返回处理，非「调用不炸」）均已存在：

### readonly 19

| # | 工具 | 专属行为断言归属 | 断言内容 |
|---|---|---|---|
| 1 | list_data_sources | test_server TestToolBodyInvocation + test_return_contract | GET /api/data/available-sources 端点＋透传返回形制 |
| 2 | list_strategies | 同上 + test_return_contract dict_items_form | 端点＋_fold_strategy_list 折叠后 items/total 形制 |
| 3 | get_strategy | test_server CASES + TestPathSegmentEncoding | 端点＋id quote 编码单段（注入形 a/b c→%2F%20） |
| 4 | list_evolution_tasks | test_server CASES + test_return_contract | 端点＋透传形制 |
| 5 | list_indicators | test_server CASES + test_return_contract + error_channel | 端点＋透传＋ApiError 转 {"error":...} |
| 6 | list_signal_columns | test_server CASES + test_return_contract | 端点＋透传形制 |
| 7 | get_market_snapshot | test_server CASES + test_errors:289-310 | 端点＋错误通道 |
| 8 | union_balance | test_tools_passthrough（GET 三必填参数透传） | 端点＋三参数查询串全等断言 |
| 9 | list_hypotheses | test_server CASES（含 status/cursor）+ test_return_contract | 端点＋可选参数＋透传 |
| 10 | get_hypothesis | test_server CASES + test_e2e_agent_loop:145 | 端点 |
| 11 | get_research_log | test_server CASES + test_page_parity s4c | 端点＋分页参数面 |
| 12 | get_analysis_task | test_server CASES + TestPathSegmentEncoding | 端点＋合法 hex id 编码零变化 |
| 13 | get_validation_report | test_server CASES | 端点 |
| 14 | list_feature_requests | test_server TestToolPaginationClamp + CASES + :180-183 | 端点＋page/page_size clamp 上下限＋合法值透传 |
| 15 | list_paper_tasks | test_paper_tools:262/:286 | limit 100 缺省 clamp 至 50＋params 组装（status 侧已有） |
| 16 | get_paper_task | test_paper_tools | 端点/参数 |
| 17 | get_paper_report | test_paper_tools | final True/False 两路路径分派 |
| 18 | get_paper_history | test_paper_tools | fills/equity/funding 三路分派＋非法 type 结构化拒绝 |
| 19 | query_paper_signals | test_paper_tools:408 + 参数面 parity | limit 钳制 500（REST le=500 同域）＋七参数面与 REST 同构 |

### research 23

| # | 工具 | 专属行为断言归属 | 断言内容 |
|---|---|---|---|
| 1 | run_analysis | test_server CASES + test_precheck_mcp_contract | POST /api/research/analysis 端点 |
| 2 | inspect_cases | test_server CASES | 端点 |
| 3 | query_indicator | test_server CASES | 端点（indicator 缺省侧分支 C-3 补） |
| 4 | register_hypothesis | test_tools_passthrough（7 处）+ CASES | body 组装：可选键带值透传/None 省略（trigger_policy、strategy_params、分批三组） |
| 5 | transition_hypothesis | test_server CASES + TestPathSegmentEncoding + e2e_agent_loop | 端点＋路径编码＋e2e 迁移链 |
| 6 | run_isolated_validation | test_tools_passthrough（body 全等）+ CASES | 请求体逐键全等断言 |
| 7 | run_random_window_test | test_tools_passthrough + CASES | 端点＋window_bars/seed/hypothesis_id body 键 |
| 8 | run_staged_validation | test_tools_passthrough + CASES | 端点＋segmentation/segments body 键 |
| 9 | run_backtest | test_server CASES + test_task_cap_mcp | 端点＋任务级封顶参数面 |
| 10 | register_formula_plugin | test_server CASES | 端点 |
| 11 | register_model_plugin | test_server CASES | 端点 |
| 12 | translate_hypothesis_to_strategy | test_server CASES + e2e_agent_loop:114 | 端点＋e2e 翻译链 |
| 13 | submit_feature_request | test_server CASES | 端点 |
| 14 | trigger_evolution | test_server CASES | 端点 |
| 15 | poll_evolution_task | test_server TestPollTimeoutClamp + CASES + TestPathSegmentEncoding | total_timeout 钳制 [1,3600] 四组值＋端点＋路径编码 |
| 16 | stop_evolution_task | tests/api/test_research_tooling（404/409）+ tests/evolution/test_concurrency_model:196 | 错误码透传＋协作停止 |
| 17 | pause_evolution_task | tests/api/test_research_tooling（非 running 409） | 错误码透传 |
| 18 | resume_evolution_task | tests/api/test_research_tooling（非 paused 400） | 错误码透传（与 stop/pause 的 409 分立） |
| 19 | get_evolution_strategies | tests/api/test_research_tooling（快照两态） | running 读快照 top10／无 champion 条目两态 |
| 20 | update_strategy_metadata | tests/api/test_research_tooling（tags 直通+部分更新） | tags 字符串形制不拆数组＋不携带键不动 |
| 21 | batch_delete_strategies | tests/api/test_research_tooling（confirm 契约） | confirm 布尔契约＋幂等 |
| 22 | mount_paper_task | test_paper_tools（window 三入口+互斥） | window_unlimited→null 转译＋互斥 MCP_PARAM_INVALID 不触 REST |
| 23 | unmount_paper_task | test_paper_tools | 端点（stop 语义） |

## 四、与任务书预期的差异（实测推翻预判，按「实测差集为补齐范围」执行）

任务书预判「5 个工具名在全部 mcpserver 测试零出现」为必补项（batch_delete_strategies、get_evolution_strategies、pause/resume/stop_evolution_task）。实测：该 5 项在 `tests/api/test_research_tooling.py`（+`tests/evolution/test_concurrency_model.py`）已有直调闭包与行为断言（见上表 #16-#21），此前不可见只因双重盲区——调研 grep 限定 tests/mcpserver 目录＋覆盖率测量面不含 mcpserver。测量面纳入后这些用例即计入覆盖。

处理：按 tasks.md C-1 完成标准「实测差集为补齐范围」与 proposal 上位约束「零重复建设」，不在 tests/mcpserver 重复补该 5 项；C-2/C-3 补齐范围＝2 处 branch partial 对应用例＋逐名打勾核对。

## 五、补齐范围汇总（C-2/C-3 输入）

- C-2（readonly 域）：补 `list_paper_tasks` 不传 status 的缺省侧用例 1 条（断言 params 不含 status 键）。
- C-3（research 域）：补 `query_indicator` 不传 indicator 的缺省侧用例 1 条（断言 body 不含 indicator 键——groups 互斥透传面同一行为族）。
- 两用例形制沿 conftest `_FakeMcp`+`RecordingFakeApi`/`_FakeApi` 捕获直调先例（test_paper_tools.py:441 / test_tools_passthrough）。

## 六、全量三并发 96 闸门状态（C-1 动作 4）

命令：`./venv/bin/python -m pytest --ignore=tests/e2e --cov-report=json:coverage.json -q -n 3`（正式 addopts，含 mcpserver）。

结果（证据 `/tmp/mcp-c1-fullrun.log`，2026-11-13 08:26-08:37，665.87s）：

- **全局 coverage 96.56% ≥96——闸门通过**（`Required test coverage of 96% reached. Total coverage: 96.56%`），mcpserver 纳入后未跌破。
- 1 failed, 9282 passed, 2 skipped。唯一 failed＝`tests/evolution/test_concurrency_model.py::TestPopulationOffsetFix::test_core_gen_continuity_and_no_regression`（POST /api/evolution/tasks/oc1/stop 409≠200）——**单进程复跑通过**（`-o addopts=""` 定向，1 passed，证据 `/tmp/mcp-c1-failed-repro.log`），判定为 xdist 三并发下偶发（并发不兼容），非本 change 引入（本 change diff 仅 pytest.ini 测量面一行，不触碰任何端点行为）。

## 七、C-2/C-3 补齐与逐名打勾（2026-11-13 终验）

补齐用例 2 条（实测差集全消）：

1. `test_paper_tools.py` `TestToolBehavior.test_list_paper_tasks_status_omitted`——list_paper_tasks 不传 status，断言 params == {"limit": 20, "offset": 0}（无 status 键）。
2. `test_tools_passthrough.py` `TestQueryIndicatorBareBody.test_no_optional_keys_bare_body`——query_indicator 只传 symbol/timeframe，断言 body 全等两键（indicator/groups 等可选键缺省不带）。

局部终验（触达面 50 文件，`--cov-branch` 口径，证据 `/tmp/mcp-c23-local.log`）：**mcpserver/tools.py 348 stmts / 0 miss / 122 branch / 0 partial / 100%**，854 passed（+2 新用例）；装配级守卫（test_server / test_stdio_l3 / test_http_l3）全绿。验收行级判定达成：两注册函数及嵌套工具定义行无 missing，分支 partial 清零。

逐名打勾（断言归属详见第三节表）：

- readonly 19：list_data_sources ✓ list_strategies ✓ get_strategy ✓ list_evolution_tasks ✓ list_indicators ✓ list_signal_columns ✓ get_market_snapshot ✓ union_balance ✓ list_hypotheses ✓ get_hypothesis ✓ get_research_log ✓ get_analysis_task ✓ get_validation_report ✓ list_feature_requests ✓ list_paper_tasks ✓（含本次补 status 缺省侧）get_paper_task ✓ get_paper_report ✓ get_paper_history ✓ query_paper_signals ✓
- research 23：run_analysis ✓ inspect_cases ✓ query_indicator ✓（含本次补 indicator 缺省侧）register_hypothesis ✓ transition_hypothesis ✓ run_isolated_validation ✓ run_random_window_test ✓ run_staged_validation ✓ run_backtest ✓ register_formula_plugin ✓ register_model_plugin ✓ translate_hypothesis_to_strategy ✓ submit_feature_request ✓ trigger_evolution ✓ poll_evolution_task ✓ stop_evolution_task ✓ pause_evolution_task ✓ resume_evolution_task ✓ get_evolution_strategies ✓ update_strategy_metadata ✓ batch_delete_strategies ✓ mount_paper_task ✓ unmount_paper_task ✓

## 八、待拍板

1. 5 必补项按实测差集不重复补（既有 tests/api 用例已承载）——若评审口径要求该 5 项必须在 tests/mcpserver 目录内有用例，需另裁决并说明重复建设的必要性。
2. server.py:102（`__main__` 入口行）missing 在本 change 范围外（两注册函数与嵌套工具定义均在 tools.py），不处理。
