"""DNAModel 转换往返特征网（CARD-16 修复锚定，修复期安全网）。

锚定 api/schemas.py::DNAModel.to_dna() 作为传输模型→领域对象单一
转换入口的等价性（三调用点统一后）：
- 往返全等：StrategyDNA → dict → DNAModel → to_dna → dict（含显式
  strategy_id 与 v2 超集字段 signal_sources/layers）
- gene_signature 往返稳定
- 新旧路径等价：from_dict(model_dump()) 与旧第三路径
  from_json(model_dump_json()) 产出全等（显式 strategy_id 时）
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from api.schemas import DNAModel
from core.strategy.dna import StrategyDNA
from tests.helpers.data_factory import make_dna, make_mtf_dna


class TestToDnaRoundTrip:
    """往返锚定采用幂等口径。

    现状注记：StrategyDNA.from_dict 会补全 layers（execution 层入
    L: 段，gene_signature 同步带 L: 后缀）——「活对象 vs 一次往返」
    从来不全等（test_strategies_routes_gaps.py 已记录此现状）。本组
    锚定往返的收敛性：一轮补全后，后续往返稳定全等。
    """

    @staticmethod
    def _roundtrip(dna: StrategyDNA) -> StrategyDNA:
        return DNAModel.model_validate(dna.to_dict()).to_dna()

    def test_plain_dna_roundtrip_idempotent(self) -> None:
        """普通 DNA：一轮补全 layers 后，二轮往返全等。"""
        once = self._roundtrip(make_dna())
        twice = self._roundtrip(once)
        assert twice.to_dict() == once.to_dict()

    def test_mtf_dna_roundtrip_idempotent(self) -> None:
        """MTF DNA：显式 layers 原样保留且往返幂等。"""
        original = make_mtf_dna()
        once = self._roundtrip(original)
        assert once.layers, "MTF layers 应保留"
        twice = self._roundtrip(once)
        assert twice.to_dict() == once.to_dict()

    def test_explicit_strategy_id_preserved(self) -> None:
        """显式 strategy_id 往返保留（未显式时新生成属等价语义）。"""
        original = make_dna()
        original.strategy_id = "keep-this-id"
        model = DNAModel.model_validate(original.to_dict())
        assert model.to_dna().strategy_id == "keep-this-id"

    def test_gene_signature_stable_across_roundtrip(self) -> None:
        """签名幂等：一轮补全 L: 段后，后续往返签名稳定。"""
        once = self._roundtrip(make_dna())
        twice = self._roundtrip(once)
        assert twice.gene_signature == once.gene_signature
        assert "L:" in once.gene_signature  # 现状：补全 layers 进签名

    def test_risk_genes_survive_roundtrip(self) -> None:
        """risk_genes（leverage/direction/stop_loss 等）往返不漂移。"""
        original = make_dna(leverage=3, direction="short", stop_loss=0.02)
        model = DNAModel.model_validate(original.to_dict())
        revived = model.to_dna()
        assert revived.risk_genes.leverage == 3
        assert revived.risk_genes.direction == "short"
        assert revived.risk_genes.stop_loss == pytest.approx(0.02)


class TestOldAndNewPathEquivalence:
    """第三路径切换（JSON 往返 → dict 直转）的等价性持久锚定。"""

    def test_from_dict_equals_from_json_path(self) -> None:
        """from_dict(model_dump()) 与 from_json(model_dump_json()) 全等。"""
        original = make_dna()
        original.strategy_id = "fixed-id"
        model = DNAModel.model_validate(original.to_dict())
        by_dict = StrategyDNA.from_dict(model.model_dump())
        by_json = StrategyDNA.from_json(model.model_dump_json())
        assert by_dict.to_dict() == by_json.to_dict()

    def test_mtf_dna_both_paths_equal(self) -> None:
        original = make_mtf_dna()
        original.strategy_id = "mtf-fixed"
        model = DNAModel.model_validate(original.to_dict())
        by_dict = StrategyDNA.from_dict(model.model_dump())
        by_json = StrategyDNA.from_json(model.model_dump_json())
        assert by_dict.to_dict() == by_json.to_dict()
