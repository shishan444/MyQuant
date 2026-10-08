# MyQuant 技术债务登记

> 登记日期：2026-10-01（同日 v2 价值重估）
> 来源：team3 三战交付报告在案项（scoring 0cdd4a9 / analysis bc33510 / data+signals ff425e3）+ 流程实测教训
> 维护规则：每项含四问（问题/方案/作用/影响）与推荐处置；裁决后勾销或转入实施；新债务续编号登记
> **唯一登记（2026-10-07）**：本册为全工程唯一技术债务登记——0.2.x 时代 TD-N 体系（原《技术债务登记-需求原稿20260930》，已归档 works/设计稿/archive/）未偿项全量迁入 I 组，查债只读本册；各战交付报告内 TD 台账为一次性在案明细，以本册为收敛正本。**日期口径**：文中合并点日期以 git 提交日为准（个别战役日记为呈批日口径，差一天属正常）

> **v2 价值重估（2026-10-01，用户批准）**：全部在案项按「真实暴露 × 后果量级」重估——①A2 为唯一「现在就真实、后果严重」项，升格为**实盘部署前置能力建设**（非清债）；②未审域不可知敞口大于登记项（三战每战挖出登记外新 P0）；③卫生项（F1/F2/F3/F4/B4、B5 大半）降级搭车或接受现状，不立版本。重排后版本序列与各项新处置见姊妹篇 MyQuant-债务清偿版本规划.md（v2）：0.3.1 api 硬化（部署面）→ 0.3.2 trading+live_trading（资金面，live_trading 原漏审已补入）→ 0.3.3 persistence（数据面）→ 0.3.4 evolution+validation（研究面）。**编号重构（2026-10-03）**：MCP 修复系列插队占用 0.3.2-0.3.5（见 MyQuant-修复MCP版本规划.md），上述三战顺延为 0.3.6/0.3.7/0.3.8。

---

## 总览与推荐

| # | 项 | 来源 | 推荐 |
|---|---|---|---|
| A1 | repository 升序+limit 根因 → keyset 分页 | analysis 战 S-1 根因 | **已清账**（fix-mcp-1 CARD-07 keyset+真总数+next_cursor，iter_all_pages 四消费方零改动，2026-10-01 合并 c37c067） |
| A2 | api/ 无认证 + CORS 通配 | scoring 战在案 | **已清账**（0.3.1 api 硬化战 CARD-32 认证四件套：CORS 白名单/API key 中间件 A 方案/绑定收口/Host 校验 + socket.io 认证，2026-10-03 合并 a88420a；S-1~S-8 全关闭。台账硬条件：key 禁复用高价值凭据、**跨机启用前须改 auth payload + 上 TLS**） |
| B1 | mtf_loader 自内联 safe_symbol ×2 | analysis 战专项 | **已清账**（第三战 CARD-11，2026-10-01 合并 ff425e3）；范围外 8 处内联另见 F 组 |
| B2 | formula.py 窗口参数无上限（根治点） | analysis 战 S-6 | **已清账**（第三战清长度/嵌套/下界；fix-mcp-2 CARD-18 窗口数值上界 DSL 化（MAX_PLUGIN_LOOKBACK_BARS 下沉 formula.py+静态关前置）——2026-10-02 合并 89c72cd 全清） |
| B3 | timeframe 无白名单/未净化 | analysis 战 S-5 | **已清账**（第三战 CARD-03 find_parquet 白名单；api 两端点经 security 复核为路由层天然阻断，一致性加固可选不另立项） |
| B4 | kernel 私有名跨模块 import 公共化 | analysis 战 TD-2 | 建议顺手修 |
| B5 | scoring 小件三件（死字段/NaN 静默/缺 warning） | scoring 战 | **件①②已清账**（fix-mcp-2：CARD-08 template_name 死字段/CARD-12 evaluation.py 吞异常报错化——2026-10-02 合并 89c72cd）；件③ knn_engine NaN 静默留 0.3.7 收尾战前置呈批（core/discovery/knn_engine.py:130-132，scoring 域外未随战；v3 两版压缩自 0.3.8 改号） |
| B6 | pct_change 弃用参数（非测试 18 处，3 处已显式化，**现 15 处未显式**——2026-10-08 验真复核） | scoring 战 | 缓办（与依赖升级专项合并） |
| C1 | 组合策略显式 default 语义 | analysis 战 TD-1 | 需裁口径 |
| C2 | scoring D 类现状容忍 4 例 | scoring 战 | 需裁口径 |
| C3 | ImportMode.NEW 三态两值（new=replace） | data/signals 战 CARD-H1 | 已裁决登记（2026-10-01 本次不修，待将来裁语义方向） |
| C4 | fetcher 硬编码内网代理默认值 | data/signals 战 CARD-15 拆出 | 已裁决登记（2026-10-01 本次不修，保留现状） |
| D | team3.md 四条流程条文 | analysis 战实测 | 建议固化 |
| E | macd 留白 / schemas Dict 保持 | analysis 战 | 无需行动（已含说明） |
| G | MCP 六步法实战复盘 14 项（G1 P1 bug + G2-G4 契约失实 + G5-G8 能力改进 + G9-G12 第二轮补入 + G13/G14 表达力升级远期） | 2026-10-03 两轮全链实战（BTC 单品种四方向 + ETH 跨品种复制，~5000 检验对+3 假设 5 考试） | **G1-G12 已全部清账**（G1/G2/G9/G10←0.3.2 错误通道战 0b3cf1f；G4-G8/G12←0.3.3 契约透明化 257c044；G3/G11←0.3.4 假设预检器 8d6f8f2；G14←0.3.5 事件驱动 28e7efc——2026-10-08 验真回填）；G13（截面工具）随第三轮实证窗口再启 |
| H | 0.3.5 实施与审查战增量 8 项（H1 空区间裸 500 / H2 crossover 丢 v2 字段 / H3 templates 包聚合断链不可达 / H4 DNAModel dict 字段静默降级面 / H5 signal_assembly 坏值与静默缺省 / H6 api 无全局 ResearchError handler / H7 存量 Feature 平行体系 / H8 英文 logger 消息） | 2026-10-08 add-event-driven-triggers 实施在案 2 项 + team3 第十一战（fix/event-triggers-review-hardening 8 卡+六步闸门）专项 6 项 | **v3 两版压缩归宿（2026-10-08）**：H4+H2 与 TD-14 合并为版本外微战 `fix/dna-strong-typing`（正确性风险先行）；H1/H5/H7 归 0.3.7 收尾战；H3/H6/H8 归卫生小件批 |
| I | 0.2.x 系列在册 11 笔 + 披露项 1 项（TD-1 考试链断链 / TD-2 前端文案 / TD-4 双轨 warmup / TD-5 validate 无留痕 / TD-7/8 卫生两件 / TD-10 then 周期键静默忽略 / TD-11 lookback 残余键静默缺省 / TD-12/13 design 文档债 / TD-14 签名不含阈值；executor 双路径并存） | 2026-09-29/30 三批次：0.2.14 规划期（TD-1..5）+ team2 交付前（TD-6..13）+ team3 存量审查（TD-14）——自归档件《技术债务登记-需求原稿20260930》迁入（2026-10-07 债务登记唯一化：本册为全工程唯一债务登记；TD-3/TD-6 已偿还、TD-9 已闭合不迁） | TD-10 待产品拍板（a 拒绝/b 定义语义）；TD-7/8/11 同批治理小任务（TD-6 已清、TD-11 window 已清余两键）；TD-12/13 随归档工件澄清说明（openspec 归档不再回写）；TD-14 专项方向 b（复用前 dna_json 等价复核）；TD-1/2/4/5 按偿还触发 |

---

## A 组 · 建议立项修（高价值）

### A1 · repository 升序+limit 根因（keyset 分页）

1. **问题**：CARD-01 修了 core/analysis 四个消费方（iter_all_pages 分页取全量），但根因还在——`core/persistence/repository/research.py` 的 `list_research_log` / `list_validation_records` 是升序+limit 首屏语义。任何新增消费方若不知道要用 `iter_all_pages`，直接调 `list_research_log(limit=N)` 就会重演"只见最早 N 条"的静默失效（判定/核查/证据类查询全量语义被截断）。另外 offset 翻页存在并发写入漂移的理论残差（security 定性可接受）。
2. **方案**：repository 层改 keyset 分页——`(created_at, 主键)` 游标式翻页替代 offset；security 已论证 core/analysis 四消费方零改动即可受益（fetch_page 前置条件同步收紧为 keyset 语义）。分支形制 `fix/persistence-keyset`。
3. **作用**：根除整类问题（全量语义成为 repository 层保证，不依赖每个调用方自觉）；并发漂移残差一并消除。
4. **影响**：repository 层内部实现变化，接口形制可保持兼容（游标参数可选）；特征网+全量回归护航，风险低。

### A2 · api/ 无认证 + CORS 通配

1. **问题**：HTTP API 全域无认证（任何人可调用研究/回测/删除类端点），CORS 通配 `*`（任何网页可跨域访问）。本地单机部署下风险可控，但一旦监听地址改为局域网/公网即裸奔——两战都在案未决的最重安全项。
2. **方案**：①API key 中间件（env 注入，MCP/前端调用方带 `Authorization` 头）+ ②CORS 收紧为显式 origin 白名单（localhost:8080 等已知前端）+ ③默认绑定 127.0.0.1。分支形制 `fix/api-auth-hardening`。
3. **作用**：公开部署前的必要闸门；CORS 通配的跨域攻击面消除。
4. **影响**：调用方需适配（MCP api_client、web 前端、e2e 测试都要带凭证）——触碰公共契约的变更，实施时需走完整呈批；测试面改动较大。**单独立项，不与 B 组混批。**

---

## B 组 · 建议顺手修（低成本小批量）

### B1 · mtf_loader 自内联 ×2

1. **问题**：safe_symbol 正牌定义在 `core/data/mtf_loader.py` 上方，同文件下方（:70/:114）又手写内联两处——同一文件两种用法。
2. **方案**：改 import 正牌（CARD-05 同款，几行）。
3. **作用**：单一实现契约在同文件内成立。
4. **影响**：零行为面，纯收敛。

### B2 · formula.py 窗口根治

1. **问题**：CARD-21 在插件注册面拦了超长窗口（MAX_PLUGIN_LOOKBACK_BARS=10_000），但 `core/signals/formula.py` 的 `formula_lookback` 本身无上限——其它未来调用方直接用仍会 Timedelta 溢出。
2. **方案**：`validate_formula` 静态校验加窗口上限常量（与 plugin_service 共享同一常量）。
3. **作用**：溢出防线从"注册面单点"变"DSL 校验层根除"。
4. **影响**：超长窗口表达式在 validate 阶段即拒（比注册面更早），无合法使用受损（真实需求千级）。

### B3 · timeframe 白名单（S-5）

1. **问题**：`api/schemas.py` 的 `DataRangeItem.timeframe` 裸 str 未净化，直接拼接 parquet 路径（`mtf_loader.py:39`）。当前受"文件须存在+只读+中间目录须存在"三重限制不可利用，属防御深度缺失。
2. **方案**：入口 TF 白名单拒绝（`case_inspection` 的 TIMEFRAME_UNKNOWN 同款）+ `find_parquet` 净化 tf——白名单用 TF_SECONDS 键集（单一事实源）。
3. **作用**：路径注入面在两层封死；未知 tf 从静默 240 兜底变显式 422。
4. **影响**：非法 tf 请求从"静默错算"变结构化拒绝（行为变化但属 bug 修正性质）。

### B4 · kernel 私有名公共化（analysis 战台账项）

1. **问题**：`_p_value_for` / `_mask_for` / `MtfEvalContext` 等 condition_kernel 私有名被六模板+case_inspection 共七处跨模块 import（condition_stats.py:132 / precheck_hypothesis.py:46 / event_study.py:62 / param_sweep.py:38 / regime_split.py:20 / screen_patterns.py:136 / case_inspection.py:61——2026-10-08 验真复核，0.3.2-0.3.5 新模板各带一处，原记「四个模板」已滞后）——Python 反模式，已有固化先例（CARD-08 实施时按一致性接受）。
2. **方案**：一次性公共化改名（私有名→公开名或提供公开别名），四消费方同步。
3. **作用**：消除"跨模块引用私有名"的风格债，未来 kernel 演进不再受限。
4. **影响**：纯改名重构零行为面，特征网护航。

### B5 · scoring 小件三件

1. **问题**：①`EvolutionEngine.template_name` 死字段（零引用）②`knn_engine` NaN 静默展示（用户看到空不知因）③`evaluation.py:40` 异常吞掉缺 warning 留痕。
2. **方案**：死字段删除；NaN 展示补提示文案；吞异常处补 `logger.warning`。
3. **作用**：死代码清除、可观察性补齐。
4. **影响**：零行为面（warning/文案为增量信息）。

### B6 · pct_change 弃用参数 ×12（缓办）

1. **问题**：12 处 `pct_change` 使用 pandas 已弃用参数形，未来 pandas 升级会 break。
2. **方案**：逐处显式化 `fill_method`——**非纯机械替换**：默认值从隐式 ffill 变 None 会改变 NaN 处理语义，每处需审计是否依赖隐式前向填充。
3. **作用**：pandas 升级通道打开。
4. **影响**：语义面需逐处审计——当前 pandas 版本稳定运行无急迫性，建议与下次依赖升级专项合并做，单独做性价比低。

---

## C 组 · 需裁口径（修=行为变化，不修=锚定现状）

### C1 · 组合策略显式 default 语义（analysis 战台账项）

1. **问题**：组合策略（staged validation 的组合分支）执行 `threshold_profile or tighten_profile()`——显式传 `{"name":"default"}` 时组合策略不强制收紧（default 是 truthy 短路）。与设计卡 6.5「组合用收紧 profile」意图有出入；存量语义非 CARD-03 引入，S-3 任意门槛注入已另行封堵，此为预定义档内的选择问题。
2. **方案 A（团队倾向）**：组合分支无视传入 profile、恒用 `tighten_profile()`——符合"组合必须加严"的设计意图。
   **方案 B**：维持现状——尊重显式选择（调用方明说 default 就给 default），已锚定在 `tests/analysis/test_td_tolerated_behaviors.py`。
3. **作用**：A=设计意图一致性恢复；B=调用方灵活性保留。
4. **影响**：仅组合策略分支的判定门槛；方案 A 使显式 default 的组合验证判定收紧（通过率可能下降，属收紧方向与 INV 族精神一致）；两案都在预定义档内无注入面。

### C2 · scoring D 类现状容忍 4 例

1. **问题**（四例，均为口径不一致而非崩溃，锚定在 `tests/scoring/test_known_behaviors.py`）：
   - ① win_rate 的 met 与 ratio 两条算路结果不一致
   - ② 早退路径 objective 透传
   - ③ 全坏点输入与空序列输出相同
   - ④ 非 DatetimeIndex 时月度异常被吞
2. **方案**：逐例定口径——每例裁"统一到哪个算路 / 吞还是拒"（细节见 scoring 战交付报告与 test_known_behaviors.py 注释）。
3. **作用**：口径统一后从"现状容忍"升级为"规格锚定"。
4. **影响**：逐例小行为变化，互不耦合。

### C3 · ImportMode.NEW 三态两值（data/signals 战 CARD-H1，已裁决登记）

1. **问题**：CSV 导入三模式中 NEW 分支=覆盖写，与 REPLACE 完全无差别（三态两值）；API 暴露 mode="new"。设计意图不可考（openspec 空、域文档无定义、前端未直传 new、特征网仅记录现状 test_new_mode_overwrites_existing）。
2. **方案（待将来裁方向）**：A）NEW 收紧为「数据集已存在则拒绝」（团队弱推荐，API 兼容、三模式语义完整）；B）删除 new（传 new 变 400，破坏潜在调用方）；C）维持别名并文档化。
3. **作用**：裁后三模式语义完整或明确文档化为别名。
4. **影响**：仅 CSV 导入端点 mode=new 且数据集已存在场景；当前无已知调用方使用 new。
5. **裁决记录**：2026-10-01 用户裁决「记录为技术债务，本次不做修复」——维持现状，特征网锚继续有效；与 api/routes/data.py 非法 mode 静默回退 MERGE（范围外专项 4）联动。

### C4 · fetcher 硬编码内网代理默认值（data/signals 战 CARD-15 拆出项，已裁决登记）

1. **问题**：`core/data/fetcher.py:22` 硬编码内网代理 `http://172.20.112.1:10809` 为 BINANCE_PROXY 未设时的默认值——查证本机未设该环境变量，即当前数据拉取正依赖此默认值走代理；内网拓扑暴露于代码且非显式配置。
2. **方案（待将来部署卫生化时）**：默认值改空显式 opt-in，部署环境同步设 BINANCE_PROXY（或确认可直连 binance.vision）。
3. **作用**：网络通路全显式化，代码不携带环境特定地址。
4. **影响**：改动日须同步部署环境配置，否则数据拉取失败——故与部署迁移合并做。
5. **裁决记录**：2026-10-01 用户裁决「记录为技术债务，本次不做修复」——保留默认值现状；CARD-15 其余三项（symbol 出站白名单/zip 上限/URL 覆写知悉）仍在本战修复队列执行。

---

## F 组 · 第三战新增待办（2026-10-01，data/signals 战范围外与遗留）

| # | 项 | 来源 | 建议 |
|---|---|---|---|
| F1 | dev.sh 调用 start.sh 无执行位（git 100644，干净 checkout 必现 Permission denied） | 阶段四 e2e 双形态补测 | **已清账**（9ab2023 删 dev.sh+run.py、5c61546 统一 scripts 入口——问题载体消失，裁决记录在案；表格原标「一行修」为滞后未勾销，2026-10-08 验真修正） |
| F2 | 内联 re.sub symbol 清洗：原记 8 处，验真复核（2026-10-08）**残余 1 处**（core/trading/runner.py:209 `_safe_sym`）——evolution/validation/api 域已随 41fc30d/4f9f351 收敛；另 runner 同链双形态：:201→:443 `_load_data` 裸拼 `f"{symbol}_{tf}.parquet"` vs :209 内联清洗并存（与 F6 同卡修） | architect 全库检索+验真复核 | 残余随 0.3.6 trading 域战 |
| F3 | formula 窗口**数值上界** DSL 化（validate_formula 加与 plugin_service 共享的上限常量）——B2 剩余面 | B2 口径更正 | **已清账**（fix-mcp-2 CARD-18，2026-10-02 合并 89c72cd） |
| F4 | api/routes/data.py 非触碰端点存量 25 行未覆盖（数据集详情/预览/删除/来源/更新） | 阶段四覆盖闸门 | **已清账**（0.3.1 特征网 test_data_f4_gaps 12 用例全盖，2026-10-03 合并 a88420a） |
| F5 | api 非法 mode 静默回退 MERGE + api/core ImportMode 枚举双表定义 | reviewer/architect 报备 | 与 C3 裁决联动，随 0.3.6 或后续（0.3.1 api 战已过未做——「api 域审查时一并」措辞已过时，2026-10-08 验真修正；现状亲核仍在：api/routes/data.py:199-201+双表） |
| F6 | core/trading/runner.py:443 _load_data 读取拼接无校验（S-3 同族读取面） | architect 专项 | trading 域审查时一并 |
| F7 | plugin_service.py:258 ast.parse 无 RecursionError 防护（DB 历史数据防御面，注册面已挡） | architect 专项 | **已清账**（fix-mcp-2 CARD-19 补 (RecursionError, MemoryError)→set()，2026-10-02 合并 89c72cd） |
| F8 | mtf_loader 两装载函数七步同构收敛 / formula 五表结构化收敛 | P2 不修记录 | 无实际漂移/回归面大，维持不动 |
| F9 | mcpserver TOOL_TIERS 失联——research 档元组漏 inspect_cases/query_indicator/run_staged_validation 三工具名，能力自述（quant://capabilities）工具清单缺检查③④⑤工具且文档自相矛盾；四道测试网全漏拦（单向子集断言） | fix-mcp-1 战前链路分析（lead/reviewer/tester 三方互证） | **已清账**（CARD-01 元组补三名+tester 等式对拍断言，2026-10-01 合并 c37c067） |
| F10 | 研究日志分页 total=当页条数（log.py 两端点+hypotheses）——agent 续读研究无终止信号；对照 strategies 域真总数语义并存两套 | fix-mcp-1 战前链路分析（reviewer Q3 扩面核实） | **已清账**（CARD-07 随 keyset 改造一并：三端点 total=真总数+next_cursor，2026-10-01 合并 c37c067） |

---

## G 组 · MCP 六步法实战复盘（2026-10-03，首次全链研究实测）

> 来源：两轮全链实战——第一轮 BTCUSDT 四周期矩阵扫描（2167 检验对→三维检查→考试）；第二轮 ETHUSDT 跨品种复制 + 动量/衍生品/突破三方向 + 双假设登记送考（G9-G12 为第二轮新增）。实测肯定面在案：FDR/warmup/幂等/留痕纪律全部按设计工作、param_sweep 无效点信息完备、inspect_cases 数据质量良好。以下为实测缺陷与改进项。
>
> **清账标记（2026-10-08 验真回填）**：G1-G12 已全部清账——G1/G2/G9/G10←0.3.2 错误通道战（0b3cf1f，2026-10-04 合并）；G4/G5/G6/G7/G8/G12←0.3.3 契约透明化（257c044，2026-10-05 合并）；G3/G11←0.3.4 假设预检器（8d6f8f2，2026-10-06 合并）；G14←0.3.5 事件驱动（28e7efc，2026-10-07 合并）。下文各条「建议」列为登记时原文留存（历史口径），实际状态以本标记为准；**未清仅 G13**（截面工具，随第三轮研究实证窗口再启）。

### G1 · condition_stats 快路径裸 KeyError + 伪 task_id 轮询陷阱（P1）【已清账←0.3.2】

1. **问题**：`api/routes/research/analysis.py:161` 的 `params=p["params"]` 无默认值下标且校验层未前置拒绝——params 键缺省时快路径抛 KeyError，`except ResearchError` 接不住，返回 `{task_id: "fast_condition_stats", status: "failed", reason: "'params'"}`——伪 task_id（DB 无此任务）+ 裸异常字符串 reason，违背结构化错误通道契约（RESEARCH_* 码+人话+hint）；MCP 客户端按 task_id 轮询即死循环到超时（实测 2×600s）。
2. **方案**：params 必填校验前置（422 PARAM_INVALID + hint 指明必填键）或 `p.get("params")` 兜底；快路径异常收尾补 KeyError 通道（结构化转译）。
3. **作用**：错误通道契约恢复完整；消除客户端轮询陷阱。
4. **影响**：单点小修，修复面极小；行为变化=非法参数从伪任务失败变结构化 422（bug 修正性质）。

### G2-G4 · 契约/文档失实（capability 自述与实际能力面偏差，便宜同批修）

| # | 失实点 | 实证 | 建议 |
|---|---|---|---|
| G2 | get_market_snapshot warmup hint 引用不存在的参数（「把 start 前移」——快照参数面仅 symbol/timeframes/as_of，无 start），Agent 照 hint 执行必碰壁 | 默认参数首调实测（3d 撞 300 bar warmup） | hint 改为 timeframes 降周期指引（与参数面匹配） |
| G3 | capability 声称扫描候选「经 condition_stats 逐条独立复核」——但 condition_stats 是单条件契约（condition 单数），组合候选（is_combination=true，top10 两两补扫产物）复核路径断裂 | 4h 组合候选复核实测撞墙（并触发 G1） | 二选一：capability 措辞收窄（组合候选不经此通道）或 condition_stats 支持多条件（能力建设，非文档修正） |
| G4 | regime_split 标注「快路径同步直返」——15m 级数据量实际异步降级（202+task_id），阈值行为无文档 | 15m 分段实测走异步 | 工具描述补异步降级阈值说明 |

### G5-G8 · 能力改进项（排期，非错误）

| # | 项 | 实证 | 建议 |
|---|---|---|---|
| G5 | warmup 预算规则不透明：×3 安全系数（lookback 200→实际 600）未在任何文档披露，错误 hint 数值指引差 3 倍——研究者靠失败学预算（本轮 4 次 warmup 失败方摸清） | 四档扫描首轮全败 600 实测 | hint 修正 + capability/工具描述披露预算规则（×3 系数明示） |
| G6 | 三路考试 verdict 输出位置不统一：隔离/分段在顶层，随机窗口藏在 summary.worst_window.verdict——AI 客户端消费不友好 | 三路考试实测 | 统一顶层 verdict 字段 |
| G7 | 分段验证幂等键不含维度：同区间先隔离后分段 → duplicated=true 复用记录，「分题型成绩单」（段级明细）拿不到——与该工具「哪类行情不行的正式度量」定位冲突 | hyp_07979a1d7ad7 考试实测（仅整体 metrics 无段级 breakdown） | 幂等键加 segmentation 维度或段级明细独立记录 |
| G8 | 扫描结果缺 n_significant 顶层汇总：FDR 全灭时需逐条看 q 才知全灭（本轮四档全 q≈1） | 四档扫描实测 | 补显著计数顶层字段（轻微） |
| G9 | condition_stats 跨指标条件装载断裂：subject=指标A、target=另一列（sma_200/kc_upper 等 cross_above_series 族）时 target 列不在 warm_names 装载清单——条件恒 False、样本 0，与扫描器同条件结果直接打架（BTC 1h SMA50 上穿 sma_200：扫描 n=65 vs 定向 0） | 第二轮方向④金叉定向实测（1h/4h 双复现） | warm_names 解析 condition.target 列引用；或校验层显式拒绝跨指标条件（勿静默 0 样本） |
| G10 | 快路径失败统一伪 task_id 形态：样本量不足等业务拒绝也返回 `{task_id:"fast_condition_stats", status:failed}`（DB 无此任务）——客户端按 task 轮询即死循环；G1 的 KeyError 是此形制的子集 | ETH funding 定向实测（样本不足分支）+ 金叉定向（0 样本分支） | 快路径失败改结构化错误直返（错误码+hint），消灭伪 task_id 家族 |
| G11 | 组合假设缺「登记前触发频率预估」：本轮 ETH 双重过滤假设（ROC<-5 & CCI>100）送考后验证段 0 笔——一次考试机会浪费在可预见的空转上；组合条件无预检通道（G3）+ 无频率预估 = 盲登记 | ETH 假设 b 考试实测（0 trades，bench 同返 0） | **新模块建议「假设预检器」**：G3 组合统计预检 + G11 触发频率外推（登记前按分析段频率预估验证段笔数，<5 显式警示）合成一个登记前置预检模块；最小形态可先做 translate 前置 warn |
| G12 | 衍生品数据边界未在工具面披露：funding 数据止于 2026-08-31、oi 止于 09-20，而考试/分析可请求至 09-19——含 funding 特征的策略在尾部 19 天的数据缺失行为未披露（静默截断或报错未知，未实测） | list_data_sources 与考试区间对照 | 数据缺口与请求区间的交集披露（snapshot/skipped 同款声明形制） |
| G13 | 跨品种/截面比较能力缺失：两轮研究 BTC↔ETH 的扫描结果对齐、尺度分层结构对照全部手工完成——无截面工具（多品种同条件统计、跨品种结构复制检验） | 第二轮跨品种复制全程手工对齐实测 | 远期新模块：截面统计模板（多 symbol 同条件 condition_stats/扫描对照）；近期可先在扫描/统计返回补 symbol 维度便于程序化对齐 |
| G14 | 事件驱动条件形制缺失：急跌/极端类触发频率不可控（三次考试 3/13/0 笔实证）的根源之一——固定阈值刻度表达不了「事件」语义（触发稀疏且分布不均），复利目标要求的信号密度在该形制下无法设计 | 两轮考试频率实证 | 远期：事件驱动条件形制（如「N bar 内跌幅=X」的事件定义+密度可控参数）；与 G3/G11 同属表达力升级族 |



## D 组 · team3.md 四条流程条文（建议固化，未固化亲核 2026-10-08）

四条均有实测偏差证据（各 1-2 次），符合条文准入标准（实测偏差证据）：

| 条文 | 实测证据 |
|---|---|
| ① security 清单转卡自动继承涉安标注 | CARD-01 组队漏标一次（developer 流程异议纠正） |
| ② 新 ResearchError 码回归面必含 tests/mcpserver/test_errors.py | CARD-22 漏拦一次（终验全量才暴露） |
| ③ 收队 TaskStop 后 ListAgents 核验清零 | 跨批次实例存活两次实测（旧 developer 误触、scenario×6 清理） |
| ④ reviewer 对质后置阶段三（批次一 developer 未入队） | 批次一对质无处投递卡死风险一次（纠偏化解） |

不固化则下次新会话重踩同类坑。

---

## H 组 · 0.3.5 实施与审查战增量（2026-10-08，add-event-driven-triggers + team3 第十一战）

> 来源：0.3.5 change 在案债务 2 项（design.md 债务登记节，TD-D/TD-E）+ team3 审查战（fix/event-triggers-review-hardening，8 卡闭环+六步闸门 4899+e2e 64）architect 专项与阶段四记债 6 项。全部**未实现**（代码现状亲核在案），随本组登记归宿落定。

| # | 项 | 代码现状（亲核） | 建议 |
|---|---|---|---|
| H1（原 TD-D） | distribution_compare「区间无数据」裸 `raise ValueError` → 500（templates/distribution_compare.py `_slice`）——数据语义（合法形制+空结果）非形制非法，research-error-channel 收编时漏 | 仍裸（0.3.5 只做时间窗守卫，未触空区间语义） | 422 结构化（RESEARCH_DATA_EMPTY 类），归 0.3.7 收尾战（v3 两版压缩，原 0.3.8 撤销） |
| H2（原 TD-E） | crossover 逐字段构造**静默丢 v2 字段** signal_sources/composition——带插件源的进化亲代子代降级为纯 rule（继承语义并集/交集/选择需独立设计，涉及插件求值成本） | 仍丢（operators.py 子代构造无此二字段，0.3.5 只补 entry_trigger_policy 继承） | 与 H4 同域合并评估（DNA 弱形制族） |
| H3（TD-F1） | templates/__init__.py 无条件聚合九模板（precheck 自装载拖 sqlalchemy/persistence 全链）——包内轻模块（param_guard）import 断链结构性不可达（CARD-02 选 a 的根因，import 行已正确待发） | 仍全量聚合 | PEP 562 `__getattr__` 懒加载，低成本独立或搭车 |
| H4（TD-F3b） | DNAModel 的 signal_sources/composition/layers 为 `Dict/List` 弱形制（extra=forbid 只挡多余键不挡值域）——坏值全程静默（executor except→空信号），比 500 更隐蔽的降级面 | 仍弱形制（0.3.5 只给 entry_trigger_policy 配了校验收口——CARD-01） | **优先级最高**：pydantic 强形制或消费点 validate 收口（沿 CARD-01 模式），与 H2+TD-14 合并为版本外微战 `fix/dna-strong-typing`（2026-10-08 v3 两版压缩定归宿——正确性风险先行不等版本） |
| H5（TD-F3a） | signal_assembly 组合参数坏值：`float(src["threshold"])` 坏串 500 + 未知 operator 静默缺省 ">"（core/analysis/signal_assembly.py:173/:176，0.2.x 存量）；验真补（2026-10-08）：:197 `float(comp.get("threshold"))`、:201 `float(weights.get(col))` 同族裸转（weighted_vote 分支），原记范围 :168-196 未含 | 仍现状（`src.get("operator", ">")` 兜底） | 422 化+operator 封闭枚举（含 :197/:201 同族），归 0.3.7 收尾战 |
| H6（TD-F3c） | api 层无全局 ResearchError exception_handler——21 处路由手动 try/except + research_error_to_http 重复（2026-10-08 验真复核自 14 处增；R5/CARD-04 的结构同族） | 仍手动形制（api/app.py 无 add_exception_handler 亲核） | FastAPI app 级 exception_handler 一次收口（测试面=既有 422 锚全回归），独立小战 |
| H7（TD-F2） | 存量 `*Feature` 平行测试体系两文件（tests/evolution/test_evolution_feature_net.py / test_validation_feature_net.py）未按 TESTING_V3 四分类归位 | 仍在 | 归 0.3.7 收尾战散债聚批（v3 两版压缩） |
| H8 | core/strategy/executor.py :609-613 英文 logger 消息（"MTF layer timeframe %r data not found…"；注释卡范围外，注释已中文化但日志文案未动；原记 :592 邻近为行号漂移，2026-10-08 验真修正） | 仍英文 | 无行为面，随手 |

**关联说明**：G14（事件驱动形制）已由 0.3.5 清账、G11（假设预检器）已由 0.3.4 清账——G 组总览行已同步标记；G13（截面工具）与 cooldown_after_exit 扩枚举均绑第三轮研究实证窗口，非独立待办。

---

## I 组 · 0.2.x 系列在册债务（TD-N 迁入，2026-10-07 债务登记唯一化）

> 来源：归档件《MyQuant-技术债务登记-需求原稿20260930》（works/设计稿/archive/）三批次全量迁入——0.2.14 规划期（TD-1..5）+ team2 交付前质量（TD-6..13）+ team3 存量审查（TD-14）。编号保持 TD-N 原编号（外部引用兼容）；字段形制从原稿（内容/为什么欠/影响与风险/偿还触发/状态）。
> 已完结三笔（2026-10-08 验真补记 TD-6）：TD-3（历史结论与新语义不可比）已偿还——0.2.14 实施落档 capability.py 版本标注+tests/README 守护行；TD-9（高周期求值重复复制）已闭合——拒绝投机性优化、等实证触发再议的观察项；TD-6（存量 symbol 清洗两处）已偿还——41fc30d 换公共函数在案（迁入时照抄原稿未复核，验真修正）。
> 迁移调和一处：TD-12/TD-13 原偿还触发为 openspec-update-change 回写 0.2.14 design 本体——该 change 已归档（openspec 归档件不再回写），偿还形制改为在受影响入口（capability 自述/相关测试锚）留澄清说明。

### TD-1 · 混合周期假设考试链断链（0.2.14 产品裁决 Q=①）

1. **内容**：混合周期假设被假设→策略转化器显式拒绝→永远无法产生策略→研究考试链三条证据路径（隔离回测/随机窗口/分段验证，均强制 `strategy_id`）全部不可达→**永远无法产生 pass/fail 证据、无法定证**——此类假设生命周期上限为 active，仅可经 abandoned/superseded 退出。另存在手工 MTF 策略挂名旁路：`run_isolated_backtest` 无谱系校验、`create_strategy` 接受任意 DNA——「不进回测」靠纪律不靠机制（与「证据-策略解耦」既有语义一致；旁路危害限假设定案，不污染策略出货）。
2. **为什么欠**：TimeframeLayer 层映射语义（role 归属 structure/zone/execution、cross_layer_logic 组装、mtf_mode）需独立产品轮锤炼，纳入 0.2.14 会使「表达自由」里程碑焦点发散。
3. **影响与风险**：研究六步流程第 5 步（样本外考试）对此类假设不可用；AI 研究者可能积累永驻 active 的假设；挂名旁路是防自欺叙事中的未闭合豁口。量级：能力级非数据级——生产库存量混合周期假设与 is_mtf 策略均为零（2026-09-29 实测），债务不随时间累积。
4. **偿还触发**：独立变更「多周期假设翻译」（假设→TimeframeLayer 映射产品轮）；偿还时同步评估 `run_isolated_backtest` 是否顺带收紧谱系校验（证据-策略解耦为有意语义，收紧与否需独立裁决）。
5. **状态**：未偿还。

### TD-2 · Lab 前端 timeframe 控件最小文案

1. **内容**：Lab 前端逐条件 timeframe 下拉控件（web/src/components/lab/RuleConditionRow.tsx）仅一句提示文案（生效语义+cross 状态窗特性），不做完整语义教学 UI。
2. **为什么欠**：完整多周期体验 UI 超出 0.2.14 展示面范围（本版展示面以 mcpserver 为主）。
3. **影响与风险**：Lab 用户对 cross 状态窗语义（一次穿越持续至下次高周期收盘）仍可能误读——提示缓解不消除。
4. **偿还触发**：Lab 多周期体验专项（用户反馈驱动排期）。
5. **状态**：未偿还。

### TD-4 · inspect_cases 双轨 warmup 推导

1. **内容**：`inspect_cases` 的 `indicator` 参数继续承担触发点指标值展示与低周期路径 warmup 推导；高周期条件的 warmup 由求值内核 `high_tf_warmup_span` 另算——两轨并存。
2. **为什么欠**：`indicator` 参数双职责短期耦合，拆分成本大于收益（展示与求值的 warmup 需求天然不同源）。
3. **影响与风险**：第三处 warmup 推导入口出现时漂移风险上升。
4. **偿还触发**：出现第三处 warmup 推导入口时提升统一（0.2.6 D4「第三个消费者出现时再议」同款纪律）。
5. **状态**：未偿还。

### TD-5 · Lab 请求无留痕→timeframe 使用面不可量化

1. **内容**：`/api/validate` 为 ad-hoc 路由（无 db 依赖、不落请求留痕）——Lab 条件中 timeframe 键的历史使用分布不可量化，BREAKING 披露只能定性。
2. **为什么欠**：validate 无留痕为既有设计；为度量而加留痕属独立决策，不为披露便利扩大变更面。
3. **影响与风险**：Lab 行为变化幅度无存量基数佐证（差分锚覆盖行为变化本身）。
4. **偿还触发**：若未来为 validate 加请求留痕（独立决策），可补实测分布。
5. **状态**：未偿还。

### TD-6 · 存量 symbol 文件名清洗两处未换公共函数【已偿还——2026-10-08 验真修正】

1. **内容**：symbol→数据文件名非法字符过滤（`re.sub(r"[^A-Za-z0-9]","",symbol)`）全系统四处各写一遍——0.2.14 已提取 `mtf_loader.safe_symbol()` 并收口本变更新增 2 处；存量 `core/validation/mtf.py`（load_mtf_data）与 `core/validation/engine.py`（validate_hypothesis）原用手写版本。
2. **为什么欠**：质量收口按「本变更引入面」界定，存量两处属既有代码不扩范围。
3. **影响与风险**：（历史登记原文）同逻辑多份拷贝——将来加强过滤规则只改公共函数时存量两处不随动，产生入口间不一致；security F-1 已实证新旧混用保留「特殊构造 symbol 探测 data_dir 外文件存在性」的 oracle。
4. **偿还触发**：版本间隙治理小任务（两行改用公共函数+关联回归）。
5. **状态**：**已偿还（41fc30d「fix(validation): base timeframe 裸拼 parquet 路径收敛共享 find_parquet」，2026-10-01）**——两处已换公共函数：mtf.py:62-64 `from core.data.mtf_loader import find_parquet, safe_symbol`+`symbol_ = safe_symbol(pair)`、engine.py:95/:107 同款（2026-10-08 验真亲核；I 组迁入时照抄原稿未复核现状，本行据实修正）。

### TD-7 · 分析模板四处重复设置求值域起点

1. **内容**：`api/routes/research/analysis.py` 四个模板分支各写一遍 `mtf_ctx.eval_start = request_start`。
2. **为什么欠**：质量观察（判不过门槛）；重复但当前无害。
3. **影响与风险**：新增模板分支漏写该句时，高周期 warmup 检查基准退回装载域起点缺省——方向保守（更严不前视），但会误拒实际够用的请求。
4. **偿还触发**：与 TD-6 同批治理任务（几行合并到装载公共处）。
5. **状态**：未偿还。

### TD-8 · 登记校验循环内重复判定基准周期值域

1. **内容**：`hypothesis_service` timeframe 校验循环对每条条件重复判定 `base_tf in TF_SECONDS`——基准周期对整个假设唯一，判一次即够。
2. **为什么欠**：纯代码整洁观察，无行为危害。
3. **影响与风险**：无行为风险；仅冗余计算与可读性。
4. **偿还触发**：与 TD-6/TD-7 同批（一行上提到循环外）。
5. **状态**：未偿还。

### TD-10 · Lab 链 then 条件周期键被静默忽略【待产品决策】

1. **内容**：THEN 条件携带 timeframe 键——登记入口（AI 链）显式拒绝（TIMEFRAME_ON_THEN）；Lab `/api/validate`（ad-hoc 链）不报错不生效：周期键触发 MTF 装载合并（engine.py:136-140 all_conditions 合并收集）但 `_check_then_conditions`（:634+）判定不消费、按基准 close 序列核验——归档 design D-r4 已定此叙事（2026-10-08 验真精化：非「静默丢弃键」而是「装载消费、判定不消费」）。
2. **为什么欠**：0.2.14 design B3 将 THEN 拒限定在登记侧，Lab ad-hoc 通道行为显式保持现状。
3. **影响与风险**：同一错误输入两入口相反对待——Lab 用户给 THEN 条件挂周期会误以为生效（静默降级纪律的跨链残余）；若 Lab 前端 then 行提供周期控件则构成真实 UX 陷阱。
4. **偿还触发**：产品决策二选一——(a) Lab 同步明确拒绝（与登记侧一致，工程半小时级，推荐：THEN 跨周期语义未定义前允许生效=引入未定义语义）；(b) 定义 THEN 跨周期前向窗口语义并允许（需独立产品轮）。决策后随治理任务落地。
5. **状态**：未偿还（阻塞于产品方向拍板）。

### TD-11 · lookback 族动作残余键静默缺省（原记「500 裸穿」——2026-10-08 验真改性质）

1. **内容**：F-2 修复覆盖 touch_bounce/role_reversal 三数值键（`engine._cond_number` 结构化拒绝，坏值 422）；lookback 族 `engine._lookback_condition` 的 **window 键已由 febe47b（2026-10-01）收口**——坏值全异常族（TypeError/ValueError/OverflowError）→ResearchError 422「condition.window 须为整数」+window<1 拒绝（engine.py:540-556，验真亲核）。**残余真债=target/inner_action 键静默缺省族**：①target 坏串 float 失败静默回退 threshold=0（:559/:563-567）；②target=dict 仍 TypeError→500、target=None→NaN 恒 False 静默（venv 实测）；③inner_action 非法值静默缺省 price_above（:558/:576-578）。同族：`_check_then_conditions`（:664-667）target 坏串同样静默 0。
2. **为什么欠**：F-2 按 security 报告范围界定收口了 window（原稿「window 仍裸」的描述自 febe47b 起过时——I 组迁入照抄未复核）；target/inner_action 键属同函数不同分支未扫全。
3. **影响与风险**：静默缺省比 500 隐蔽（与 H5 同族）——坏 target 让回看条件变「threshold=0 的恒真/恒假比较」，研究结论与输入意图错位且无提示。
4. **偿还触发**：复用 `_cond_number` 同款收口 target/inner_action（engine.py:558-578 单点，极小）——与 TD-7/8 治理批或 H5 归宿搭车。
5. **状态**：未偿还（范围收窄：window 已清，余 target/inner_action 两键）。

### TD-12 · design 末 bar 脉冲措辞两可（错读会引入前视）

1. **内容**：0.2.14 design B1「末 bar 窗口=+tf」两种读法——实现采正确读法（+tf 挂载机制，未收盘脉冲不落位）；另一读法（末 bar 兜底窗收未收盘脉冲）违反 asof_completed MUST。
2. **为什么欠**：openspec 工件编辑须走逐笔确认协议，交付前未发起。
3. **影响与风险**：文档地雷——未来读者（含 AI）按错读法实施会引入前视缺陷；代码正确但文档未澄清。
4. **偿还触发**：design 已随 change 归档不再回写——在受影响入口（capability 自述 asof_completed 段/相关测试锚）留一句无歧义澄清说明。
5. **状态**：未偿还。

### TD-13 · 实施三处与 design 字面的调和未回写 design 本体

1. **内容**：①warmup 按条件实际参数粒度推导（indicator_convergence，design B1 字面为名字级 max）；②warmup 检查基准=求值域起点 eval_start（design B2 字面为 work.index[0]）；③策略链 tf 由索引差分中位数推断（infer_tf_delta+TfLike，design B1 未列）。三处行为全部正确且测试钉死，已记录于规划文档 0.2.14 实施裁决段；design 本体字面未同步。
2. **为什么欠**：调和发生在实施期，design 本体按规划工件冻结未回写。
3. **影响与风险**：文档与实现不同步——按 design 字面重新实施的读者会得到行为不同（且两处更差）的版本。
4. **偿还触发**：design 已随 change 归档不再回写——随 TD-12 同批在受影响入口留澄清说明（实施裁决段为准）。
5. **状态**：未偿还。

### TD-14 · 假设翻译同签名分流不含阈值——策略变体拿不到自己的策略

1. **内容**：translate 同签名分流（`core/analysis/hypothesis_to_strategy.py:170-177`）复用判定只看 `gene_signature`，而签名构成（`core/strategy/dna.py:272`）的规则基因段只编码「指标(全部 params):动作类型:角色」、不含 `condition.threshold`——同指标同参数同动作、仅阈值不同的假设变体，翻译时命中同签名旧策略直接返回旧 id，永远拿不到自己的策略；与签名 docstring「参数值不同→签名不同」自相矛盾（验真补充：:296-299 v2 插件源段**已含** threshold/operator——缺口限 rule 基因族）。实核三处亲验：签名构成漏阈值、分流拦截在案；下游两层（落库 `repository/strategy.py:53-58` 草稿免去重、转正 `repository/research.py:226-229` 按谱系定位）均不做签名二次拦截。
2. **为什么欠**：用户裁决挂账不修（2026-09-30 team3 CARD-14 停滞呈报后重裁）——修复方向存在分叉：改签名构成波及 7 个消费面（translate 分流/策略库去重/进化排重/diversity 统计/进化跑批/API 列表兜底/存量清重），存量指纹全失效需一次性迁移，属版本级改动；「复用前精确复核」为最小改动方案但同指纹多条并存的语义取舍需产品确认。
3. **影响与风险**：研究域「同指标不同阈值」策略变体无法并存（变体假设复用旧策略）；签名字段实际语义与 docstring 声明不一致，后续读者按文档理解会误判。
4. **偿还触发**：专项处理时首选方向 b——复用前加 dna_json 等价复核（两侧经 `from_json(to_json())` 往返规范化后比对，先例 `hypothesis_to_strategy.py:168-170`；实核依据在案：落库与转正两层均不拦截，改动单点）；方向 a（签名构成补 condition 全键）需配套存量迁移另案。tester 特征网建网已用 RSI:gt 场景绕开该缺陷，现有测试无依赖。
5. **状态**：未偿还（用户裁决挂账）。

### 披露项 · executor legacy 层分支与 mtf_engine 双路径并存（既有结构债）

- `executor` 对 `mtf_mode is not None` 的 is_mtf DNA 委托 mtf_engine，其余走 legacy 层分支——双路径消费同一 `resample_signals`，0.2.14 收口已覆盖两路径，但双路径本身的结构收敛未做。偿还触发：进化引擎重构类变更顺带。状态：未偿还（既有）。

---

## E 组 · 无需行动（说明在案）

- **macd_histogram 裸名留白**：不可达名放行是设计留白（冒充不了真指标列——真列名恒带参数段或 pattern_ 前缀），刻意未锚死。
- **schemas threshold_profile 保持 Dict**：CARD-03 方向 B 的既定结果（HTTP 形制不变是裁决内容），收紧 schema 会破坏 B 的零契约承诺——不动是正确的。

---

## 决策建议汇总

1. **A1 + B1-B5 合并一个分支做**（`fix/debt-batch-1`，除 A2 外都是零/低行为面，一个呈批收口）；**A2 单独立项**（触碰契约+调用方适配，实施时单独呈批）；**B6 缓办**（与下次依赖升级专项合并）。
2. **C1 裁 A（恒收紧）或 B（维持现状）**；**C2 不急**，建议下次翻 scoring 域测试时逐例过（或指定哪几例现在裁）。
3. **D 组固化**即改 team3.md（四条一次写入）。

各项独立不耦合，可批复任意子集。

---

## 裁决记录

| 日期 | 项 | 裁决 | 执行状态 |
|---|---|---|---|
| 2026-10-01 | C3（CARD-H1 NEW 语义）+ C4（fetcher 代理默认值） | 记录为技术债务，本次不做修复 | 已登记（本文档 C3/C4 节） |
| 2026-10-01 | 第三战交付呈批（23 卡 / 覆盖率 100% / 全量 3679+e2e 四轮） | 批准合并 | 已合并 main ff425e3（--no-ff），B1/B3 清账、B2 部分清账（F 组新增 F1-F8） |
| 2026-10-01 | fix-mcp-1 上游战交付呈批（14 卡 P0×1+P1×13 / mcpserver 4 文件 100% 行+分支 / 全量 3817+e2e 64） | 批准合并 | 已合并 main c37c067（--no-ff，30 笔提交），A1/F9/F10 清账，登记 F 组新增 F9/F10；fix-mcp-2（evolution+validation）待启动 |
| 2026-10-02 | fix-mcp-2 下游战交付呈批（33 卡 P0×4+P1×17+注释组 12 / scene 覆盖 6-31%→96-100% / 全量 4082+e2e 64） | 批准合并 | 已合并 main 89c72cd（--no-ff，48 笔提交），B2 全清、B5 件①②、F3/F7 清账；**fix-mcp 系列全系列闭环**；C1/C2 口径呈批顺延（实施面不在本战域：C1 在 analysis、C2 在 scoring——随下战或单独裁）；新增观察项：cross_timeframe signal_type 名义参数、有效性测试同文件两处单轨迹锚（与已修的 20-seed 系同族） |
| 2026-10-03 | 0.3.1 api 硬化战交付呈批 + 复批（44 卡=主批 40+复批 4 / 终验 4387+e2e 64×3 / 覆盖率 18/25 文件 100%） | 批准合并（含 PB-1 认证四件套 key 未设 A 方案 / PB-2 死端点删除 B10+D6 / PB-3 心跳裁撤三项推荐方案全批） | 已合并 main a88420a（--no-ff，72 笔提交，60 文件 +5928/-1332），A2/F4 清账，S-1~S-8 全关闭；新增 TD 在案：pytest 双模块身份坑（conftest 统一留专项）、C3b 口径顺延、低危 4 项（进化 pending 无上限/discovery 数值参数/scene horizons 空列表/config.json 存量占位值）、专项 5 项（mcpserver :8800 认证面、core 侧公共入口×2、启动器治理、paper_trading 心跳同族观察）、P2/P3 记录 10 项；F3 双形态分裂随 CARD-01 修复、D6 discovery 死端点与 B10 占位端点随 CARD-33 删除、心跳死信号随 CARD-34 裁撤、三处文档失实修正 |
| 2026-10-03 | scripts 目录清理 + 启动器统一（用户两轮裁决：B 组照清 / 单一入口 scripts 套、MCP 默认同起） | 批准执行 | 已合并 main 9ab2023（清理：-2183 行，verify_×4/migrate_column_refs/check_coverage_thresholds/根三件/api_run.py/logger.sh 删除，F1 执行位清账，start.sh 0.0.0.0 失实显示修正；**MYQUANT_API_HOST 仅修 export 传导——2026-10-08 验真核：Python 侧零消费、`api/__main__.py:22` 硬编码 127.0.0.1，跨机开启通路不通〔保守方向〕，消费端接线随 A2 跨机启用检查点同窗口**）+ main 5c61546（统一：start.sh 默认三服务同起 API+MCP+Web、stop.sh 逆序全停、run.py/dev.sh 删除、五处失实启动指引修正含 agent 可读能力自述、F3 锚定网与 MAP.md 同步）；**启动器治理专项闭环**；验证 694+1409 passed、统一入口实测起停一轮 |
| 2026-10-03 | MCP 六步法实战复盘 8 项（首次全链研究 18 类调用实测：G1 P1 bug + G2-G4 契约失实 + G5-G8 能力改进） | 记入技术债务，暂不修代码 | 已登记（本文档 G 组 G1-G8）；修复建议：G1 立项修（一处校验前置）、G2-G4 便宜同批、G5-G8 排期；研究主记录 hyp_07979a1d7ad7（考试 inconclusive 留痕可查） |
| 2026-10-03 | 第二轮多方向研究总结（ETH 跨品种复制+三方向定向+双假设送考）与 G9-G14 登记 | 记入技术债务，暂不修代码 | 已登记（G9-G12 第二轮实测 + G13 截面工具/G14 事件驱动形制 + G11 升级「假设预检器」模块建议）；研究结论在案：尺度分层结构跨品种复现（最坚实发现）、~5000 检验对 FDR 全灭（简单规律不存在于该表达域）、三假设考试全 inconclusive（基准碾压+频率硬伤）；研究记录 hyp_5fc1dc53d03b / hyp_b2d36a31700e 留痕可查 |
| 2026-10-03 | 版本编号统一：MCP 修复系列并入 0.3.x 线（原 MCP-2.1~2.4 → 0.3.2~0.3.5） | 批准执行（用户指令「从 0.3.2 开始进行规划和重构，以便后续引用」） | 已执行：MyQuant-修复MCP版本规划.md 全量重构（0.3.2 错误通道修复战/0.3.3 契约透明化/0.3.4 假设预检器/0.3.5 表达力升级）；债务清偿三战顺延 0.3.6/0.3.7/0.3.8（MyQuant-债务清偿版本规划.md 同步改号）；登记文档 v2 重估段引用面同步。此后全工程单一版本序列 |
| 2026-10-04 | 0.3.2 错误通道修复战交付呈批（含小样本族收编 422 用户裁决） | 批准合并 | 已合并 main 0b3cf1f（--no-ff，14 提交）；G1/G2/G9/G10 清账（G 组标记 2026-10-08 验真回填） |
| 2026-10-05 | 0.3.3 契约透明化交付呈批（三轮外部评估+四次裁决） | 批准合并 | 已合并 main 257c044（--no-ff，18 提交=实施 5+第八战 13）；G4-G8/G12 清账；TD-1 死测试退役微分支 0ff3aa0；债务六笔落后续债务区 |
| 2026-10-06 | 0.3.4 假设预检器交付呈批（含 clear-analysis-tds 审查战） | 批准合并 | 已合并 main 8d6f8f2；G3/G11 清账（预检器模块落地+登记自动挂接） |
| 2026-10-07 | 0.3.5 事件驱动形制交付呈批（实施 20/20+第十一战 8 卡+六步闸门 4899+e2e 64） | 批准合并 | 已合并 main 28e7efc（--no-ff，含审查战 fe0e406；48 文件 +5856/−986）；G14 清账；H 组 8 项登记；openspec 归档+trigger-policy 主 spec 新建 |
| 2026-10-08 | 债务登记唯一化（TD-N 原稿未偿项迁入 I 组+三件归档）与登记册真实性验真战（team3 十二战：60 项对拍——属实 42/部分属实 17/失实 1） | 批准执行（唯一化为用户指令；验真战用户指令） | 唯一化已提交 84e1bbc；验真修正本批落档（23 卡：G 组清账回填/TD-6 已偿还/TD-11 改性质/F2 残余 1 处/B4×7/B6×15/H6×21 等+姊妹篇四处同步） |
