"""仓位参数两键声明与校验测试（add-position-sizing-params 1.1/1.2）。

族 1 序列化超集：不含新键 DNA 的 to_dict 输出与现状逐位一致（固定
样例字面量快照锚）+新键声明 roundtrip 恒等（含边界 1.0）+from_dict
直通；族 2 validator 域：initial_fraction (0,1] 值域/类型、
max_position_loss >0 下界（无上界）、互斥拒绝与不误拒边界、
position_size 存量校验保持、RiskGenesModel 字段对齐。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import RiskGenes
from tests.helpers.data_factory import make_dna


def _rg(**kw) -> RiskGenes:
    """合法基线 RiskGenes（validate_dna 全绿面）+ 覆写。"""
    base = dict(stop_loss=0.05, take_profit=0.10)
    base.update(kw)
    return RiskGenes(**base)


# ── 族 1：序列化超集 ──


class TestSerializationSuperset:
    """族 1：存量逐位不变（快照锚）+新键条件写入+roundtrip+直通。"""

    def test_f1_legacy_output_byte_identical_snapshot(self):
        """不含新键 DNA 的 to_dict 输出与现状逐位一致——固定样例字面量
        快照（含全部既有可选键声明：分批三键/max_hold/time_exit/
        position_mode/risk_fraction；快照值=改造前 main 现状实拍）。"""
        rg = RiskGenes(
            stop_loss=0.05, take_profit=0.1, position_size=0.3, leverage=1,
            direction="long", sl_mode="pct", atr_period=14,
            exit_fraction=0.5, add_fraction=0.1, max_exposure=1.0,
            max_hold=20, time_exit_utc=600, position_mode="fixed",
            risk_fraction=0.1)
        assert rg.to_dict() == {
            "stop_loss": 0.05, "take_profit": 0.1, "position_size": 0.3,
            "leverage": 1, "direction": "long", "sl_mode": "pct",
            "atr_period": 14, "exit_fraction": 0.5, "add_fraction": 0.1,
            "max_exposure": 1.0, "max_hold": 20, "time_exit_utc": 600,
            "position_mode": "fixed", "risk_fraction": 0.1,
        }
        # make_dna 基线（七键基础形——分批/时间族全 None）同快照锚
        assert make_dna().risk_genes.to_dict() == {
            "stop_loss": 0.05, "take_profit": 0.1, "position_size": 0.5,
            "leverage": 1, "direction": "long", "sl_mode": "pct",
            "atr_period": 14,
        }

    @pytest.mark.parametrize("if_val,mpl_val", [
        (0.2, 500.0),     # 常规声明
        (1.0, 1e12),      # initial_fraction 闭上界 1.0 + 防线无上界大值
    ])
    def test_f1_new_keys_roundtrip_identity(self, if_val, mpl_val):
        """新键声明 roundtrip 恒等：to_dict 含键且值恒等 → from_dict →
        字段恒等 → 再序列化相等（含边界 1.0）。"""
        rg = _rg(initial_fraction=if_val, max_position_loss=mpl_val)
        d = rg.to_dict()
        assert d["initial_fraction"] == if_val
        assert d["max_position_loss"] == mpl_val
        rg2 = RiskGenes.from_dict(d)
        assert rg2.initial_fraction == if_val
        assert rg2.max_position_loss == mpl_val
        assert rg2.to_dict() == d

    def test_f1_undeclared_keys_absent(self):
        """未声明（None）不写键——超集形制（存量序列化逐位不变的前提）。"""
        d = _rg().to_dict()
        assert "initial_fraction" not in d
        assert "max_position_loss" not in d

    def test_f1_from_dict_passthrough(self):
        """from_dict 直通承接：含新键 dict 字段直取恒等；不含新键 dict
        → 两字段 None（合法缺省态）。"""
        rg = RiskGenes.from_dict({
            "stop_loss": 0.05, "initial_fraction": 0.25,
            "max_position_loss": 2500.0})
        assert rg.initial_fraction == 0.25
        assert rg.max_position_loss == 2500.0
        rg_none = RiskGenes.from_dict({"stop_loss": 0.05})
        assert rg_none.initial_fraction is None
        assert rg_none.max_position_loss is None


# ── 族 2：validator 域 ──


class TestInitialFractionDomain:
    """族 2a/2b：initial_fraction (0,1] 值域+类型（bool 排除）。"""

    @pytest.mark.parametrize("val,should_pass", [
        (0, False),      # 开下界排除（零首仓无意义）
        (0.5, True),
        (1.0, True),     # 闭上界（全部可用资金）
        (1.01, False),
        (-0.2, False),
    ])
    def test_f2a_value_domain(self, val, should_pass):
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.initial_fraction = val
        res = validate_dna(dna)
        assert res.is_valid == should_pass, (
            f"initial_fraction={val!r} 期望 {'过' if should_pass else '拒'}，"
            f"errors={res.errors}")
        if not should_pass:
            assert any("initial_fraction" in e for e in res.errors)

    @pytest.mark.parametrize("bad", [True, "x"])
    def test_f2b_type_rejected(self, bad):
        """非数字（str）与 bool（True 是 int 子类——值域穿透 0<True<=1
        静默合法化）拒绝。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.initial_fraction = bad
        res = validate_dna(dna)
        assert not res.is_valid, f"{bad!r} 应拒"
        assert any("initial_fraction" in e for e in res.errors)


class TestMaxPositionLossDomain:
    """族 2c：max_position_loss 只有下界（>0），上界完全放开。"""

    @pytest.mark.parametrize("val,should_pass", [
        (0, False),       # 开下界排除（0=禁用哨兵不可由声明面表达）
        (-100.0, False),
        (0.01, True),
        (1e12, True),     # 无上界断言——防实现私设上界
    ])
    def test_f2c_value_domain(self, val, should_pass):
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.max_position_loss = val
        res = validate_dna(dna)
        assert res.is_valid == should_pass, (
            f"max_position_loss={val!r} 期望 {'过' if should_pass else '拒'}，"
            f"errors={res.errors}")
        if not should_pass:
            assert any("max_position_loss" in e for e in res.errors)

    @pytest.mark.parametrize("bad", [True, "x"])
    def test_f2c_type_rejected(self, bad):
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.max_position_loss = bad
        res = validate_dna(dna)
        assert not res.is_valid, f"{bad!r} 应拒"
        assert any("max_position_loss" in e for e in res.errors)


class TestMutualExclusion:
    """族 2e/2f：initial_fraction×risk_budget 互斥+不误拒边界。"""

    def test_f2e_risk_budget_combination_rejected(self):
        """互斥拒：initial_fraction 声明+position_mode="risk_budget"（其余
        组合面补齐 risk_fraction/stop_loss 使唯一误差源=互斥）——同笔首仓
        不可两个指挥。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.initial_fraction = 0.2
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.1   # risk_budget 组合面补齐
        dna.risk_genes.stop_loss = 0.05
        res = validate_dna(dna)
        assert not res.is_valid
        assert any("mutually exclusive" in e for e in res.errors), (
            f"互斥文案缺席: {res.errors}")

    def test_f2f_no_overreach_fixed_coexistence(self):
        """不误拒边界：position_mode=None（缺省 fixed）+risk_fraction>0+
        initial_fraction 合法共存——互斥激活判据唯一为显式
        "risk_budget"（fixed 档生效、risk_fraction 无消费点）。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.initial_fraction = 0.2
        dna.risk_genes.risk_fraction = 0.1
        assert dna.risk_genes.position_mode is None
        res = validate_dna(dna)
        assert res.is_valid, f"fixed 档共存不应误拒: {res.errors}"

    def test_f2f_explicit_fixed_coexistence(self):
        """显式 "fixed" 档共存同不误拒（激活判据唯一性的补面）。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.initial_fraction = 0.5
        dna.risk_genes.position_mode = "fixed"
        res = validate_dna(dna)
        assert res.is_valid, f"fixed 档共存不应误拒: {res.errors}"

    def test_f2d_three_face_parity_anchor(self):
        """族 2d 三面对拍·strategy 面（6.3）：同一互斥构造拒绝+错误
        文案结构化可读（两字段名与互斥语义齐备——登记声明块面与
        零副作用断言见 tests/analysis、MCP 参数面 422 detail 契约与
        场景文案见 tests/mcpserver，三面共拒同一组合）。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.initial_fraction = 0.2
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.1
        dna.risk_genes.stop_loss = 0.05
        res = validate_dna(dna)
        assert not res.is_valid
        mutex = [e for e in res.errors if "mutually exclusive" in e]
        assert mutex, f"互斥文案缺席: {res.errors}"
        # 结构化可读：一条错误内两字段名齐备（定位到冲突双方）
        assert "initial_fraction" in mutex[0]
        assert "risk_budget" in mutex[0]

    @pytest.mark.parametrize("infinite", [
        float("inf"),   # IEEE inf
        float("-inf"),
        10 ** 400,      # 超大 int（float 化溢出——延迟爆炸形）
        1e999,          # 字面量解析即 inf
    ], ids=["inf", "neg-inf", "huge-int", "1e999"])
    def test_mpl_finiteness_rejected(self, infinite):
        """SEC-01 有限性校验（validator 面）：inf/超大整数不是金额
        （「无上界=考场无观点」不受影响——finite 不是上界）；此前
        inf>0 恒真直通、10**400 延迟到物化层 float() 崩。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.max_position_loss = infinite
        res = validate_dna(dna)
        assert not res.is_valid, f"{infinite!r} 须拒"
        assert any("finite" in e and "max_position_loss" in e
                   for e in res.errors), res.errors


class TestPositionSizeStillEnforced:
    """族 2g：position_size 声明后无消费点但仍必填仍参与值域校验
    （存量兼容——序列化不删键、校验段不跳过）。"""

    def test_f2g_position_size_range_still_validated(self):
        """声明两新键+position_size 越界（0.05<[0.10,1.0]）仍拒——
        新键不豁免存量校验。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        dna.risk_genes.initial_fraction = 0.2
        dna.risk_genes.max_position_loss = 500.0
        dna.risk_genes.position_size = 0.05
        res = validate_dna(dna)
        assert not res.is_valid
        assert any("Position size" in e for e in res.errors)

    def test_f2g_position_size_default_still_present(self):
        """声明两新键+position_size 缺省（默认 0.3）合法——「必填」的
        声明面=字段恒在（不因新键转 Optional/不删键）。"""
        from core.strategy.validator import validate_dna
        dna = make_dna()
        assert dna.risk_genes.position_size == 0.5  # make_dna 缺省在场
        dna.risk_genes.initial_fraction = 0.2
        dna.risk_genes.max_position_loss = 500.0
        d = dna.risk_genes.to_dict()
        assert "position_size" in d, "序列化不删键"
        assert validate_dna(dna).is_valid


class TestRiskGenesModelAlignment:
    """族 2g：RiskGenesModel 字段对齐（传输层同步铁律——漏同步的失效
    形态=extra="forbid" 拒收含新键的 DNA 直传/回显，dna 静默消失）。"""

    def test_f2g_model_fields_carry_new_keys(self):
        from typing import get_args, get_origin, Union
        from api.schemas import RiskGenesModel
        for key in ("initial_fraction", "max_position_loss"):
            assert key in RiskGenesModel.model_fields, (
                f"RiskGenesModel 缺 {key}——extra='forbid' 将拒收含键 DNA")
            ann = RiskGenesModel.model_fields[key].annotation
            # Optional[float]（float | None）——传输层不加 bound 同款形制
            assert get_origin(ann) is Union \
                and get_args(ann) == (float, type(None)), \
                f"{key} 注解应为 Optional[float]，实际: {ann}"

    def test_f2g_model_accepts_new_keys_dna_roundtrip(self):
        """含新键 DNA 直传回显：RiskGenesModel(**risk.to_dict()) 构造
        成功且新键值恒等（同步铁律的行为面）。"""
        from api.schemas import RiskGenesModel
        rg = _rg(initial_fraction=0.2, max_position_loss=1e12)
        model = RiskGenesModel(**rg.to_dict())
        assert model.initial_fraction == 0.2
        assert model.max_position_loss == 1e12
        assert model.position_size == 0.3

    def test_f2g_model_position_size_still_required(self):
        """position_size 传输面仍必填（非 Optional——显式 null 拒）：
        422 语义的字段级承载（缺键走默认 0.3，null 是类型违规）。"""
        import pydantic
        from api.schemas import RiskGenesModel
        with pytest.raises(pydantic.ValidationError):
            RiskGenesModel(stop_loss=0.05, position_size=None)
