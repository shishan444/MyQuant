"""能力自述（add-ai-research-mcp 4.5 / improve-mcp-agent-loop M4/M5）。

五部分（spec mcp-server）：工具清单、验证协议要点、草稿-转正生命
周期、策略可表达边界、假设状态机规则。工具清单运行时从注册集生成；
协议要点为静态权威文档（与 specs 同源维护）。自述含慢通道预期管理
（需求单只持久化不自动实现——避免 AI 提单后空等结果）。
"""

CAPABILITY_DOC = """# MyQuant 研究通道能力自述

> 接入形态：本服务以 streamable HTTP 提供（随统一入口 `./scripts/start.sh`
> 启动，默认 http://127.0.0.1:8800/mcp）。宿主一次配置 URL 即用：
> `claude mcp add --transport http myquant-research http://127.0.0.1:8800/mcp`

## 1. 工具清单
{tool_list}

## 2. 验证协议要点（必须遵守，工具与端点强制执行，不依赖你自觉）
- **隔离纪律**：验证类工具拒绝与「判定区间」重叠的请求。判定区间 =
  假设登记时自报的 data_ranges ∪ 该假设全部研究日志记录的区间（并集）。
  漏报没用——日志是系统自动记的。
- **时间参数规约**：验证与分析的时间参数统一按 UTC 解释；未带时区的
  naive 时间（如 2024-01-01）按 UTC 处理。
- **warmup 推导式**：隔离回测自动前扩预热 =
  max(200, 跨指标取最大的(周期类参数注册表上限之和 × 3))——单指标
  按周期类参数上限求和(保守取向)；含公式插件时取
  max(内置推导, plugin_formula_lookback)；高周期条件掩码链为
  max(条件实际参数 × 3, 20)，bar 数按该轴周期折算为前扩时长（1d 轴
  42 bar = 42 天，非 42 根基准 bar；下限 20 与主公式下限 200 分属
  两式）。结果会声明实际 warmup；数据起点不足时显式拒绝（错误
  detail 携带 warmup_bars 与 rule 全文）。
- **门槛机械判定**：通过与否由预定义门槛表（盈利>0、回撤<25%、
  交易数>=5）决定，不是你的判断。
- **组合策略加严**：引用多个信号源的策略验证门槛更严。
- **同种子复现**：随机窗口抽查记录种子，同种子同参数结果一致。
- **幂等**：同条件集+区间集的假设登记返回既有 id；同区间的验证
  重复请求返回既有记录（不会重复计算）。
- **事件策略层（trigger_policy）**：假设登记与分析模板（event_study/
  condition_stats/precheck_hypothesis）可选携带 trigger_policy——
  条件集整体级（修饰 when 全集 AND 合并掩码如何变换为事件流），
  三值封闭枚举 {none, first_true, suppress_bars}：none=缺省等价；
  first_true=连续段仅首根计一次；suppress_bars 必带 bars（正整数，
  事件后 N bar 抑制跨段生效，间隔恰 N 产生新事件）。策略参与假设
  指纹与幂等身份（不同策略=不同假设）；带策略求值全链同口径：验证
  引擎/预检外推/分析模板/策略入场组装消费同一权威变换（五链一致），
  预检外推与统计链实跑为事件数对事件数严格闭环（考试链成交笔数另受
  空仓进场抑制影响，不作对照）。翻译直接映射进策略声明（不静默
  降级）。事件形制整理触发不创造触发：0 事件形态照常
  reject_suggestion；bars 上界按求值数据长度校验（超界 422）。
- **沙盒回测纪律**：run_backtest 是快速试错通道（可调手续费/滑点，
  直返指标、不构成验证证据）；挂 hypothesis_id 的沙盒区间**计入判定
  并集**——此后同区间的隔离验证会被拒（先偷看不获利）。
- **时点行情快照**：get_market_snapshot 返回 as_of 时点多周期五维
  行情卡（trend/momentum/volatility/volume/derivatives——读数+90 日
  分位+三态判读；六步研究第①步固定入口）。读数与分位闭端截至
  as_of（防前视）；衍生品维无数据时如实标 no_data；每次调用落
  research_log 留痕（adhoc，不进判定并集）。
- **案例抽查**：inspect_cases 返回候选条件（或点名时间戳）的真实
  触发案例窗口（每案例触发点前后 context_bars 根原始 OHLCV+指标值；
  六步研究第③步检查③——统计判定对数据质量结构性失明，本工具是
  全流程唯一原始 K 线检视入口，上限即预算：案例 ≤5、窗口 ≤20、
  点名 ≤5，超限结构化拒绝不静默截断）。命中超限等距取样（首尾
  保底、n_matched 披露命中总数）；start 缺省=全量+effective_start
  有效域披露；ref_id 用条件指纹与 param_sweep 留痕同键（检查①③
  可对链）；拒绝路径零持久副作用。
- **指标读数**：query_indicator 单指标任意参数现算，归约摘要直答
  「该参数下指标在这段数据上什么样子」（六步研究第③步检查④——
  检查链①清单②信号列③案例④读数闭环，「参数是策略的一部分」的
  读数落地）。reductions 四选多：latest/quantiles（七点分位）/
  regime_means（四态分组均值+count）/trend_slope（斜率），缺省前
  两者；params 缺省=注册表 default（echo 披露实际生效值），键值
  越界/多余键结构化拒绝；多输出指标全列独立归约，个别列冷启动
  未完成该列为 null（键保留）。求值域 [effective_start, end]（end
  缺省=数据末端），warmup 段不入归约；序列不外发（报表压缩
  ≤2048 字节）；零写入零留痕（读数与元数据查询同级）。
- **批量读数（0.4.1）**：query_indicator 可选 groups=[{indicator,
  params?, reductions?, start?, end?}] 与单组字段互斥（同传 422）；
  组数 1..8、各组区间独立、返回 {"groups":[…]}（请求序为键）；整批
  原子含执行期（任一组失败整批 422 无部分结果，detail 以 groups[N]
  指明组序）；载荷分档单组 2048 / 批量 16384 字节。
- **指标参数修复中间态（2026-10-13）**：BB 的 std 与 Keltner 的
  atr_period/multiplier 传参修复已生效——全链路新生成的计算数值
  正确。历史存量数字仍为旧语义：BB 存量中 std≠2 的数值实按 2.0
  计算（存量重算由后续批次承载后撤下本条）；Keltner 存量全部为
  吞参语义（atr_period 从未生效；重算边界待需求侧裁决，见
  works/设计稿/技术债务-2026-10-13.md）。对照历史研究档案数字时
  须知此差异。
- **比较动作全集（fix-research-c2 起）**：价格比较动作
  price_above/price_below（价格在指标线上方/下方——比较主体固定
  close，target 为占位值被忽略）在统计面与翻译面两侧均已可用；
  未识别比较动作结构化拒绝（422 附 legal_values=动作分派表键集，
  不再以全 False 掩码静默降级）。
- **组合条件（0.4.1）**：condition_stats/event_study 支持 conditions
  条件数组（AND 语义，条数 1..5，每条混合形制含可选高周期
  timeframe 键——掩码投影）。兼容锚=参数键形制：旧 condition 键
  逐字节不变；conditions（含长度 1）统一走引用并集前扩装载
  （warmup 预算=全部条目引用并集）；event_study 两形制存在首段
  收敛性差异（旧键无 warmup_bars 披露键、conditions 有——详见
  run_analysis 说明）。
- **区间事件摘要（0.4.1 segment_narrative）**：输入区间输出时间线
  事件清单（pattern_trigger/indicator_cross 封闭三对/band_position
  三轨道进出/sustained_run 连续同向段）+结构统计；工程算事件 AI 读
  事件——载荷无原始 OHLCV 行；双上限区间 ≤5000 bar、事件 ≤500 条；
  默认事件源=注册表 trend/momentum/pattern 类+轨道族机械推导（不
  入集类别披露，indicators 点名不受限）；留痕 analysis 型。
- **判定并集余量查询（0.4.1 union_balance）**：只读查询某假设的
  已判区间/剩余可用区间/保守折算剩余 bar 数（三参数必填；数据范围
  按 parquet 首尾 bar 完整时间戳直读，半开）；与隔离验证重叠拦截
  同一账本两出口——查询 remaining 段发起隔离验证不被拒；零副作用
  不落日志；假设不存在 404。
- **反例画像（0.4.1 counter_profile）**：条件触发自动分符合组
  （窗口内任一时点实现方向预期——区间语义）/反例组，全指标特征
  分布对比（八键；特征域=注册表默认参数全输出列减 guard_only/
  lazy）按 Cohen's d 排序输出差异清单——工具只呈现差异不生成条件
  （特征选择由 AI 做）；反例组 <30 标 insufficient 不拒、反例组 0
  合法；总触发 <30 走既有小样本拒绝。
- **退出参数扫描（0.4.1 exit_sweep）**：条件固定下按止损模式分组
  扫退出参数网格（Σ≤50 组；模式合法集=引擎真实分派集引用式——
  0.4.1={pct}），逐组独立回测（含费滑、与隔离验证同一执行链——
  条件经翻译构造内存 DNA 不落库）输出五指标+三道门平台判定
  （最差聚合全序 unsupported>spike>indeterminate>plateau，标尺内建
  不可传；单轴长度 1 恒不成平台属判定语义）；校验期整批 422/执行
  期单组容错（两期分野）；条件不支持 timeframe 键（翻译链防线
  RESEARCH_NOT_EXPRESSIBLE——condition_stats/counter_profile 掩码
  路径支持，能力域即此差异）；**本模板不挂假设**：留痕检查型不进
  判定并集（检查不消耗验证区间）；组数 >10 走 202+轮询。
- **分段验证**：run_staged_validation 按市场状态切段逐段独立回测
  （检查⑤分题型成绩单——「哪类行情不行」的正式度量，与随机窗口抽查
  互补：抽查答任意时段、分段答状态归因）。双模互斥：auto（0.2.7
  机械判读 gates 内建）/explicit 段列表（恰好覆盖、相接不重叠）。
  段级门槛沿用既有三段闸门逐段适用，段级 verdict 三态 {pass, fail,
  inconclusive}（短段样本不足/方向不可区分判 inconclusive——证据
  不足非证伪）；汇总三键（最差段/段间差异/全段达标），总判定最差
  聚合三态。挂假设且全段 pass 产生可被 transition 引用的 pass 记录
  （与隔离验证叠加不替代），inconclusive/fail 同样落记录但不可引用；
  不挂假设 adhoc 纯日志（verdict 照报、record_id=None）。验证区间
  不入判定并集（防同区间重复靠幂等——与隔离回测同语义）。

## 3. 草稿-转正生命周期（转化 → 验证 → 定案转正）
- translate 对 active 假设产出 **draft 草稿**（不要求先有 pass 记录）：
  WHEN 条件 → 入场信号，THEN 条件按「预期兑现即离场」→ 出场信号；
  两者均不可表达时显式拒绝。
- **策略层参数声明块（0.4.2）**：register_hypothesis 可选
  strategy_params（stop_loss/take_profit/sl_mode/position_size/
  leverage/exit_fraction/add_fraction/max_exposure——值域按 sl_mode
  分叉：pct/trail 档 [0.005,0.20] 比例值、atr 档 [0.5,5.0] ATR 倍数；
  sl_mode∈{pct,atr,trail}）。translate 映射进产物 risk_genes（返回体
  risk_genes=declared；无声明=平台默认 platform_default）。**声明块与
  假设身份正交**：不进条件指纹与幂等身份（同条件集+同区间集=同一
  假设），参数变体经终态后带新声明重登记产生；幂等四分支详见工具
  文档（{}=显式声明「无参数」≠键缺失）。
- **分批条件组（0.4.2）**：exit_partial_conditions（部分离场→
  REDUCE_TRIGGER）/add_trigger_conditions（加仓触发→ADD_TRIGGER）/
  add_suppress_conditions（加仓抑制→ADD_INHIBIT——任一抑制条件成立
  的 bar 不加仓）三可选组键，组内 {subject,action,target} 与 when/then
  同构；**组键进条件指纹参与假设身份**（触发条件属研究主张）；分批
  数值参数（exit_fraction 缺省 0.5/add_fraction 0.1/max_exposure 1.0）
  经 strategy_params 声明块承载（有组无值自动归一化）。仅回测链执行
  ——交易三链（回放/模拟/实盘）对分批角色与 trail 模式装载即拒。
- **止损模式三分派（0.4.2）**：pct（入场价比例，存量语义）/atr
  （止损=入场价∓倍数×ATR，按 DNA atr_period 动态取列，数据不足显式
  拒）/trail（自最高价/最低价回撤达比例全部离场，多空对称）——回测
  引擎真实分派；exit_sweep 网格按模式分档取值域（量纲双关）。
- draft 即验证对象：对 draft 跑 run_isolated_validation——**验证的就是
  将来出货的确切 DNA**。draft 的 DNA 不可经 PUT 修改（改条件请重新
  translate）；draft 不出现在默认策略列表（显式 status=draft 可查）。
- transition(supported) 时，若 draft 自身有 pass 记录，则同事务自动
  转正（draft→active）；若 pass 挂在其他策略上而 draft 未验证，假设
  照常定证但 draft 不转正（不会调包出货未验证的 DNA）。
- 与库中既有策略同基因签名时显式分流标注（same_dna_existing），
  不会静默丢弃；签名命中还须策略层参数九字段全等（含 atr_period）
  才分流——参数异必建新实体（参数变体不被吞并）。
- get_validation_report 携带 **lineage 谱系段**（0.4.2，纯增量）：
  报告实体回链 parent 假设→同身份族（同条件集+区间集的全部假设含
  终态后重登记变体）的全部历史翻译实体（draft/active/archived）与
  各自三级验证记录计数/verdict 分布、is_current 标注——重新翻译后
  旧实体考试记录归属清晰可辨。

## 4. 策略可表达边界（假设→策略转化器的限制，显式拒绝优先）
- 可表达：阈值比较（gt/lt）、穿越（cross_above/cross_below）、
  回看类条件；插件信号（公式/模型）绑定为信号源。
- 公式插件 DSL 白名单 21 词（0.2.12）：时序变换 11 + 时序结构
  crossover/crossunder + 逐元素 max/min/gt/lt/eq/logical_*——
  金叉死叉等穿越结构可在公式层一次表达，产出布尔列直通假设条件
  与策略翻译（真=触发，target 推荐 gt 0.5/ge 1/eq 1）。
- 不可表达（转化会被拒绝并说明原因）：跨币种条件（WHEN BTC、
  THEN ETH）、分布形态类条件、无 subject 的模式条件、**条件携带
  timeframe 键的多周期假设**（0.2.14——TimeframeLayer 层映射的
  role 归属与跨层组装语义待独立版本产品决策；登记、条件求值类
  分析与案例检查不受影响，但研究考试链（隔离回测/随机窗口/分段）
  在翻译支持前不可用于混合周期假设——此类假设生命周期上限为
  active，仅可经 abandoned/superseded 退出）。
- 不可表达时走慢通道：提交 feature_requests（只保证持久化与可查，
  **不会自动实现**——不要提单后空等结果）。
- **pattern_mining 为实验性**：特征自动挑选（按前缀 ~16 维，非全列）、
  特征 NaN 填零、区间头部时点特征有偏（无 warmup）——结论只作探索
  线索，不作定案证据；引用列不在模板可用域时结构化拒绝附清单
  （指标条件请走 condition_stats）。

## 5. 假设状态机
```
登记 = active
  ├─(证据 record_id，verdict=pass)→ supported ── 谱系 draft 有自身 pass
  │                                            → 同事务转正（draft→active）
  ├─(证据 record_id，verdict=fail)→ refuted（谱系 draft 永久保持 draft）
  ├─(新假设 hypothesis_id)────────→ superseded
  └─(放弃说明，免证据)───────────→ abandoned

inconclusive 记录（0.2.11 证据闸门：样本不足/每笔收益均值 95% CI 跨零）
  → 不触发任何迁移边（supported/refuted 须 pass/fail 严格匹配；draft
    转正须 pass；组合分量证据须 pass——四不通路全堵），假设保持 active，
    可换未消耗区间继续验证。inconclusive 是证据记录的属性，不是假设
    的状态（状态机零改动——一次样本不足的验证不能否定假设）。
终态（supported/refuted/superseded/abandoned）：拒绝转化、新验证与
再迁移；被证伪或放弃的想法请登记新假设（可在 conditions 里引用旧
想法的修正）。
```
- 条件是机器判定的唯一来源：你登记的 when/then 结构化条件会被
  翻译与验证，statement 措辞只作展示。
- subject 混合形制（0.2.13 统一）：参数化指标用结构化
  {indicator, params, field}（GET list_indicators 查参数空间；多输出
  指标 field 必填）；基础列与插件列用字符串精确名（GET
  list_signal_columns 查询，条目附 reference_form 引用形制与可引用域）。
  字符串编码形（如 rsi_14）入口已关闭——同列同写法在假设、模型特征、
  分析模板三域通用。
- when 条目可选 timeframe 键（0.2.14 新语义，asof_completed 防前视
  纪律全域生效版本——历史结果与新语义跨代际不可直接对比）：
  仅 >= 基准周期的高周期；timeframe 修饰整条件——掩码投影求值
  （高周期轴解析+求值、布尔掩码按已收盘可见性投影回基准索引）；
  穿越类动作挂高周期=状态窗语义（一次穿越持续至下次高周期收盘，
  「穿越当根精确触发」是策略执行层脉冲职责）；低周期条件存在无歧义
  等价表达——降基准（基准周期降为该低周期、其余条件挂高周期）；
  then 条件携带键拒绝（前向收益对照的跨周期归属未定义）。
"""


def build_capability_doc(tool_names: list[str]) -> str:
    """合并静态协议文档与运行时工具清单。"""
    lines = "\n".join(f"- {n}" for n in sorted(tool_names))
    return CAPABILITY_DOC.replace("{tool_list}", lines or "（无——检查档位配置）")
