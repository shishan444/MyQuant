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
- 不可表达（转化会被拒绝并说明原因）：跨币种条件（WHEN BTC、
  THEN ETH）、分布形态类条件、无 subject 的模式条件。
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
终态（supported/refuted/superseded/abandoned）：拒绝转化、新验证与
再迁移；被证伪或放弃的想法请登记新假设（可在 conditions 里引用旧
想法的修正）。
```
- 条件是机器判定的唯一来源：你登记的 when/then 结构化条件会被
  翻译与验证，statement 措辞只作展示。
- subject 必须引用可用信号列（GET list_signal_columns 查询；
  内置指标列形如 ema_50、rsi_14，插件列精确名；条目标注可引用域）。
"""


def build_capability_doc(tool_names: list[str]) -> str:
    """合并静态协议文档与运行时工具清单。"""
    lines = "\n".join(f"- {n}" for n in sorted(tool_names))
    return CAPABILITY_DOC.replace("{tool_list}", lines or "（无——检查档位配置）")
