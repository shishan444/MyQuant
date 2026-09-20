"""能力自述（add-ai-research-mcp 4.5）。

四部分（spec mcp-server）：工具清单、验证协议要点、策略可表达边界、
假设状态机规则。工具清单运行时从注册集生成；协议要点为静态权威
文档（与 specs 同源维护）。自述含慢通道预期管理（需求单只持久化
不自动实现——避免 AI 提单后空等结果）。
"""

CAPABILITY_DOC = """# MyQuant 研究通道能力自述

## 1. 工具清单
{tool_list}

## 2. 验证协议要点（必须遵守，工具与端点强制执行，不依赖你自觉）
- **隔离纪律**：验证类工具拒绝与「判定区间」重叠的请求。判定区间 =
  假设登记时自报的 data_ranges ∪ 该假设全部研究日志记录的区间（并集）。
  漏报没用——日志是系统自动记的。
- **warmup 推导式**：隔离回测自动前扩预热 = max(200, 策略用到指标的
  最大周期×3)，结果会声明实际 warmup；数据起点不足时显式拒绝。
- **门槛机械判定**：通过与否由预定义门槛表（盈利>0、回撤<25%、
  交易数>=5）决定，不是你的判断。
- **组合策略加严**：引用多个信号源的策略验证门槛更严。
- **同种子复现**：随机窗口抽查记录种子，同种子同参数结果一致。
- **幂等**：同条件集+区间集的假设登记返回既有 id；同区间的验证
  重复请求返回既有记录（不会重复计算）。

## 3. 策略可表达边界（假设→策略转化器的限制，显式拒绝优先）
- 可表达：阈值比较（gt/lt）、穿越（cross_above/cross_below）、
  回看类条件；插件信号（公式/模型）绑定为信号源。
- 不可表达（转化会被拒绝并说明原因）：跨币种条件（WHEN BTC、
  THEN ETH）、分布形态类条件、无 subject 的模式条件。
- 不可表达时走慢通道：提交 feature_requests（只保证持久化与可查，
  **不会自动实现**——不要提单后空等结果）。

## 4. 假设状态机
```
登记 = active
  ├─(证据 record_id，verdict=pass)→ supported
  ├─(证据 record_id，verdict=fail)→ refuted
  └─(新假设 hypothesis_id)──────────→ superseded
终态（supported/refuted/superseded）：拒绝新验证与再迁移；
被证伪的想法请登记新假设（可在 conditions 里引用旧想法的修正）。
```
- 条件是机器判定的唯一来源：你登记的 when/then 结构化条件会被
  翻译与验证，statement 措辞只作展示。
- subject 必须引用可用信号列（GET list_signal_columns 查询；
  内置指标列形如 ema_50、rsi_14，插件列精确名）。
"""


def build_capability_doc(tool_names: list[str]) -> str:
    """合并静态协议文档与运行时工具清单。"""
    lines = "\n".join(f"- {n}" for n in sorted(tool_names))
    return CAPABILITY_DOC.replace("{tool_list}", lines or "（无——检查档位配置）")
