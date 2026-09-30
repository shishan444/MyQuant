"""能力自述（add-ai-research-mcp 4.5 / improve-mcp-agent-loop M4/M5）。

五部分（spec mcp-server）：工具清单、验证协议要点、草稿-转正生命
周期、策略可表达边界、假设状态机规则。工具清单运行时从注册集生成；
协议要点为静态权威文档（与 specs 同源维护）。自述含慢通道预期管理
（需求单只持久化不自动实现——避免 AI 提单后空等结果）。
"""

CAPABILITY_DOC = """# MyQuant 研究通道能力自述

> 接入形态：本服务以 streamable HTTP 提供（随应用总入口 `python run.py`
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
- **warmup 推导式**：隔离回测自动前扩预热 = max(200, 策略用到指标的
  最大周期×3)，结果会声明实际 warmup；数据起点不足时显式拒绝。
- **门槛机械判定**：通过与否由预定义门槛表（盈利>0、回撤<25%、
  交易数>=5）决定，不是你的判断。
- **组合策略加严**：引用多个信号源的策略验证门槛更严。
- **同种子复现**：随机窗口抽查记录种子，同种子同参数结果一致。
- **幂等**：同条件集+区间集的假设登记返回既有 id；同区间的验证
  重复请求返回既有记录（不会重复计算）。
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
- draft 即验证对象：对 draft 跑 run_isolated_validation——**验证的就是
  将来出货的确切 DNA**。draft 的 DNA 不可经 PUT 修改（改条件请重新
  translate）；draft 不出现在默认策略列表（显式 status=draft 可查）。
- transition(supported) 时，若 draft 自身有 pass 记录，则同事务自动
  转正（draft→active）；若 pass 挂在其他策略上而 draft 未验证，假设
  照常定证但 draft 不转正（不会调包出货未验证的 DNA）。
- 与库中既有策略同基因签名时显式分流标注（same_dna_existing），
  不会静默丢弃。

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
