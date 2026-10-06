# MyQuant 修复 MCP 版本规划（0.3.3-MCP修复 系列）

> 制定日期：2026-10-03（同日完成验真审计——全部条目代码级核证为真后定稿）
> 编号重构：2026-10-03 应用户裁决并入 0.3.x 统一版本线——原 MCP-2.1/2.2/2.3/2.4 重编为 **0.3.2/0.3.3/0.3.4/0.3.5**；债务清偿三战（trading+live_trading / persistence / evolution+validation）由原 0.3.2/0.3.3/0.3.4 顺延为 **0.3.6/0.3.7/0.3.8**（0.3.1 api 硬化已合并不变）。此后全工程单一版本序列，引用一律用 0.3.x 编号
> **0.3.2 完成（2026-10-05）**：合并 main **0b3cf1f**（--no-ff，14 提交）+ team3 第七战审查增量（11 卡：P0×3 真缺陷+P1×8，含小样本族收编 422 用户裁决）+ openspec 归档（`archive/2026-10-04-fix-mcp-error-channel`）+ 主 spec `research-error-channel` 同步落库
> **规划重构（2026-10-05）**：0.3.2 收口为战报档案、0.3.3 展开为当前焦点——openspec change **mcp-contract-transparency** 四工件定稿（三轮外部评估收口，四次裁决落痕见 0.3.3 节）
> **0.3.3 完成（2026-10-05）**：合并 main **257c044**（--no-ff，18 提交=实施 5+team3 第八战 13）——team3 审查 5 卡 P1 全闭环（naive 时间裸 500 回归/衍生品 gap 前扩误报/warmup 折算口径/n_significant 封顶/非法时间串 422 化），终验全量 4478 passed+e2e 64+触碰面覆盖率 100% 行+分支；TD-1 死测试退役微分支 **0ff3aa0**；债务六笔落位后续债务区；openspec change 待归档
> 来源：works/设计稿/MyQuant-技术债务登记.md G 组 14 项（两轮六步法全链实战产出）+ 验真连带 3 项
> 验真基线：2026-10-03 验真审计逐项代码级核证；0.3.3 各锚点于 2026-10-04~05 经三轮外部评估独立复验修正后定稿（锚点表见 0.3.3 节）
> 与姊妹篇关系：MyQuant-技术债务登记.md 记「有什么债」；本篇记 MCP 域「怎么修、怎么升」——**只做 MCP 相关能力**（mcpserver + api/routes/research + core/analysis 域），与债务清偿三战（0.3.6-0.3.8）分属两条能力线，并行不冲突
> 使用规则：每版实施完成合并后勾销；战后呈批项裁定后回填；新发现入登记文档续编并在此排期

---

## 总览

```
0.3.2 错误通道修复战     0.3.3 契约透明化批        0.3.4 假设预检器         0.3.5 表达力升级
 G1+C-α/G9/G10+C-γ/G2  →   G4-G8/G12/C-β        →   G3+G11（新模块）     →   G13/G14（远期）
 【已合并 0b3cf1f】          【已合并 257c044】        【当前焦点】             突破简单规律表达域
 修复 AI 研究可信度地基       预算/判定/边界三层可见      消灭盲登记浪费
```

**排序逻辑**（止损 → 透明 → 提效 → 升维）：两轮实战（~5000 检验对 + 3 假设 5 考试）的证据权重排序——静默错误比崩溃危险；伪 task_id 死循环直接侵蚀研究可信度；0 笔考试是真实浪费；表达力边界投入最重故置后。

**依赖关系**：0.3.4 前置依赖 0.3.3（预检器消费的预算/判定/边界披露面须先就位；原「前置 0.3.2」已满足）；0.3.5 启动时点在 0.3.4 落地后按第三轮研究实证需求评估 G13/G14 谁先。

---

## 0.3.3 · 契约透明化批（G4-G8 + G12 + C-β）——战报档案

**形制**：openspec change **`mcp-contract-transparency`**（四工件定稿、validate --strict 通过）；实施分支 `feature/mcp-contract-transparency`；tasks 17 项（核验 2 + 披露 4 + 判定 3 + 边界幂等 3 + 装载 3 + 收口 2）。

| 项 | 内容（定稿形态——经三轮外部评估修正） |
|---|---|
| **做什么** | ① **G5 warmup 预算披露（四面三维度忠实口径）**：主公式 `max(200, 跨指标取最大的（周期类参数注册表上限之和）×3)`（derive_warmup 真身 registry_meta.py:60-78——docstring「最大周期×3」口径差禁抄）+ 高周期分支（core/data/mtf_alignment.py:30/:136-163，floor 20）+ 插件维度（`max(derive_warmup, plugin_formula_lookback)`，_derive_dna_warmup :176-194）；四披露面（报错 detail 键 warmup_bars/rule、能力自述、list_indicators、隔离回测 warmup.basis 字段）+capability.py:24-25 旧口径修正；载体分工：数值落 detail、静态规则落 hint；② **G6 随机窗 verdict 顶层化（双路径同源）**：正常路径取在场 agg、幂等命中取记录 verdict 列（先例 :360，禁从 worst_window 提取——窗口判定≠聚合判定）；③ **G7 幂等身份五元化** `(hypothesis_id, strategy_id, range_hash, level, segmentation)`：segmentation 列编码分段身份（auto 字面量 / explicit 规范化段清单 sha256[:16]——同形转换钉死 / 其余空串 NOT NULL DEFAULT ''）、五元唯一索引（alembic 迁移+存量回填+迁移内联副本防重放漂移）、level 入查询顺带修复隔离/分段互撞现状缺陷；④ **G12 衍生品边界披露**：df.attrs 通道三态（absent/gap/probe_failed）+并发安全+存活锚；⑤ **G4 异步阈值披露**（_HEAVY_ROWS:36 常量引用）；⑥ **G8 n_significant 顶层单点计数**；⑦ **C-β index-only 轻量计数**（storage.load_parquet 扩 columns 前置+heavy 判定索引化） |
| **四次裁决** | Q1 接受迁移（五元唯一索引——零 schema 被 ux_validation_idem 唯一索引证伪后重定）；Q1' explicit 段清单必须独立（编码段清单内容而非模式串——阻断修正）；Q2 快照链排除（尾部陈旧另立登记，见后续债务）；Q3 忠实实现口径（禁抄 docstring） |
| **评估历程** | 三轮外部评估 8→7→4 项收敛（末轮零阻断），两次内审+七次回流共修复 31 项全部落盘 |
| **验真锚点** | registry_meta.py:60-78 / mtf_alignment.py:30,:136-163 / validation_service.py:463,:568,:941（三构造点）,:360（verdict 先例）,:577-578,:525-527（双路径）/:944（params 载体）/:867-869（range_hash 四键前像）/ models.py:460-464（ux_validation_idem）/ research.py:397 / screen_patterns.py:270,:333,:367 / mtf_loader.py:164-191 / analysis.py:36,:384-386 / storage.py:24-26 / capability.py:24-25 / warmup.basis :452,:474 / test_errors.py:218-227 |
| **价值** | 研究者的「预算/判定/边界」三层可见性补齐——全部为两轮实战高频摩擦点；G7 顺带还清隔离/分段互撞旧债 |
| **验收** | 报错含预算数值与规则（四披露面口径一致）；三路考试 verdict 顶层直取（双路径同值）；扫描全灭 n_significant=0 可见；funding 缺口区间显式声明；同区间异分段（含两份 explicit 清单）各自独立+等价清单命中+类型隔离；heavy 请求每分支装载=1；幂等六断言+双附加锚全绿 |
| **测试设计** | TESTING_V3 对账形制：specs 十二个新增 Scenario 逐条对账+产品六验收点+design 测试锚；约 32-37 例；回归面零改写红线（既有锚需改写即零破坏被突破信号，停卡上报） |
| **team3 第八战增量** | 5 卡 P1 全闭环（13 提交=实现 5+判别锚 5+特征网/闸门 3，全部「修复→回归→抽核→提交」四步）：CARD-01 naive 时间计数裸 500（C-β 引入的回归——lo/hi 经 to_utc 归一）/ CARD-02 衍生品 gap 前扩误报（coverage_range 三层贯穿，判定基准改用户请求区间）/ CARD-03+06 warmup 高周期折算口径缺失+旧口径源内残留（三披露面同步）/ CARD-04 n_significant 被 top_n 截断封顶（改 by_fp 全族统计）/ CARD-05 非法时间串裸 500 存量洞（DataRangeItem 单点 422 化）；终验全量 4478 passed（2 存量红=TD-1 死测试，已另分支退役）+e2e 64+触碰面覆盖率 100% 行+分支 |
| **合并点** | **257c044**（2026-10-05，--no-ff，18 提交；分支 feature/mcp-contract-transparency 与 fix/mcp-contract-review-033 已删）；TD-1 微分支 chore/test-run-entry-retirement（**0ff3aa0**）；openspec change **mcp-contract-transparency 待归档**（主 specs 5 delta 待同步） |

## 0.3.2 · 错误通道修复战（G1+C-α / G9 / G10+C-γ / G2）——战报档案

| 项 | 内容 |
|---|---|
| **做什么（摘要）** | 模板必填键校验族（8 模板 11 键）；G9 跨指标条件双层修复（3a 预算层+3b 计算层+3c 模糊兜底禁用）；G10+C-γ 快路径失败形制重构（结构化直返+研究日志留痕）；G2 快照 hint 双层机制 |
| **team3 审查增量** | 11 卡全闭环：P0×3（留痕吞结构化响应/pandas_ta None 列 TypeError/单事件 NaN 序列化崩）+P1×8（小样本族收编 422 用户裁决方向 a/判定链单点化等） |
| **合并点** | **0b3cf1f**（2026-10-05，--no-ff，14 提交，分支 fix/mcp-error-channel 已删）；全量 4493 passed+e2e 64；openspec 归档 `archive/2026-10-04-fix-mcp-error-channel`；主 spec `research-error-channel`（5 Requirement）已同步 |
| **TD 去向** | flaky 时序两例/plugin_service 防御三行/engine 存量动作族 26 行 27 分支归 0.3.8——见后续债务区 |

## 0.3.4 · 假设预检器（G3 + G11 合成，新模块开发）

| 项 | 内容 |
|---|---|
| **做什么** | 新分析模板/工具 `precheck_hypothesis`：① 组合统计预检（分析段条件统计+基准对照——补上 G3 断裂的组合复核通道）；② 触发频率外推（按分析段触发密度预估验证段笔数，<5 显式警示——消灭 0 笔考试浪费）；③ warmup/数据预算预检（承载 G5 预算计算落地）。`register_hypothesis` 挂接可选预检警告 |
| **价值** | 研究迭代从「登记→考试→才发现空转」变为「预检→只送有弹药的方向」——两轮实战（3/13/0 笔频率失控）提炼的最高 ROI 能力 |
| **验收** | 两轮三假设回溯预检验收锚：BTC 急跌（~3 笔→警示）/ETH 双过滤（~0 笔→拒绝送考）/ETH 动量（~13 笔→放行）——三个已知答案全部命中 |
| **形制** | openspec change 完整流程；前置 0.3.3 |

## 0.3.5 · 表达力升级（G13 + G14，远期排期）

| 项 | 内容 |
|---|---|
| G13 截面工具 | 多 symbol 同条件统计/扫描对照模板——尺度分层结构跨品种检验从手工变内置 |
| G14 事件驱动形制 | 事件定义条件+密度可控参数——直击频率硬伤根源 |
| 形制 | 各自独立 openspec change；启动时点在 0.3.4 落地后按第三轮研究实证评估 |

---

## 研究结论基线（本规划的问题源头，防重复探索）

两轮实战的三个结构性结论，作为 0.3.4/0.3.5 能力设计的需求依据：

1. **尺度分层结构跨品种复现**（BTC/ETH 双品种：短周期 ≤4h 均值回归倾向 + 中长周期动量延续倾向）——G13 截面工具的验证对象；
2. **~5000 检验对 FDR 全灭**：2023-2026 BTC/ETH 在 47 指标×固定阈值×单/浅组合条件下不存在统计上立得住的简单规律——G14 事件驱动与深组合的方向依据；
3. **三假设考试全 inconclusive**（基准碾压 +19.6%/+24.7% + 频率失控 3/13/0 笔）——G11 预检器与「任何入场型规则须回答凭什么跑赢持有」的设计约束。

## 待裁事项

| # | 事项 | 状态 |
|---|---|---|
| ~~Q1 认证面是否随 0.3.3 搭车~~ | **已裁（2026-10-04）：暂缓**——本机形态零暴露；与 A2 台账硬条件（跨机启用前必做 auth payload 形制改造+TLS）绑定同窗口 |
| ~~Q2 快照链纳入与否~~ | **已裁（2026-10-05）：排除出 0.3.3**——尾部陈旧与缺口声明不同形态，另立登记（见后续债务 G12 衍生项） |
| Q3 | G13/G14 启动顺序 | 待裁（0.3.4 合并后，按第三轮研究实证定） |

## 后续债务（非本规划主体，供选择）

| 类 | 项 | 状态 |
|---|---|---|
| 0.3.3 衍生 | **G12 衍生项：快照 stale 披露**——get_market_snapshot 衍生品维尾部陈旧（funding 停更后照报 status ok 陈旧读数），需独立披露设计 | 待排期（随衍生品月包回补或后续版本） |
| team3 第七战 TD | flaky 时序敏感两例 / plugin_service 防御三行 / engine 存量动作族 26 行 27 分支 | 归 0.3.8 validation 域治理 |
| team3 第八战 TD（0.3.3 审查增量，2026-10-05） | TD-1 run_entry 死测试退役（run.py 已删于 8da2857 而两用例未退役——全量恒 2 红信号污染）；TD-2 存量覆盖缺失（validation_service 10 行 6 分支 / screen_patterns 4 行 6 分支 / condition_kernel+repository 小项——触碰面外存量）；TD-3 迁移 d4e7f9a2c5b8 downgrade 注释失实（同三元多行时重建索引实抛 IntegrityError，注释自称保留可读）；TD-4 七件小债（warmup_loader import 冗余 / 魔数 50 耦合 min_bars / hint 三要素张力 / 幂等 409 边缘序 / coverage 三态不对称 / models.py 注释锚 / 0-bar 边界文案） | TD-1 **单独微型分支 `chore/test-run-entry-retirement`，0.3.3 合并后立即**（不排版本批次——粒度不匹配+信号债按天计息）；TD-2 归 0.3.8；TD-3 归 0.3.7；TD-4 随 0.3.4/0.3.5 触碰搭车 |
| 跨机启用检查点增量（team3 第八战 S 组） | S-1 500 detail 透内部异常原文（str(exc)[:200]）/ S-2 验证族无区间 bar 上限（仅 sandbox 有 SANDBOX_MAX_BARS）/ S-4 分析任务 result_ref 无 TTL / S-3 socket.io token 经 URL query（api/auth.py） | 与 Q1 已裁同窗口：跨机启用日（auth payload 形制改造+TLS）一并清零，不进版本序列 |
| 0.3.x 债务清偿序列 | 0.3.6 trading+live_trading（资金面，实盘前置）→ 0.3.7 persistence → 0.3.8 evolution+validation | 待实施（实盘路线主线，与本系列并行） |
| 积压裁决 | C1 组合策略语义 / C2 scoring 四例 / C3b mode 口径 / Q3-team3.md 固化 / Q4-F1 | 待裁 |
| 其他专项 | pytest 双模块 conftest 统一、TD 低危 4 项 + P2/P3 记录 10 项、B4/B5③/B6 | 按需 |

---

## 执行勾销表

| 版本 | 内容摘要 | 分支 | 合并点 | 状态 |
|---|---|---|---|---|
| 0.3.2 | 错误通道修复：11 键校验族+跨指标双层装载+快路径失败形制+快照 hint；team3 审查增量 11 卡 | fix/mcp-error-channel | 0b3cf1f（2026-10-05，--no-ff，14 提交） | 已合并+归档+主 spec 同步 |
| 0.3.3 | 契约透明化：五元身份（段清单编码+迁移）/四面三维度忠实口径/verdict 双路径/df.attrs 边界/index-only 装载；team3 第八战增量 5 卡（naive 时间回归/gap 前扩误报/折算口径/计数封顶/422 化） | feature/mcp-contract-transparency（+fix/mcp-contract-review-033） | 257c044（2026-10-05，--no-ff，18 提交） | 已合并（openspec 归档+主 spec 同步待执行） |
| — | TD-1 死测试退役（run_entry 两用例——8da2857 遗留，全量 2 红信号污染） | chore/test-run-entry-retirement | 0ff3aa0 | 已合并（后续债务区登记销账） |
| 0.3.4 | 假设预检器新模块：组合统计预检+频率外推+预算预检（G3+G11） | openspec change | — | 待实施（前置 0.3.3） |
| 0.3.5 | 表达力升级：截面工具（G13）/事件驱动形制（G14） | openspec change | — | 待实施（前置 0.3.4 后评估） |
