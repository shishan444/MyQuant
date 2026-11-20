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
  存量假设的 data_ranges（登记时自报，现冻结只读）∪ 该假设全部研究
  日志记录的区间（并集）。漏报没用——日志是系统自动记的。
- **时间参数规约**：验证与分析的时间参数统一按 UTC 解释；未带时区的
  naive 时间（如 2024-01-01）按 UTC 处理。
- **warmup 推导式**：沙盒回测（run_backtest）自动前扩预热 =
  max(200, 跨指标取最大的(周期类参数注册表上限之和 × 3))——单指标
  按周期类参数上限求和(保守取向)；含插件列时取
  max(内置推导, plugin_columns_lookback)（series 档止损线来源同轨
  并入：指标形式走周期×3 规则、fx 形式走依赖闭包）；高周期条件掩码链为
  max(条件实际参数 × 3, 20)，bar 数按该轴周期折算为前扩时长（1d 轴
  42 bar = 42 天，非 42 根基准 bar；下限 20 与主公式下限 200 分属
  两式）。结果会声明实际 warmup；数据起点不足时显式拒绝（错误
  detail 携带 warmup_bars 与 rule 全文）。
- **事件策略层（trigger_policy）**：分析模板（event_study/
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
- **回测入口（P2 起）**：run_replay_backtest 是现役回测主入口——
  逐根回放引擎（纸盘执行核心同一套执行逻辑，修复条款内嵌两域同
  效）；symbol/timeframe 从策略 DNA 抄录不可覆写；装载失败结构化
  拒绝（series 档不支持——回放沿纸盘链能力集 pct/atr/trail，62 条
  hypothesis 历史存量 series 策略失去现役回测入口，只读保留）。
- **沙盒回测纪律（legacy）**：run_backtest 已下线（legacy VBT 链路，
  保留代码为回退后门，不再是推荐链路）——原快速试错语义不变：
  可调手续费/滑点直返指标、不构成验证证据；每次执行同步写入验证
  记录统一流（validation_records level=sandbox_backtest 行，verdict
  恒空串；响应回显 record_id；沙盒记录不可作 ruler_compare/
  ab_compare 对照源）；挂 hypothesis_id 的沙盒区间**计入判定并集**
  ——此后同区间的隔离验证会被拒（先偷看不获利）。
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
- **指标参数修复（2026-10-13）**：BB 的 std 与 Keltner 的
  atr_period/multiplier 传参修复已生效，全链路新生成的计算数值
  正确；BB 历史存量已按 2026-10-13 终局裁决整批清除。Keltner
  历史存量为吞参语义（atr_period 从未生效）；按 2026-10-14 裁决
  历史策略已全部清除——无存量即无重算，重算边界问题终局关闭。
- **比较动作全集（fix-research-c2 起）**：价格比较动作
  price_above/price_below（价格在指标线上方/下方——比较主体固定
  close，target 为占位值被忽略）在统计面与翻译面两侧均已可用；
  未识别比较动作结构化拒绝（422 附 legal_values=动作分派表键集，
  不再以全 False 掩码静默降级）。
- **分析模板插件列条件（fix-research-c3 起；regime_split
  2026-10-14 起）**：condition_stats/event_study/regime_split/
  inspect_cases/param_sweep/counter_profile 的条件引用插件列
  （fx_*/model_* 注册表列）可用——装载链（收集→可达性
  校验→两维 warmup→求值注入→存在性校验）；未注册列名与求值失败
  分别结构化拒绝（RESEARCH_PLUGIN_COLUMN_NOT_FOUND「名字写错」/
  RESEARCH_COLUMN_NOT_AVAILABLE「插件坏了」），不再静默 0 样本。
- **纯公式进场形制（fix-research-c4 起）**：「公式插件进场+规则出场」
  形制可用——组合层规则侧基线按进场角色基因实质判定（任一
  ENTRY_TRIGGER/ENTRY_GUARD 基因在场即有规则进场侧，与引擎进场
  构造域对齐），纯插件进场不再被静默作废（此前 0 笔交易无提示）；
  组合态可疑形态（规则基线全 False+插件源在场）落 [signal-assembly]
  前缀告警留痕（真实零触发同告警——可观测优先的既定契约）。
- **组合条件（0.4.1 起；regime_split 2026-10-14 起）**：condition_stats/event_study/regime_split 支持 conditions
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
- **反例画像（0.4.1 counter_profile）**：条件触发自动分符合组
  （窗口内任一时点实现方向预期——区间语义）/反例组，全指标特征
  分布对比（八键；特征域=注册表默认参数全输出列减 guard_only/
  lazy）按 Cohen's d 排序输出差异清单——工具只呈现差异不生成条件
  （特征选择由 AI 做）；反例组 <30 标 insufficient 不拒、反例组 0
  合法；总触发 <30 走既有小样本拒绝。条件形制与条件统计一致
  （condition|conditions 二选一）；条件条目可含高周期 timeframe
  键（掩码投影求值）。
- **退出参数扫描（0.4.1 exit_sweep）**：条件固定下（形制与条件
  统计一致——condition|conditions 二选一）按止损模式分组
  扫退出参数网格（Σ≤50 组；模式合法集=回测引擎真实分派集引用式
  ——现值 {pct, atr, trail}，引擎扩分派后自动跟随；stops 值域
  pct 档 [0.005,0.20]、atr 档 [0.5, 5.0]（ATR 倍数），均与
  validator 现役域同源），逐组独立回测（含费滑、与逐根回放回测
  （run_replay_backtest）同一执行链（逐根回放引擎）——条件经翻译构造
  内存 DNA 不落库）输出
  五指标+三道门平台判定
  （最差聚合全序 unsupported>spike>indeterminate>plateau，标尺内建
  不可传；单轴长度 1 恒不成平台属判定语义）；校验期整批 422/执行
  期单组容错（两期分野）；条件条目可携带 timeframe 键，语义
  三分支——等于基准周期与缺省语义相同（键归一，显式写基准
  周期不构成高周期过滤）、严格高于基准周期经掩码投影求值
  （与策略翻译链同款放行）、低于基准周期拒绝
  （TIMEFRAME_NOT_HIGHER）；不可表达形制仍显式拒绝不静默降级
  （RESEARCH_NOT_EXPRESSIBLE——不可表达形制统一经公共入口
  防线拒绝）；**本模板不挂假设**：留痕检查型不进
  判定并集（检查不消耗验证区间）；组数 >10 走 202+轮询。
- **纸盘挂载（rebuild-paper-trading 5.3）**：纸盘交易（实时行情虚拟
  资金前向验证）工具族六件——只读四件（list_paper_tasks 任务状态/
  get_paper_task 详情/get_paper_report 滚动与结题报告/get_paper_
  history 成交·权益·资金费历史）+挂载两件（mount_paper_task 上盘/
  unmount_paper_task 撤盘，研究执行档——资源创建语义，与触发回测
  同档非触金）。挂载参数面与 REST 任务创建完全一致：仅
  验证条件参数（初始资金/杠杆/费滑/品种/验证窗口/停止条件），
  **不含策略语义参数**（方向与执行周期以策略 DNA 为准不可覆写）；
  创建恒受理（并发满载时新任务排队不拒绝）；撤盘=停止语义
  （保留数据出结题报告，不删除证据）。纸盘内部下单是策略代码行为
  非 AI 决策——纸盘挂载不改变触金封禁边界（下单/调仓/修改风控类
  工具与端点任何档位不可达的边界永久不变）。

## 3. 策略库与验证记录（retire-legacy 后形态）
- 策略入库与状态变更经人工 Web 审核操作（REST /api/strategies CRUD
  与生命周期 8 边迁移——晋级边已退役，candidate/live 为休眠态）；
  研究验证不再产出三态判定（旧编排已退役）。
- run_backtest（沙盒统一流）为验证记录唯一生产写入通道：挂
  hypothesis_id（指向存量假设，只读）时区间经研究日志计入判定并集
  （防偷看账本——workbench 观察台内部消费）；不挂以 adhoc 留痕。
  新记录三明细（逐笔成交/权益曲线/信号留痕）同事务原子落库，经
  验证记录页 detail 端点复核（审视回溯分析）。
- 历史假设记录冻结只读（登记/迁移/翻译通道已退役；workbench 对
  存量数据的现算视图照常）。

## 4. 条件可表达边界（exit_sweep 内存 DNA 构造的限制，显式拒绝优先）
- 可表达：阈值比较（gt/lt/ge/le）、穿越（cross_above/cross_below）、
  回看类条件；插件信号（公式/模型）绑定为信号源；价格对指标列比较
  （price_above/price_below，fix-research-c2）；**列名 target 的序列
  比较（fix-research-c6 起——可统计可登记的「指标列对指标列」条件
  已可翻译送考：gt/lt/ge/le 与穿越族（含 breakout/breakdown 别名）
  的编码列名 target 产出序列形基因，数值 target 语义不变）**；
  **close 基础列 subject 的价格主体比较（变更一 B1 起——
  {subject:"close"} × lt/gt × 编码列名 target 翻译为挂目标指标的
  price_below/price_above 基因（收盘价对该指标线的严格比较），
  WHEN/THEN/分批三组同规则；ge/le 含等号形态与 open 等其余基础列
  主体仍显式拒绝——等号语义无对应严格形态，不作静默近似）**。
- 公式插件 DSL 白名单（四族分组，权威源 FORMULA_FUNCTIONS 现算，
  见 /api/research/vocabulary；0.2.12 时序结构与逐元素逻辑，
  add-math-formula-vocabulary 数学词汇扩容）：时序变换族（含
  rolling_quantile 滚动分位，分位点 [0,1] 常量）+ 时序结构
  crossover/crossunder + 逐元素族（max/min/gt/lt/eq/logical_*
  与数学函数 sqrt/log，非定义域输入输出 NaN）+ 卡尔曼封装族
  （kalman 平滑水平/kalman_slope 斜率状态，q/r 正常数、warmup
  固定贡献 200 根）——金叉死叉等穿越结构可在公式层一次表达，
  产出布尔列直通假设条件与策略翻译（真=触发，target 推荐
  gt 0.5/ge 1/eq 1）。
- 不可表达（转化会被拒绝并说明原因）：跨币种条件（WHEN BTC、
  THEN ETH）、分布形态类条件、无 subject 的模式条件、**条件携带
  timeframe 键的多周期假设**（0.2.14——TimeframeLayer 层映射的
  role 归属与跨层组装语义待独立版本产品决策；登记、条件求值类
  分析与案例检查不受影响；exit_sweep 对混合周期条件按同款掩码
  投影求值（when 侧高周期放行域）。
- **pattern_mining 为实验性**：特征自动挑选（按前缀 ~16 维，非全列）、
  特征 NaN 填零、区间头部时点特征有偏（无 warmup）——结论只作探索
  线索，不作定案证据；引用列不在模板可用域时结构化拒绝附清单
  （指标条件请走 condition_stats）。

"""


def build_capability_doc(tool_names: list[str]) -> str:
    """合并静态协议文档与运行时工具清单。"""
    lines = "\n".join(f"- {n}" for n in sorted(tool_names))
    return CAPABILITY_DOC.replace("{tool_list}", lines or "（无——检查档位配置）")
