# MyQuant MCP 研究链路修复版本规划（fix-mcp 系列）

> 制定日期：2026-10-01
> 来源：用户定调「现在主要解决 mcp agent 进行研究相关的问题，其他板块不处理」——对 works/设计稿/MyQuant-技术债务登记.md 做链路视角重分析产出
> 分析方法：从代码核出 agent 研究的真实六层链路，登记项按链路环节重映射 + 两个登记外新发现（F9/F10）
> 与姊妹篇关系：MyQuant-技术债务登记.md 记「有什么债」；MyQuant-债务清偿版本规划.md（0.3.x 实盘价值链）**维持原样不动**——本系列插队先行，其 api 战范围相应缩小（见「与其他规划的关系」）
> 使用规则：每版实施完成合并后勾销；战中呈批项裁定后回填；F9/F10 待同步入登记文档（本文档先自包含定义）

---

## 总览

```
fix-mcp-1 研究链路上游战          fix-mcp-2 研究链路下游战
 入口层+端点层+记忆层               评估层
 mcpserver/ 5文件1159行首审         core/evolution 2668行首审
 api/routes/research/ 10文件        core/validation 1457行首审
   1131行首审                      B5 三小件搭车
 research.py 564行                 C1/C2 战中呈批裁口径
 A1 keyset + F10 total             F3/F7/F2剩余 搭车
 F9 能力自述失联修复
 规模约2854行                       规模约4125行
```

**排序逻辑**：按链路上下游——先修 agent 的「认知与记忆」（它怎么知道有什么工具、怎么读研究历史），再修「评估口径」（它产出的策略怎么被判定）。上游战直接改善 agent 每次研究的日常行为，下游战收口判定质量。

---

## 链路全景（问题地图）

```
宿主 AI agent
   |  读 quant://capabilities 能力自述     [F9：清单缺三工具]
   v
[入口层] mcpserver/ 5 文件 1159 行         从未经 team3 审查
   |  REST（QuantApiClient + 端点白名单）
   v
[端点层] api/routes/research/ 1131 行      未审查（api 域一部分）
   v
[执行层] core/analysis/                    第二战已审（bc33510）——不重审
   v
[记忆层] research.py 564 行                未审查
   |                                       [A1 升序首屏][F10 total 无信息量]
   v
[评估层] evolution 2668 行 + validation    未审查
      1457 行                              [C1][B5][C2][F3][F7]
```

事实局限在案：`openspec/specs/` 目录当前为空——fix-mcp-1 组 team3 战时基准源走「特征测试网」模式（mcpserver 测试面厚实：7 个测试文件 + `_expected_tools` 共享锚 + e2e），capability.py 静态协议文档作辅助基准。

---

## 新发现两项定义（自包含，待同步入登记文档 F 组）

### F9 · TOOL_TIERS 失联——能力自述工具清单缺三个研究工具

1. **问题**：`mcpserver/tools.py:512-517` 的 research 档注册元组漏了 `inspect_cases`、`query_indicator`、`run_staged_validation`（0.2.13/0.2.14 新增三工具）；`server.py:52` 生成能力自述时工具清单取自 `TOOL_TIERS` → `quant://capabilities` 第 1 节缺这三工具，而第 2 节协议要点却详细描述它们（`capability.py:40/48/58`）——**自述文档自相矛盾**。工具本体经 `@mcp.tool()` 已注册可调，属「认知面缺失」非「功能面缺失」，更隐蔽。agent 按自述构建认知时，六步研究法检查③（案例抽查）④（指标读数）⑤（分段验证）不可见——研究质量检查链在 agent 认知里断了半条。
2. **测试网四道全漏的原因**：`_expected_tools.py` 锚注册面（三工具在册所以绿）；`test_server.py:80` 对 research 档是单向 `<=`（拦「枚举有未注册」不拦「注册没进枚举」）；`TestSnapshotToolParity` 只单点断言 snapshot；能力自述测试只查 `register_hypothesis in text`。0.2.6/0.2.7/0.2.8 三版各踩一次人肉同步坑——这是第四次，共享锚建了但锚错了面。
3. **方案**：元组补三工具名 + 断言补双向（`TOOL_TIERS["readonly"] | TOOL_TIERS["research"] == EXPECTED_RESEARCH_TOOLS`，锚 `_expected_tools` 单处权威）。
4. **作用**：agent 的产品手册与工具事实恢复一致；断言双向化后第五版增工具漏同步必红。
5. **影响**：一行级修复 + 一条断言，零行为面。

### F10 · 研究日志分页 total 无信息量

1. **问题**：`api/routes/research/log.py:47` 返回 `"total": len(out)`——total 是当页条数非总条数；`feature-requests` 端点（:92）同族。`get_research_log` 的产品定位是「续接研究前先读」（agent 的研究记忆），翻页时没有任何信号判断「还有没有下一页」，只能盲翻到空页才知道读完。
2. **方案**：count 查询返回真实总数（或补 `has_more` 信号），与 A1 的 keyset 改造合并一张卡修（同属分页语义缺口）。
3. **作用**：agent 记忆遍历获得终止信号，「读完了吗」的判断有依据。
4. **影响**：响应多一次 count 查询；total 语义修正（当页数→总数）属 bug 修正性质，调用方按语义增强兼容。

---

## fix-mcp-1 · 研究链路上游战（入口层 + 端点层 + 记忆层）

| 项 | 由什么 → 变成什么 | 价值 |
|---|---|---|
| mcpserver/ 5 文件 1159 行全量首审 | 从未审查（agent 研究唯一入口层）→ team3 五维+安全出卡修复 | 入口层风险从不可知变已量化 |
| api/routes/research/ 10 文件 1131 行首审 | 未审查（研究全部 HTTP 端点）→ 出卡修复 | 端点层风险已量化；F4 存量覆盖缺口自然补齐 |
| A1 keyset 分页（research.py） | repository 升序+offset 首屏语义（agent 续接研究先读最早一页；防线跟着消费方走）→ `(created_at, 主键)` 游标式，全量语义成 repository 层保证 | agent 记忆读取的结构性修复；并发漂移残差一并消除 |
| F10 total 语义 | total=当页数（agent 不知何时读完）→ 真实总数/终止信号 | 记忆遍历有依据 |
| F9 能力自述失联 | 自述清单缺三工具+文档自相矛盾 → 清单与注册集双向锚定 | agent 认知与能力一致，检查链完整可见 |
| F2（api 域部分） | 手写内联 re.sub → 收敛 safe_symbol | 清洗契约统一 |

- 分支形制：`fix/mcp-research-upstream-review`
- 基准源：特征测试网模式（specs 空）+ capability.py 协议基准
- 验收：team3 阶段四闸门（覆盖率 ≥98% 或不可达判定 + 全量回归含 e2e）

## fix-mcp-2 · 研究链路下游战（评估层）

| 项 | 由什么 → 变成什么 | 价值 |
|---|---|---|
| core/evolution 2668 行首审 | 未审查 → 出卡修复 | agent 触发进化（trigger_evolution）的评估端风险已量化 |
| core/validation 1457 行首审 | 未审查 → 出卡修复 | 验证考试链（隔离/分段/随机窗口的判定底层）风险已量化 |
| C1 组合策略 default 语义（战中呈批） | 显式 default 短路不收紧 → 裁 A 恒收紧 / B 维持现状并规格锚定 | 验证口径一致性（团队倾向 A） |
| C2 scoring D 类 4 例（战中呈批） | 现状容忍 → 逐例裁口径规格锚定 | agent 依赖的评分数字口径统一 |
| B5 三小件 | 死字段/NaN 静默/吞异常缺 warning → 清除+提示+留痕 | 进化域可观察性 |
| F3 formula 窗口上界 DSL 化 | DSL 校验无窗口上限（注册面 plugin_service 单点 10_000）→ validate_formula 共享常量上限 | AI 生成表达式溢出防线从单点变双层 |
| F7 ast.parse 防护 | plugin_service 装载 DB 历史表达式只捕 SyntaxError → RecursionError 同防 | 装载面与注册面同标准 |
| F2（evolution/validation 部分） | 各域手写内联 re.sub → 收敛 safe_symbol | 清洗契约链路统一 |

- 分支形制：`fix/mcp-research-downstream-review`
- 前置：fix-mcp-1 合并后切出
- C1/C2 走 team3 战中呈批通道（决策路由支持，团队倾向在案）

---

## 与其他规划的关系

- **0.3.x 债务清偿规划（v2）维持原样不动**：本系列插队先行后，其 0.3.1 api 硬化战范围相应缩小为「api 非研究端点 + A2 认证」，序列号与剩余内容顺延关系待该系列重启时再理——现在不处理。
- **A2（api 认证）不属本系列**：部署面问题；MCP `api_client` 是它的适配方之一，本系列既不解决也不被它阻塞。
- **core/analysis 不重审**（第二战已审）。
- **F9/F10 同步入登记文档**：已同步（2026-10-01 合并后补 F 组两行，Q7 完结）。

## 待裁事项

| # | 事项 | 建议裁定时点 | 我的建议 |
|---|---|---|---|
| Q5 | C1 组合策略语义（A 恒收紧 / B 维持现状） | fix-mcp-2 战中呈批 | A（团队既有倾向） |
| Q6 | C2 四例逐例口径 | fix-mcp-2 战中呈批 | 逐例裁，不预设 |
| Q7 | F9/F10 登记入册（登记文档 F 组续编） | 随时 | 已完成（2026-10-01 合并 c37c067 后补登） |

---

## 执行勾销表

| 版本 | 内容摘要 | 分支 | 合并点 | 状态 |
|---|---|---|---|---|
| fix-mcp-1 | 上游战：mcpserver+research 端点+research.py 首审；14 卡（A1/F9/F10 落地、F4 端点覆盖随闸门补齐；TD：S-6③④ P2、S-7 记录、范围外专项三项） | fix/mcp-research-upstream-review | c37c067（--no-ff，2026-10-01，30 笔提交） | **已合并** |
| fix-mcp-2 | 下游战：evolution+validation 首审；C1/C2 呈批；B5/F3/F7/F2(剩余) | fix/mcp-research-downstream-review（拟） | — | 待实施（前置 fix-mcp-1） |
