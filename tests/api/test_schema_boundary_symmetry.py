"""schema-boundary-symmetry 契约锚（improve-schema-bound-symmetry）。

retire-legacy-subsystems 后 Evolution/Batch schema 族随进化与批跑域
退役，SYM1-4（杠杆五处对齐/越界响应面/合法零影响/对称基线在案）
守护面消失删除；保留 SYM5：直建 schema（RiskGenesModel pydantic
default）与产物模型（dna.RiskGenes dataclass 默认）双默认同值。
"""

import pytest

from api.schemas import RiskGenesModel

pytestmark = [pytest.mark.unit]



class TestSym5RiskGenesDefaultParity:
    """A9 双默认同值（research-execution-config-alignment 1.1）：
    直建 schema（RiskGenesModel pydantic default）与产物模型
    （dna.RiskGenes dataclass 默认）两处独立维护的默认值结构一致。
    止损强制后登记面默认消灭、直建 schema default 存活——两处机制
    生命周期不同步，漏改一处=默认值静默分裂（物化值背离产物默认）。
    """

    def test_pydantic_defaults_match_dna_defaults(self):
        """结构对照（不硬编码字面量）：to_dict 恒写键族逐键与 pydantic
        dump 对应键值相等——dataclass 侧改默认而 schema 未跟（或反向）
        即红。"""
        from core.strategy.dna import RiskGenes

        pm = RiskGenesModel().model_dump()
        rg = RiskGenes().to_dict()
        assert rg, "to_dict 恒写集非空（7 常键族）"
        for k, v in rg.items():
            assert k in pm, f"schema 缺键 {k}"
            assert pm[k] == v, f"默认分裂: {k} schema={pm[k]!r} dna={v!r}"
