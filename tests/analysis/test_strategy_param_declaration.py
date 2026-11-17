"""策略层参数声明块测试（add-position-sizing-params / 0.4.2 R1 域）。

retire-legacy-subsystems 终波分拣：登记面值域/幂等四分支/翻译披露/
变体分流/API 传输五族守护对象（register_hypothesis 与
translate_hypothesis 行为）已随研究写入通道退役删除；保留面=声明块
在 DNA 序列化层的空值语义（null 键 from_dict 直通=合法缺省态），
执行物化见 core.paper_trading.kernel.types.materialize。
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import StrategyDNA


class TestNullSemanticsDeclarationVsDnaFace:
    def test_null_semantics_split_declaration_vs_dna_face(self):
        """族 2h 两路径语义差锁定：登记面 null=坏值拒绝（登记域已
        退役）、DNA 面 null=合法缺省——手工 dna_json null 键经
        from_dict 后字段 None（合法缺省态），执行按缺省物化。防后续
        维护者拉平两侧（登记面校验层拒绝 vs 序列化层超集透传）。"""
        from core.paper_trading.kernel.types import materialize

        dna_json = json.dumps({
            "signal_genes": [], "logic_genes": {}, "execution_genes": {},
            "risk_genes": {"stop_loss": 0.05, "position_size": 0.5,
                           "leverage": 1, "direction": "long",
                           "sl_mode": "pct", "atr_period": 14,
                           "initial_fraction": None,
                           "max_position_loss": None}})
        dna = StrategyDNA.from_dict(json.loads(dna_json))
        # DNA 面 null 键 → 字段 None=合法缺省（from_dict 直通承接）
        assert dna.risk_genes.initial_fraction is None
        assert dna.risk_genes.max_position_loss is None
        # 执行按缺省物化（0.0=未声明/禁用哨兵）
        params = materialize(dna.risk_genes)
        assert params.initial_fraction == 0.0
        assert params.max_position_loss == 0.0
