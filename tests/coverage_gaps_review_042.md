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

## 步骤 3：测试资产 V3 化核对结论（0.4.2 全部 10 文件）

核对对象：7 新测试文件 + 2 卡配套文件（null_params/failclose）+ 本阶段
补齐用例。逐项对照 TESTING_V3 §二/§三：

| 核对项 | 结论 |
|---|---|
| 组织落位（域目录+能力命名） | 合规——strategy/backtest/trading/analysis/mcpserver/persistence 各域 |
| pytestmark 层级声明 | 合规——10/10 文件声明（unit 1/integration 9） |
| 命名形制（行为命名） | 合规——test_<行为>_<场景>（_<预期>）全库抽查无违例 |
| 参数化可读 id（中文） | 合规——同值异判/装载校验/模式×链路矩阵等 8 处 |
| 直线结构（准备→执行→断言） | 合规——无分支堆叠测试 |
| fixture 最近公共 | 合规——tmp_data_dir/api_client 在根 conftest；db 各文件自带给（域内既有惯例，tests/analysis 5+ 文件同形制，不做集中化重构——修复期不动既有形制） |
| 断言形制 | 合规——浮点用 1e-6/1e-9 直接比较（域惯例优先于 pytest.approx 统一） |
| 恒真断言 | 已清零——阶段一 2 处已修（提交 6b37de7） |
| 期望值独立性 | 抽检合规——test_sl_modes 全手工核算（注释附推导）；卡配套 2 文件期望值独立（0.02=10000×0.1/50000 等人工核算）；未见期望值取自被测实现 |

## 步骤 4：属性补强核对结论

既有属性/不变量锚盘点：同值异判参数化（param_domain_matrix 8 例 +
exit_sweep 9 例）、序列化往返不变量（roundtrip_zero_migration）、批量
与单路径等价（batch 双锚）、三入口等价（test_three_entries_adds_
consistent）、幂等族（登记/翻译/分流多处）、模式×链路 9 格矩阵。
本阶段补强：parse_parent_ids 四分支（A-3）。结论：属性面充分，
无新增属性补强项；A-2 重建了未知键拒绝直锚（退役断言回补）。

## 步骤 5：层级补全核对结论

层级分布数据见上文。补充判定：validator/pydantic 两面直测由唯一 unit
文件承载（param_domain_matrix）——unit 面责任充分；integration 承载
协作与全链（登记→翻译→回测→拒绝面 DoD 链）；e2e 不新增（判定依据
见上）。结论：层级分布符合工程形制，无层级缺口。

