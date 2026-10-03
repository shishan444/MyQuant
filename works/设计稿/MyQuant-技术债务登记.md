# MyQuant 技术债务登记

> 登记日期：2026-10-01（同日 v2 价值重估）
> 来源：team3 三战交付报告在案项（scoring 0cdd4a9 / analysis bc33510 / data+signals ff425e3）+ 流程实测教训
> 维护规则：每项含四问（问题/方案/作用/影响）与推荐处置；裁决后勾销或转入实施；新债务续编号登记

> **v2 价值重估（2026-10-01，用户批准）**：全部在案项按「真实暴露 × 后果量级」重估——①A2 为唯一「现在就真实、后果严重」项，升格为**实盘部署前置能力建设**（非清债）；②未审域不可知敞口大于登记项（三战每战挖出登记外新 P0）；③卫生项（F1/F2/F3/F4/B4、B5 大半）降级搭车或接受现状，不立版本。重排后版本序列与各项新处置见姊妹篇 MyQuant-债务清偿版本规划.md（v2）：0.3.1 api 硬化（部署面）→ 0.3.2 trading+live_trading（资金面，live_trading 原漏审已补入）→ 0.3.3 persistence（数据面）→ 0.3.4 evolution+validation（研究面）。

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
| B5 | scoring 小件三件（死字段/NaN 静默/缺 warning） | scoring 战 | **件①②已清账**（fix-mcp-2：CARD-08 template_name 死字段/CARD-12 evaluation.py 吞异常报错化——2026-10-02 合并 89c72cd）；件③ knn_engine NaN 静默留 0.3.4 前置呈批（scoring 域外未随战） |
| B6 | pct_change 弃用参数 ×12 | scoring 战 | 缓办（与依赖升级专项合并） |
| C1 | 组合策略显式 default 语义 | analysis 战 TD-1 | 需裁口径 |
| C2 | scoring D 类现状容忍 4 例 | scoring 战 | 需裁口径 |
| C3 | ImportMode.NEW 三态两值（new=replace） | data/signals 战 CARD-H1 | 已裁决登记（2026-10-01 本次不修，待将来裁语义方向） |
| C4 | fetcher 硬编码内网代理默认值 | data/signals 战 CARD-15 拆出 | 已裁决登记（2026-10-01 本次不修，保留现状） |
| D | team3.md 四条流程条文 | analysis 战实测 | 建议固化 |
| E | macd 留白 / schemas Dict 保持 | analysis 战 | 无需行动（已含说明） |
| G | MCP 六步法实战复盘 8 项（G1 P1 bug + G2-G4 契约失实 + G5-G8 能力改进） | 2026-10-03 首次全链实战 | G1 建议立项修；G2-G4 便宜同批；G5-G8 排期 |

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

### B4 · kernel 私有名公共化（TD-2）

1. **问题**：`_p_value_for` / `_mask_for` / `MtfEvalContext` 等 condition_kernel 私有名被四个模板跨模块 import——Python 反模式，已有固化先例（CARD-08 实施时按一致性接受）。
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

### C1 · 组合策略显式 default 语义（TD-1）

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
| F1 | dev.sh 调用 start.sh 无执行位（git 100644，干净 checkout 必现 Permission denied） | 阶段四 e2e 双形态补测 | 一行修（调用改 `bash "$SCRIPT_DIR/start.sh"` 或三脚本补 x 位入库） |
| F2 | 范围外内联 re.sub symbol 清洗 8 处（trading/evolution/validation/api 各域）未收敛 safe_symbol | architect 全库检索 | 与下次对应域审查合并做 |
| F3 | formula 窗口**数值上界** DSL 化（validate_formula 加与 plugin_service 共享的上限常量）——B2 剩余面 | B2 口径更正 | **已清账**（fix-mcp-2 CARD-18，2026-10-02 合并 89c72cd） |
| F4 | api/routes/data.py 非触碰端点存量 25 行未覆盖（数据集详情/预览/删除/来源/更新） | 阶段四覆盖闸门 | **已清账**（0.3.1 特征网 test_data_f4_gaps 12 用例全盖，2026-10-03 合并 a88420a） |
| F5 | api 非法 mode 静默回退 MERGE + api/core ImportMode 枚举双表定义 | reviewer/architect 报备 | 与 C3 裁决联动，api 域审查时一并 |
| F6 | core/trading/runner.py:443 _load_data 读取拼接无校验（S-3 同族读取面） | architect 专项 | trading 域审查时一并 |
| F7 | plugin_service.py:258 ast.parse 无 RecursionError 防护（DB 历史数据防御面，注册面已挡） | architect 专项 | **已清账**（fix-mcp-2 CARD-19 补 (RecursionError, MemoryError)→set()，2026-10-02 合并 89c72cd） |
| F8 | mtf_loader 两装载函数七步同构收敛 / formula 五表结构化收敛 | P2 不修记录 | 无实际漂移/回归面大，维持不动 |
| F9 | mcpserver TOOL_TIERS 失联——research 档元组漏 inspect_cases/query_indicator/run_staged_validation 三工具名，能力自述（quant://capabilities）工具清单缺检查③④⑤工具且文档自相矛盾；四道测试网全漏拦（单向子集断言） | fix-mcp-1 战前链路分析（lead/reviewer/tester 三方互证） | **已清账**（CARD-01 元组补三名+tester 等式对拍断言，2026-10-01 合并 c37c067） |
| F10 | 研究日志分页 total=当页条数（log.py 两端点+hypotheses）——agent 续读研究无终止信号；对照 strategies 域真总数语义并存两套 | fix-mcp-1 战前链路分析（reviewer Q3 扩面核实） | **已清账**（CARD-07 随 keyset 改造一并：三端点 total=真总数+next_cursor，2026-10-01 合并 c37c067） |

---

## G 组 · MCP 六步法实战复盘（2026-10-03，首次全链研究实测）

> 来源：第一次六步法量化研究全链实战（BTCUSDT 四周期矩阵扫描 2167 检验对 → 三维检查 → 假设 hyp_07979a1d7ad7 → 样本外三路考试，全程 18 类 MCP 调用）。实测肯定面在案：FDR/warmup/幂等/留痕纪律全部按设计工作、param_sweep 无效点信息完备、inspect_cases 数据质量良好。以下为实测缺陷与改进项。

### G1 · condition_stats 快路径裸 KeyError + 伪 task_id 轮询陷阱（P1，建议立项修）

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



四条均有实测偏差证据（各 1-2 次），符合条文准入标准（实测偏差证据）：

| 条文 | 实测证据 |
|---|---|
| ① security 清单转卡自动继承涉安标注 | CARD-01 组队漏标一次（developer 流程异议纠正） |
| ② 新 ResearchError 码回归面必含 tests/mcpserver/test_errors.py | CARD-22 漏拦一次（终验全量才暴露） |
| ③ 收队 TaskStop 后 ListAgents 核验清零 | 跨批次实例存活两次实测（旧 developer 误触、scenario×6 清理） |
| ④ reviewer 对质后置阶段三（批次一 developer 未入队） | 批次一对质无处投递卡死风险一次（纠偏化解） |

不固化则下次新会话重踩同类坑。

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
| 2026-10-03 | scripts 目录清理 + 启动器统一（用户两轮裁决：B 组照清 / 单一入口 scripts 套、MCP 默认同起） | 批准执行 | 已合并 main 9ab2023（清理：-2183 行，verify_×4/migrate_column_refs/check_coverage_thresholds/根三件/api_run.py/logger.sh 删除，F1 执行位清账，start.sh 0.0.0.0 失实显示与 MYQUANT_API_HOST 传导断裂修正）+ main 5c61546（统一：start.sh 默认三服务同起 API+MCP+Web、stop.sh 逆序全停、run.py/dev.sh 删除、五处失实启动指引修正含 agent 可读能力自述、F3 锚定网与 MAP.md 同步）；**启动器治理专项闭环**；验证 694+1409 passed、统一入口实测起停一轮 |
| 2026-10-03 | MCP 六步法实战复盘 8 项（首次全链研究 18 类调用实测：G1 P1 bug + G2-G4 契约失实 + G5-G8 能力改进） | 记入技术债务，暂不修代码 | 已登记（本文档 G 组 G1-G8）；修复建议：G1 立项修（一处校验前置）、G2-G4 便宜同批、G5-G8 排期；研究主记录 hyp_07979a1d7ad7（考试 inconclusive 留痕可查） |
