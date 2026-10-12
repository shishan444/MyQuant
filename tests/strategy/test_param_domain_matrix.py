"""B1-33 字段×责任面矩阵双向守护（0.4.2 策略表达力，§1 阶段子集）。

责任面切分（design DC-15）：pydantic 层只管传输形制（类型/Optional/
封闭枚举 pattern/ge-le 存量）；值域与联动约束的权威点=service 登记链
3.6 步（声明块，随任务 2.2 建）+ validator（DNA 域）。守护为双向——
✔ 格拒越界不放宽；裸 float 格（pydantic）不得拒值域越界（擅自加
bound 即红——防入口分叉的另一方向）。

分阶段口径（tasks 1.3/4.1/4.3，评估问题 6 编排）：
- §1 阶段（本文件当前形态）：已存在五字段格（stop_loss/take_profit/
  sl_mode/position_size/leverage）+ pydantic 传输形制面。
- 4.1 第一站：service 格三键（exit_fraction/add_fraction/max_exposure）
  由「未知键拒」翻「域校验」（同任务原子交付）。
- 4.3 第二站：validator 三字段格补齐 + 八字段全矩阵收口重跑
  （三面全格——pydantic/service/validator）。
"""

import pytest

pytestmark = [pytest.mark.unit]

from api.schemas import RiskGenesModel, EvolutionTaskCreate, PaperTradingTaskCreate
from core.strategy.dna import RiskGenes, SL_MODES, SignalGene, SignalRole, StrategyDNA
from core.strategy.validator import (
    MAX_LEVERAGE,
    SL_ATR_MAX,
    SL_ATR_MIN,
    SL_PCT_MAX,
    SL_PCT_MIN,
    validate_dna,
)


def _mk_dna(**risk_overrides) -> StrategyDNA:
    """构造最小合法 DNA（RSI 进出双信号），仅覆写 risk_genes 字段。"""
    genes = [
        SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                   condition={"type": "lt", "threshold": 30}),
        SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                   condition={"type": "gt", "threshold": 70}),
    ]
    risk = RiskGenes(**risk_overrides)
    return StrategyDNA(signal_genes=genes, risk_genes=risk)


def _errors(**risk_overrides) -> list:
    return validate_dna(_mk_dna(**risk_overrides)).errors


# ── validator 格（✔ 权威点拒越界——不放宽）─────────────────────────


class TestValidatorDomainGrid:
    """五字段格：validator 拒越界值、放行边界值与合法模式。"""

    def test_stop_loss_pct_domain_bounds(self):
        # 边界值放行（闭区间）
        assert _errors(stop_loss=SL_PCT_MIN) == []
        assert _errors(stop_loss=SL_PCT_MAX) == []
        # 越界拒绝
        assert _errors(stop_loss=0.001) != []
        assert _errors(stop_loss=2.0) != []

    def test_stop_loss_atr_fork_domain(self):
        # atr 档分叉域 [SL_ATR_MIN, SL_ATR_MAX]（ATR 倍数）
        assert _errors(sl_mode="atr", stop_loss=SL_ATR_MIN, take_profit=4.0) == []
        assert _errors(sl_mode="atr", stop_loss=SL_ATR_MAX) == []  # 上界不带 TP（TP>SL 独立检查）
        assert _errors(sl_mode="atr", stop_loss=0.3, take_profit=4.0) != []
        assert _errors(sl_mode="atr", stop_loss=6.0, take_profit=8.0) != []

    def test_take_profit_domain_fork(self):
        assert _errors(take_profit=2.0) != []  # pct 档越界
        assert _errors(sl_mode="atr", take_profit=6.0) != []  # atr 档越界

    def test_take_profit_gt_stop_loss_atr_kept(self):
        # atr 档保留 TP>SL（倍数盈亏比有意义）
        assert _errors(sl_mode="atr", stop_loss=2.0, take_profit=1.0) != []

    def test_take_profit_gt_stop_loss_trail_exempt(self):
        # trail 档豁免 TP>SL（回撤比例与固定止盈不构成盈亏比，A1）
        assert _errors(sl_mode="trail", stop_loss=0.05, take_profit=0.03) == []

    def test_sl_mode_enum(self):
        # 1.1 补齐后状态：validator 格覆盖 sl_mode（现状缺口已修）
        assert _errors(sl_mode="trailing") != []
        assert _errors(sl_mode="xyz") != []
        # 合法模式须配该档合法 stop_loss（atr 档默认 0.05 不在其域）
        for mode, sl in (("pct", 0.05), ("atr", 2.0), ("trail", 0.05)):
            assert _errors(sl_mode=mode, stop_loss=sl) == [], \
                f"合法模式 {mode} 不应被拒"

    def test_position_size_domain(self):
        assert _errors(position_size=0.10) == []
        assert _errors(position_size=1.0) == []
        assert _errors(position_size=0.05) != []
        assert _errors(position_size=1.5) != []

    def test_leverage_domain(self):
        assert _errors(leverage=1) == []
        assert _errors(leverage=MAX_LEVERAGE) == []
        assert _errors(leverage=0) != []
        assert _errors(leverage=11) != []

    @pytest.mark.parametrize("stop_loss,sl_mode,rejected", [
        (1.5, "pct", True),   # 1.5 超 pct 比例域
        (1.5, "atr", False),  # 1.5 落 atr 倍数域——同值异判锚
        (8.0, "pct", True),   # 8.0 双拒
        (8.0, "atr", True),
        (0.05, "pct", False), # 0.05 在 pct 域合法
        (0.05, "atr", True),  # 0.05 低于 atr 下界 0.5——误放防护
        (2.0, "pct", True),
        (2.0, "atr", False),
    ])
    def test_same_value_different_verdict(self, stop_loss, sl_mode, rejected):
        """同值异判锚：同一 stop_loss 值在 pct/atr 两档判定不同（量纲双关）。"""
        has_error = _errors(sl_mode=sl_mode, stop_loss=stop_loss,
                            take_profit=None) != []
        assert has_error == rejected

    def test_trail_uses_pct_domain(self):
        """trail 档沿 pct 同域（回撤比例量纲，A1）。"""
        assert _errors(sl_mode="trail", stop_loss=0.05) == []
        assert _errors(sl_mode="trail", stop_loss=2.0) != []


# ── pydantic 裸 float 格（传输形制——不得拒值域越界，不越权）────────


class TestPydanticTransportGrid:
    """传输形制面：裸 float 字段放行值域越界（权威校验在 service/validator，
    pydantic 加 bound 即入口分叉即红）；封闭枚举 pattern 与存量 ge/le 保留。"""

    def test_stop_loss_bare_float_no_bound(self):
        RiskGenesModel(stop_loss=0.001)  # 放行为正确分层
        RiskGenesModel(stop_loss=2.0)

    def test_take_profit_bare_float_no_bound(self):
        RiskGenesModel(take_profit=2.0)

    def test_position_size_bare_float_no_bound(self):
        RiskGenesModel(position_size=0.05)

    def test_sl_mode_pattern_from_sl_modes(self):
        # pattern 由 SL_MODES 派生（封闭枚举是传输形制，单源取值域）
        for mode in SL_MODES:
            RiskGenesModel(sl_mode=mode)
        with pytest.raises(Exception):
            RiskGenesModel(sl_mode="trailing")

    def test_leverage_pydantic_three_schemas(self):
        # leverage 唯一三面一致字段：pydantic ge/le 存量保留（单源 MAX_LEVERAGE）
        with pytest.raises(Exception):
            RiskGenesModel(leverage=11)
        # 另两处 Field 同源（evolution 任务创建与 paper 任务创建）
        for model_cls, build in (
            (EvolutionTaskCreate, lambda lev: {"leverage": lev}),
            (PaperTradingTaskCreate, lambda lev: {
                "dna_json": "{}", "leverage": lev}),
        ):
            with pytest.raises(Exception):
                model_cls(**build(11))
        RiskGenesModel(leverage=MAX_LEVERAGE)  # 边界 10 过


# ── 分批三字段格（4.3 第二站：validator 格补齐后八字段全矩阵收口）──


class TestScalingFieldsGrid:
    """B1-33 第二站：validator 三字段格+pydantic 三键格+DNA 往返
    （service 格三键已于 4.1 翻格——见 test_strategy_param_declaration）。"""

    def test_validator_fraction_bounds(self):
        assert _errors(exit_fraction=1.5) != []
        assert _errors(exit_fraction=0) != []
        assert _errors(add_fraction=0) != []
        assert _errors(add_fraction=1.5) != []
        assert _errors(exit_fraction=0.5) == []
        assert _errors(add_fraction=1.0) == []

    def test_validator_max_exposure_leverage_linkage(self):
        """联动上界（防拒因漂移锚）：max_exposure=8 须显式 leverage=3
        ——缺省 leverage=1 时 8 同拒但拒因为 8>1，锚丧失联动判定力。"""
        assert _errors(max_exposure=8, leverage=3) != []   # 8>3 拒
        assert _errors(max_exposure=8, leverage=10) == []  # 8≤10 过
        assert _errors(max_exposure=1.5) != []             # 缺省杠杆 1 拒
        assert _errors(max_exposure=1.5, leverage=2) == []  # 声明杠杆过

    def test_validator_none_allowed(self):
        """None=合法缺省态不报错（与执行侧缺省完备 0.5/0.1/1.0 衔接）。"""
        assert _errors(exit_fraction=None, add_fraction=None,
                       max_exposure=None) == []

    def test_pydantic_scaling_keys_bare_float(self):
        """pydantic 三键裸 float Optional：不加 bound（值域权威在
        service/validator——擅自加 bound 即红）。"""
        RiskGenesModel(exit_fraction=1.5)   # 放行为正确分层
        RiskGenesModel(add_fraction=0)
        RiskGenesModel(max_exposure=99)
        m = RiskGenesModel(exit_fraction=0.5, add_fraction=0.1,
                           max_exposure=1.0)
        assert m.exit_fraction == 0.5

    def test_risk_genes_roundtrip_zero_migration(self):
        """DNA 往返零迁移：None 不写入序列化（存量逐位不变）、有值保真。"""
        rg = RiskGenes()
        assert "exit_fraction" not in rg.to_dict()
        assert "add_fraction" not in rg.to_dict()
        assert "max_exposure" not in rg.to_dict()
        # 存量形（无新键）from_dict 读 None
        legacy = RiskGenes.from_dict({
            "stop_loss": 0.05, "take_profit": None, "position_size": 0.3,
            "leverage": 1, "direction": "long", "sl_mode": "pct",
            "atr_period": 14})
        assert legacy.exit_fraction is None
        # 有值保真往返
        rg2 = RiskGenes(exit_fraction=0.5, add_fraction=0.1,
                        max_exposure=1.0)
        d = rg2.to_dict()
        assert d["exit_fraction"] == 0.5 and d["max_exposure"] == 1.0
        assert RiskGenes.from_dict(d) == rg2


# ── 常量单源锚 ─────────────────────────────────────────────────────


class TestSingleSourceConstants:
    """D4 家族单源：常量值锚定（改值时本组同步——防漂移）。"""

    def test_sl_modes_value(self):
        assert SL_MODES == ("pct", "atr", "trail")

    def test_atr_fork_constants(self):
        assert (SL_ATR_MIN, SL_ATR_MAX) == (0.5, 5.0)

    def test_pct_constants_unchanged(self):
        assert (SL_PCT_MIN, SL_PCT_MAX) == (0.005, 0.20)

    def test_max_leverage_value(self):
        assert MAX_LEVERAGE == 10
