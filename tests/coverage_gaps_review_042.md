# review-042 阶段四覆盖率测量与盲区清单（步骤 1 产出）

测量形制：全量 tests/（5473 passed，1590.61s）+ coverage statement 模式，
范围=变更域三包（core/api/mcpserver）中 0.4.2 审查范围与阶段三 10 卡
触碰模块。变更域总计 9647 stmts / 457 miss / **95%**（44 文件全覆盖跳过）。

测量坑位记录：仓库根残留旧 `.coverage`（branch 模式数据）与本次 statement
数据 combine 冲突（coverage INTERNALERROR "Can't combine statement coverage
data with branch data"），报告由本次 pid 数据文件直接生成——复跑时先清
`.coverage*` 或统一 branch 开关。

## A 类：该补用例（步骤 2 执行清单）

| # | 盲区 | 证据 | 补测方向 |
|---|---|---|---|
| A-1 | API 层组键连接处零覆盖：`api/routes/research/hypotheses.py:101`（组键带值并入 conditions 的行）从未执行，tests/api/ 对三个组键 grep 零命中 | Miss 101 | API 端到端：POST 带三组键 → conditions 落库含组键+翻译产三角色基因；POST 不带组键 → 同 conditions 幂等命中（None 省略指纹连续） |
| A-2 | 声明块未知键拒绝分支无锚：`hypothesis_service.py:149`（unknown 键 422）——4.1 三键扩白名单时原「未知键拒」断言退役翻转，此后无直接锚 | Miss 149 | service 直调 `{"foo": 1}` → 422 RESEARCH_PARAM_INVALID（field=foo） |
| A-3 | `parse_parent_ids` 四分支无直接单元锚（CARD-06 单点化产物，现仅经 list_strategies_by_parent_hypotheses/_build_lineage 两消费方间接锚定） | 新函数无直测 | unit 直调四分支：JSON 数组/裸串/None 空/单值 JSON 包装 |

## B 类：低优先记录（存量域或防御纵深，不在步骤 2 强制范围）

- `live_trading/executor.py:150-151,155-156`：挂 SL/TP 成功后 pos 回写分支（测试路径 state.position 为 None 时跳过）——挂单成功回写锚候选
- `hypothesis_service.py:131`（strategy_params 非 dict 纵深拒——API 层 pydantic 已挡）、`:183`（take_profit 独立越界分支）、`:484-485`（幂等存量坏 JSON 容错回退）
- `strategy/validator.py:89,107,131,338,341`：进化/手工 DNA 形态进阶校验分支（mixed 方向缺 DIRECTION gene / layer 缺 entry / role_reversal 缺 role / wick_touch 缺 direction——存量域）
- `backtest/engine.py:198-205,248-251,296-302`：funding 循环 in-position 分支与杠杆权益后处理的未平仓/空区间边角（存量域）
- `trading/replay.py:154-163`：回放主循环事件统计段（events_log/fill_rate——测试路径 events 为空时循环体不执行）
- `hypothesis_to_strategy.py:187-188,467-468`：声明块/dna_json 坏 JSON 容错回退（防御性）

## C 类：不可达/工具性盲区（记录不补）

- `backtest/engine.py:341-345,395-689`：`pre_sim_func_nb`/`order_func_nb` 主体——numba JIT 编译执行不经 Python 行覆盖（工具局限非测试盲区）；其行为已由 test_sl_modes 全部手工核算断言（触发 bar 定位/权益终值/多空对称）从执行结果外部锚定
- `hypothesis_to_strategy.py:236`：插件列组防御性收口 raise——代码注释自证「结构上不可达（插件列在非 ENTRY 角色已被拒）」
- `trading/runner.py` 83%（58 miss：264-278/326-348/623-642 等）：线程框架的循环/控制器/异常通道——mock 线程无判定价值，靠 integration 与运行时通道承载
- `trading/replay.py:15,18-28`：模块 docstring 示例行与 import 常量行

## 层级分布核对（步骤 5 前置数据）

0.4.2 新测试资产层级：unit 1 文件（test_param_domain_matrix）、
integration 9 文件、e2e（工程形制的 system 对应物）0 增量。工程 tests/
全库无 `pytest.mark.system` 标记先例——层级形制为 unit/integration +
tests/mcpserver/test_e2e_*（经 MCP 公开入口）。「MCP 工具 → 服务端」连接
由 schema 对拍（形制）+ tools_passthrough（body 组装行为）+ API 全链
（服务端到达）三层锚定，判定 0.4.2 不新增 e2e（形制重、断言面已被
三层承载）。
