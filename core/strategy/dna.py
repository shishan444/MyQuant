"""策略 DNA 数据结构（策略遗传表示/序列化/进化的基因建筑块）。

定义进化优化框架内表示、序列化与演化交易策略的核心基因构件：
信号基因（指标条件×角色）、逻辑/执行/风控基因组、多周期层与
StrategyDNA 主体（含基因型签名与往返序列化）。

服务对象：进化引擎（选择/杂交/变异直接操作本结构）、策略库去重
与进化 exclude-set（gene_signature 消费）、translate 分流（假设→
策略转化按签名分流）、DNAModel 五 API 面（api/schemas 的传输层
对应物经 to_dna() 进入本域）。
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# 枚举（条件类型与信号角色）
# ---------------------------------------------------------------------------

class ConditionType(Enum):
    """信号条件使用的比较/关系类型全集。"""

    LT = "lt"                          # 指标 < 阈值
    GT = "gt"                          # 指标 > 阈值
    LE = "le"                          # 指标 <= 阈值
    GE = "ge"                          # 指标 >= 阈值
    CROSS_ABOVE = "cross_above"        # 指标自下向上穿越阈值
    CROSS_BELOW = "cross_below"        # 指标自上向下穿越阈值
    PRICE_ABOVE = "price_above"        # 收盘价高于指标值
    PRICE_BELOW = "price_below"        # 收盘价低于指标值
    # 动态上下文条件（序列对照类）
    CROSS_ABOVE_SERIES = "cross_above_series"  # 指标 A 自下向上穿越指标 B
    CROSS_BELOW_SERIES = "cross_below_series"  # 指标 A 自上向下穿越指标 B
    LOOKBACK_ANY = "lookback_any"              # 回看窗内任一 bar 满足内层条件
    LOOKBACK_ALL = "lookback_all"              # 回看窗内全部 bar 满足内层条件
    # 支撑/阻力类条件
    TOUCH_BOUNCE = "touch_bounce"      # 价格触碰指标线后反弹
    ROLE_REVERSAL = "role_reversal"    # 指标线支撑/阻力角色互换
    WICK_TOUCH = "wick_touch"          # 影线触线但收盘在线另一侧


class SignalRole(Enum):
    """信号基因在策略中承担的角色（触发/过滤 × 进出加减 × 方向）。"""

    ENTRY_TRIGGER = "entry_trigger"   # 直接触发买入
    ENTRY_GUARD = "entry_guard"       # 过滤入场条件
    EXIT_TRIGGER = "exit_trigger"     # 直接触发卖出
    EXIT_GUARD = "exit_guard"         # 过滤出场条件
    ADD_TRIGGER = "add_trigger"       # 触发加仓
    ADD_GUARD = "add_guard"           # 过滤加仓条件
    REDUCE_TRIGGER = "reduce_trigger" # 触发减仓
    REDUCE_GUARD = "reduce_guard"     # 过滤减仓条件
    # 加仓抑制（0.4.2 R3/DC-1）：抑制=「任一抑制条件成立的 bar 不加仓」，
    # 组合 adds=(trigger AND guard) AND NOT(inhibitor 任一)。仅 ADD 侧设
    # （不预建 REDUCE_INHIBIT）；取反映射对 cross 类必错故独立角色承载。
    # 翻译映射（add_suppress_conditions→ADD_INHIBIT）与 executor 三处
    # 分支随任务 4.2/4.4 落地。
    ADD_INHIBIT = "add_inhibit"
    DIRECTION = "direction"           # mixed 策略的多空方向判定


def derive_role(timeframe: str) -> str:
    """由周期串推导层角色。

    >= 1d → 'structure'（结构层）；>= 1h → 'zone'（区间层）；
    < 1h → 'execution'（执行层）——多周期层的默认角色分配规则。
    """
    tf = timeframe.strip().lower()
    # 解析数值与单位
    if tf.endswith("d"):
        return "structure"
    if tf.endswith("h"):
        hours = int(tf[:-1])
        return "structure" if hours >= 24 else "zone"
    return "execution"


# 止损模式取值域单一事实源（D4 家族/DC-4，0.4.2 策略表达力）：
# 消费方——api/schemas.py sl_mode pattern（派生）、validator 域校验、
# 登记声明块校验（hypothesis_service）；引擎真实分派集另立
# ENGINE_SUPPORTED_SL_MODES（core/backtest/engine.py——声明域与
# 分派集是两个概念，见 stop-loss-modes 规格「引用式约定边界」）
SL_MODES = ("pct", "atr", "trail")


# ---------------------------------------------------------------------------
# 数据类（基因构件与 DNA 主体）
# ---------------------------------------------------------------------------

@dataclass
class SignalGene:
    """单个信号条件（技术指标衍生的最小基因单元）。

    Attributes:
        indicator: 指标名（如 "RSI"、"EMA"、"MACD"）。
        params: 指标参数（如 {"period": 14}）。
        role: 该信号在策略中的角色（SignalRole 枚举）。
        field_name: 多输出指标的输出列选择（如 MACD 的 "histogram"）；
                    单输出指标为 ``None``。
        condition: 结构化条件字典，如 {"type": "lt", "threshold": 30}
                   或 {"type": "price_above"}（无需阈值）。
        timeframe: 基因级周期标记（unblock-research-validation A 段）——
                    该条件在哪个周期的 K 线轴上求值（None=策略基准
                    周期）。when/加仓触发组条件经翻译防线写入；序列化
                    有值才写（存量基因逐字节不变），gene_signature
                    有值才编码 @tf 后缀（同基因异周期必异签名）。
    """

    indicator: str
    params: Dict[str, Union[int, float]]
    role: SignalRole
    field_name: Optional[str] = None
    condition: Dict[str, Any] = field(default_factory=dict)
    timeframe: Optional[str] = None

    def to_dict(self) -> dict:
        out = {
            "indicator": self.indicator,
            "params": self.params,
            "role": self.role.value,
            "field": self.field_name,
            "condition": self.condition,
        }
        # 基因级周期有值才写（存量序列化逐字节不变）
        if self.timeframe is not None:
            out["timeframe"] = self.timeframe
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "SignalGene":
        data = dict(data)  # 浅拷贝（不就地改入参）
        if isinstance(data.get("role"), str):
            data["role"] = SignalRole(data["role"])
        # JSON 键 "field" 映射到 Python 属性 "field_name"（序列化形制对齐）
        if "field" in data:
            data["field_name"] = data.pop("field")
        return cls(**{k: v for k, v in data.items() if k != "field"})


@dataclass
class LogicGenes:
    """逻辑基因组：信号基因组合为进/出场决策的与或算子。"""

    entry_logic: str = "AND"  # 入场组合："AND" 或 "OR"
    exit_logic: str = "AND"   # 出场组合："AND" 或 "OR"
    add_logic: str = "AND"    # 加仓组合："AND" 或 "OR"
    reduce_logic: str = "AND" # 减仓组合："AND" 或 "OR"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LogicGenes":
        return cls(**data)


@dataclass
class ExecutionGenes:
    """执行基因组：交易周期与标的。"""

    timeframe: str = "4h"        # 周期（如 "1h"、"4h"、"1d"）
    symbol: str = "BTCUSDT"     # 标的（如 "BTCUSDT"、"ETHUSDT"）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionGenes":
        return cls(**data)


@dataclass
class RiskGenes:
    """风控基因组（止损止盈/仓位/杠杆/方向 + 分批数值参数）。"""

    stop_loss: float = 0.05               # pct/trail: 0.05（5%/回撤比例）；atr: 2.0（2 倍 ATR）
    take_profit: Optional[float] = None   # pct/trail: 0.10（10%）；atr: 4.0（4 倍 ATR）
    position_size: float = 0.3            # 仓位比例（如 0.3 = 30%）
    leverage: int = 1                     # 杠杆 1-MAX_LEVERAGE 倍（上限单源 validator.MAX_LEVERAGE）
    direction: str = "long"               # 方向："long" | "short"
    sl_mode: str = "pct"                  # 止损模式（取值域单源 SL_MODES："pct" | "atr" | "trail"）
    atr_period: int = 14                  # atr 模式的 ATR 回看周期
    # 分批数值参数（0.4.2 R3）：None 仅手工 DNA 出现——登记路径「有组无
    # 值→归一化字面化→翻译物化」恒具体值；执行侧缺省 0.5/0.1/1.0（A5）
    exit_fraction: Optional[float] = None   # 部分离场比例（reduce 分支）
    add_fraction: Optional[float] = None    # 加仓比例（add 分支，分母=当前权益）
    max_exposure: Optional[float] = None    # 总敞口上限（名义/权益；None 引擎侧按 1.0）
    # 时间出场（add-max-hold-exit）：最长持有 N 根 bar 的到期无条件
    # 平仓；None=无时间离场（存量语义，引擎侧物化 0=禁用）
    max_hold: Optional[int] = None
    # 固定时刻出场（任务 6）：每日 UTC 分钟时刻 0-1439 无条件全平；
    # None=未声明（引擎物化哨兵 -1.0——0 是 0:00 合法值非禁用）
    time_exit_utc: Optional[int] = None
    # 动态定仓（complete-research-tooling 任务 4）：None=缺省 fixed
    # （固定比例 position_size 路径逐字节不变）；"risk_budget"=风险预
    # 算制定仓——入场 size=当时权益×risk_fraction÷实际生效止损距离
    # （按 sl_mode 三档分派距离；须声明 stop_loss>0——登记面组合校验）
    position_mode: Optional[str] = None
    # 风险预算比例 (0,0.5]（risk_budget 档生效；fixed 档不消费）
    risk_fraction: Optional[float] = None
    # 首笔动用资金比例 (0,1]（add-position-sizing-params）：声明时替
    # 换 fixed 路径的首笔定仓来源（margin=当时权益×initial_fraction），
    # 加仓仍走 add_fraction；与 risk_budget 互斥（同笔首仓不可两个指挥）
    initial_fraction: Optional[float] = None
    # 持仓金额防线（>0 生效，上界完全放开——考场无观点）：单一合并
    # 持仓总浮亏（未实现亏损+该持仓累计资金费净支出）按收盘价判定，
    # 达线次根开盘全平；None=未声明（执行物化 0.0=禁用）
    max_position_loss: Optional[float] = None

    def to_dict(self) -> dict:
        # v2 超集形制（沿 signal_sources 零迁移先例）：新键非 None 才写
        # ——存量 DNA 序列化输出逐位不变（asdict 会强制携带 None 键，
        # 故显式键序输出+三新键条件写入）
        result = {
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "position_size": self.position_size,
            "leverage": self.leverage,
            "direction": self.direction,
            "sl_mode": self.sl_mode,
            "atr_period": self.atr_period,
        }
        if self.exit_fraction is not None:
            result["exit_fraction"] = self.exit_fraction
        if self.add_fraction is not None:
            result["add_fraction"] = self.add_fraction
        if self.max_exposure is not None:
            result["max_exposure"] = self.max_exposure
        if self.max_hold is not None:
            result["max_hold"] = self.max_hold
        if self.time_exit_utc is not None:
            result["time_exit_utc"] = self.time_exit_utc
        if self.position_mode is not None:
            result["position_mode"] = self.position_mode
        if self.risk_fraction is not None:
            result["risk_fraction"] = self.risk_fraction
        if self.initial_fraction is not None:
            result["initial_fraction"] = self.initial_fraction
        if self.max_position_loss is not None:
            result["max_position_loss"] = self.max_position_loss
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "RiskGenes":
        return cls(**data)


@dataclass
class TimeframeLayer:
    """多周期策略中的单个周期层。

    每层含信号基因与逻辑基因，在自身周期的数据上独立求值。

    role: "structure"（>=1d）/ "zone"（>=1h）/ "execution"（<1h）。
          反序列化时遗留 "trend" 映射为 "structure"；None 缺省
          "execution"（向后兼容）。
    """

    timeframe: str
    signal_genes: List[SignalGene] = field(default_factory=list)
    logic_genes: LogicGenes = field(default_factory=LogicGenes)
    role: Optional[str] = None  # 层角色："structure" | "zone" | "execution"

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "signal_genes": [sg.to_dict() for sg in self.signal_genes],
            "logic_genes": self.logic_genes.to_dict(),
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimeframeLayer":
        data = dict(data)
        if "signal_genes" in data and isinstance(data["signal_genes"], list):
            data["signal_genes"] = [
                SignalGene.from_dict(sg) if isinstance(sg, dict) else sg
                for sg in data["signal_genes"]
            ]
        if "logic_genes" in data and isinstance(data["logic_genes"], dict):
            data["logic_genes"] = LogicGenes.from_dict(data["logic_genes"])
        return cls(**data)


@dataclass
class StrategyDNA:
    """交易策略的完整基因表示（进化算法操作的基本单元）。

    选择/杂交/变异全部直接作用于本结构。多周期（MTF）经 ``layers``
    表达；向后兼容起见 ``signal_genes`` 属性代理 layers[0]（单周期
    形制下两者同源）。
    """

    signal_genes: List[SignalGene] = field(default_factory=list)
    logic_genes: LogicGenes = field(default_factory=LogicGenes)
    execution_genes: ExecutionGenes = field(default_factory=ExecutionGenes)
    risk_genes: RiskGenes = field(default_factory=RiskGenes)
    strategy_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)
    mutation_ops: List[str] = field(default_factory=list)
    layers: Optional[List[TimeframeLayer]] = None
    cross_layer_logic: str = "AND"
    mtf_mode: Optional[str] = None  # "direction" | "confluence" | "direction+confluence" | None
    confluence_threshold: float = 0.3
    proximity_mult: float = 1.5
    # == v2 超集（add-ai-research-mcp 6.1）：缺省 None = 退化形态（零迁移）==
    # 信号源引用：[{type: rule|formula|model, ref, weight?}]（rule 源即 signal_genes）
    signal_sources: Optional[List[Dict[str, Any]]] = None
    # 组合决策层：{mode: weighted_vote|gate|logic, weights?, threshold?, op?}
    composition: Optional[Dict[str, Any]] = None
    # 入场触发策略（trigger-policy 卡5）：{mode, bars?} 归一形（假设
    # conditions_json 顶层 trigger_policy 键直接映射）；None=无策略=
    # 掩码直译现状（既有策略形制零变化）
    entry_trigger_policy: Optional[Dict[str, Any]] = None
    _layers_explicit: bool = field(default=False, repr=False, init=False)

    def __post_init__(self):
        # 自动标记显式层（构造传入——区别于 from_dict 的 auto-wrap 兼容层）
        if self.layers is not None:
            object.__setattr__(self, "_layers_explicit", True)

    @property
    def is_mtf(self) -> bool:
        """是否为多周期层结构（layers 显式存在——auto-wrap 兼容层不计）。"""
        return self.layers is not None and len(self.layers) >= 1 and self._layers_explicit

    @property
    def timeframes(self) -> List[str]:
        """全部层涉及的周期清单（无 layers 时退化为执行周期）。"""
        if self.layers:
            return [layer.timeframe for layer in self.layers]
        return [self.execution_genes.timeframe]

    @property
    def gene_signature(self) -> str:
        """基因型签名（相似度/去重比较的稳定指纹串）。

        每信号基因编码 indicator + 全部参数（排序）+ 条件类型 + 角色，
        另含杠杆/方向与（MTF 的）层结构——不同参数值产出不同签名。
        这是 DNA 的内在属性（基因型指纹），消费方：多样性度量、
        策略库去重、进化 exclude-set（与 translate 同签名分流共用）。
        自 ``core.evolution.diversity._gene_signature`` 逐字迁移。
        """
        parts = []
        for gene in sorted(self.signal_genes, key=lambda g: g.role.value):
            param_parts = sorted(f"{k}={v}" for k, v in gene.params.items())
            param_summary = ",".join(param_parts)
            cond_type = gene.condition.get("type", "?") if gene.condition else "?"
            # 基因级周期有值才编码 @tf 后缀（D2：防 GAP-11 同签名分流
            # 复发——同基因异周期必异签名；无值基因签名逐位不变）
            gene_part = f"{gene.indicator}({param_summary}):{cond_type}:{gene.role.value}"
            if gene.timeframe is not None:
                gene_part += f"@{gene.timeframe}"
            parts.append(gene_part)
        parts.append(f"lev:{self.risk_genes.leverage}")
        parts.append(f"dir:{self.risk_genes.direction}")
        # 层结构计入签名（M3 修复：同基因不同分层配置不再同签名）
        if self.layers:
            for layer in self.layers:
                layer_parts = [f"L:{layer.timeframe}:{layer.role or 'execution'}"]
                for g in sorted(layer.signal_genes, key=lambda x: x.role.value):
                    g_cond = g.condition.get("type", "?") if g.condition else "?"
                    layer_parts.append(f"{g.indicator}:{g_cond}")
                parts.append(",".join(layer_parts))
        # v2 插件信号源与组合层（fix-plugin-column-translation）：混合产物
        # 的 signal_genes 与纯 rule 策略可相同，签名若不含 signal_sources
        # 会把不同基因型误判同签名（translate 同签名分流、库去重、进化
        # exclude-set 全部失真）。退化形态（无插件源）签名逐位不变（零迁移）。
        plugin_sources = [
            s for s in (self.signal_sources or [])
            if isinstance(s, dict) and s.get("type") in ("formula", "model")
        ]
        if plugin_sources:
            parts.append("src:" + ";".join(sorted(
                f"{s.get('type')}:{s.get('ref')}:"
                f"{s.get('threshold', 0.0)}:{s.get('operator', '>')}"
                for s in plugin_sources)))
            if self.composition:
                comp = self.composition if isinstance(self.composition, dict) else {}
                parts.append("comp:" + json.dumps(
                    comp, sort_keys=True, ensure_ascii=False, default=str))
        # 入场触发策略（trigger-policy 卡5）：同条件集异 policy = 不同
        # 基因型（假设翻译同签名分流、库去重、进化 exclude-set 依据）；
        # 无 policy 签名逐位不变（沿 signal_sources 零迁移先例）
        if self.entry_trigger_policy is not None:
            parts.append("etp:" + json.dumps(
                self.entry_trigger_policy, sort_keys=True,
                ensure_ascii=False, default=str))
        return "|".join(parts)

    def _resolve_signal_genes(self) -> List[SignalGene]:
        """解析信号基因（layers 存在且 signal_genes 为空时取 layers[0]）。"""
        if self.layers and not self.signal_genes:
            return self.layers[0].signal_genes
        return self.signal_genes

    def to_dict(self) -> dict:
        result = {
            "strategy_id": self.strategy_id,
            "generation": self.generation,
            "parent_ids": self.parent_ids,
            "mutation_ops": self.mutation_ops,
            "signal_genes": [sg.to_dict() for sg in self.signal_genes],
            "logic_genes": self.logic_genes.to_dict(),
            "execution_genes": self.execution_genes.to_dict(),
            "risk_genes": self.risk_genes.to_dict(),
            "cross_layer_logic": self.cross_layer_logic,
            "mtf_mode": self.mtf_mode,
            "confluence_threshold": self.confluence_threshold,
            "proximity_mult": self.proximity_mult,
        }
        # v2 超集（非 None 才写——退化形态序列化逐位不变）
        if self.signal_sources is not None:
            result["signal_sources"] = self.signal_sources
        if self.composition is not None:
            result["composition"] = self.composition
        if self.entry_trigger_policy is not None:
            result["entry_trigger_policy"] = self.entry_trigger_policy
        if self.layers:
            result["layers"] = [layer.to_dict() for layer in self.layers]
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyDNA":
        data = dict(data)  # 浅拷贝（不就地改入参）

        # 解析 layers（MTF 形制，存在时）
        layers = None
        if "layers" in data and isinstance(data["layers"], list):
            layers = [
                TimeframeLayer.from_dict(l) if isinstance(l, dict) else l
                for l in data["layers"]
            ]
        data.pop("layers", None)

        # 解析跨层逻辑（缺省 AND）
        cross_layer_logic = data.pop("cross_layer_logic", "AND")

        # 解析 MTF 控制字段（带缺省）
        mtf_mode = data.pop("mtf_mode", None)
        confluence_threshold = data.pop("confluence_threshold", 0.3)
        proximity_mult = data.pop("proximity_mult", 1.5)

        # 遗留 "trend" 角色映射为 "structure"（序列化兼容）
        if layers:
            for layer in layers:
                if layer.role == "trend":
                    layer.role = "structure"

        if "signal_genes" in data and isinstance(data["signal_genes"], list):
            data["signal_genes"] = [
                SignalGene.from_dict(sg) if isinstance(sg, dict) else sg
                for sg in data["signal_genes"]
            ]

        if "logic_genes" in data and isinstance(data["logic_genes"], dict):
            data["logic_genes"] = LogicGenes.from_dict(data["logic_genes"])

        if "execution_genes" in data and isinstance(data["execution_genes"], dict):
            data["execution_genes"] = ExecutionGenes.from_dict(data["execution_genes"])

        if "risk_genes" in data and isinstance(data["risk_genes"], dict):
            data["risk_genes"] = RiskGenes.from_dict(data["risk_genes"])

        if not data.get("strategy_id"):
            data["strategy_id"] = str(uuid.uuid4())

        # v2 字段（add-ai-research-mcp 6.1）
        signal_sources = data.pop("signal_sources", None)
        composition = data.pop("composition", None)
        entry_trigger_policy = data.pop("entry_trigger_policy", None)

        # 白名单过滤未知键：旧读者遇新键不再 TypeError（回退安全）；
        # 存量 DNA 无未知键，行为逐位不变
        _known = {
            "signal_genes", "logic_genes", "execution_genes", "risk_genes",
            "strategy_id", "generation", "parent_ids", "mutation_ops",
        }
        data = {k: v for k, v in data.items() if k in _known}

        instance = cls(**data)
        instance.signal_sources = signal_sources
        instance.composition = composition
        instance.entry_trigger_policy = entry_trigger_policy
        instance.layers = layers
        instance.cross_layer_logic = cross_layer_logic
        instance.mtf_mode = mtf_mode
        instance.confluence_threshold = confluence_threshold
        instance.proximity_mult = proximity_mult
        # 仅数据携带的 layers 标记为显式（auto-wrap 兼容层不标——
        # is_mtf 语义据此区分数据结构与真多周期）
        instance._layers_explicit = layers is not None

        # auto-wrap：无 layers 但有 signal_genes 时包单层（执行周期）——
        # 单周期 DNA 在层形接口上的兼容形态
        if instance.layers is None and instance.signal_genes:
            instance.layers = [
                TimeframeLayer(
                    timeframe=instance.execution_genes.timeframe,
                    signal_genes=list(instance.signal_genes),
                    logic_genes=LogicGenes(
                        entry_logic=instance.logic_genes.entry_logic,
                        exit_logic=instance.logic_genes.exit_logic,
                    ),
                )
            ]

        return instance

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "StrategyDNA":
        return cls.from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# 策略命名工具
# ---------------------------------------------------------------------------

INDICATOR_TYPE_MAP: Dict[str, str] = {
    "EMA": "趋势", "SMA": "趋势", "WMA": "趋势", "DEMA": "趋势", "TEMA": "趋势",
    "RSI": "动量", "MACD": "动量", "CCI": "动量", "Stochastic": "动量",
    "BB": "波动", "ATR": "波动", "KC": "波动",
    "ADX": "趋势", "Ichimoku": "趋势",
    "MFI": "量价", "OBV": "量价",
    "SAR": "趋势", "TRIX": "动量", "ROC": "动量",
}

_DIRECTION_LABEL = {"long": "做多", "short": "做空", "mixed": "混合"}


def generate_strategy_name(dna: "StrategyDNA") -> str:
    """由 DNA 生成人类可读的策略名。

    形制："{指标}{类型} {方向} {周期}-{hash4}"
    示例："EMA趋势 做多 4H-A3F2"
    哈希后缀区分同入场触发指标的不同策略（名称唯一性锚）。
    """
    genes = dna.layers[0].signal_genes if dna.layers else dna.signal_genes
    trigger = next((g for g in genes if g.role == SignalRole.ENTRY_TRIGGER), None)
    indicator = trigger.indicator if trigger else (genes[0].indicator if genes else "MIX")
    type_label = INDICATOR_TYPE_MAP.get(indicator, "混合")
    dir_label = _DIRECTION_LABEL.get(dna.risk_genes.direction, "做多")
    tf = dna.execution_genes.timeframe.upper()

    # 全部区分性字段拼短哈希（同触发指标策略的唯一性来源）
    sig_parts = []
    for g in dna.signal_genes:
        # 基因级周期计入哈希（T1e：异周期策略名不同）
        tf_suffix = f"@{g.timeframe}" if g.timeframe is not None else ""
        sig_parts.append(f"{g.indicator}:{g.role.value}:{sorted(g.params.items())}{tf_suffix}")
    sig_parts.append(f"logic:{dna.logic_genes.entry_logic}/{dna.logic_genes.exit_logic}")
    sig_parts.append(f"risk:{dna.risk_genes.stop_loss}/{dna.risk_genes.take_profit}/{dna.risk_genes.position_size}")
    # 层配置计入哈希（同基础基因不同分层的策略区分）
    if dna.layers:
        for i, layer in enumerate(dna.layers):
            for g in layer.signal_genes:
                sig_parts.append(f"L{i}:{g.indicator}:{g.role.value}:{sorted(g.params.items())}")
    sig_str = "|".join(sig_parts)
    hash4 = hashlib.md5(sig_str.encode()).hexdigest()[:4].upper()

    return f"{indicator}{type_label} {dir_label} {tf}-{hash4}"
