"""schema-boundary-symmetry 契约锚（improve-schema-bound-symmetry）。

四 Scenario 对应 spec delta（新建域 schema-boundary-symmetry）：
SYM1 杠杆五处对齐（请求四+响应一全 Field(ge=1, le=MAX_LEVERAGE) 同源）；
SYM2 越界值响应面显式暴露（库旁路值经响应模型校验显式报错非静默透传）；
SYM3 合法数据零影响（合法值全过——补边界前后响应逐位一致的声明面）；
SYM4 新增模型对称基线在案（盘点清单=新模型对照物载体）。
"""

import pytest
from pydantic import BaseModel, ValidationError

from api.schemas import (
    BatchBacktestRequest,
    EvolutionTaskCreate,
    EvolutionTaskResponse,
    RiskGenesModel,
    VerifyRequest,
)
from core.strategy.validator import MAX_LEVERAGE

pytestmark = [pytest.mark.unit]

# 杠杆边界字段所在模型全集（盘点清单 A-1：请求侧四处+响应侧补齐一处）
_LEVERAGE_MODELS = (
    RiskGenesModel, VerifyRequest, BatchBacktestRequest,
    EvolutionTaskCreate, EvolutionTaskResponse,
)


def _field_bounds(model: type[BaseModel], name: str):
    """取模型字段的 (ge, le) 声明（FieldInfo metadata 中取约束）。"""
    info = model.model_fields[name]
    ge = le = None
    for m in info.metadata:
        ge = getattr(m, "ge", None) or ge
        le = getattr(m, "le", None) or le
    return ge, le


class TestSym1LeverageFiveFieldsAligned:
    def test_all_leverage_fields_share_max_leverage_source(self):
        """SYM1：五处 leverage 全部 ge=1/le=MAX_LEVERAGE（同源引用——
        le 绑定常量对象值，禁散写字面量）。"""
        for model in _LEVERAGE_MODELS:
            ge, le = _field_bounds(model, "leverage")
            assert ge == 1, f"{model.__name__}.leverage ge={ge}"
            assert le == MAX_LEVERAGE, \
                f"{model.__name__}.leverage le={le} != MAX_LEVERAGE"


def _resp(**extra):
    """EvolutionTaskResponse 最小合法构造（必填八字段齐备——SYM2 的
    ValidationError 断言面须由越界触发而非缺字段触发，防断言污染）。"""
    return dict(task_id="t", status="running", target_score=1.0,
                score_template="default", symbol="BTCUSDT", timeframe="4h",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z", **extra)


class TestSym2OutOfRangeExposed:
    @pytest.mark.parametrize("bad", [0, MAX_LEVERAGE + 1, 10 ** 6])
    def test_response_model_rejects_out_of_range(self, bad):
        """SYM2：越界值（库旁路场景——本机直改库后回读构造响应）经
        响应模型显式报错，不静默透传给调用方。"""
        with pytest.raises(ValidationError, match="leverage"):
            EvolutionTaskResponse(**_resp(leverage=bad))

    def test_min_annual_return_bounds_enforced(self):
        with pytest.raises(ValidationError, match="min_annual_return"):
            EvolutionTaskResponse(**_resp(min_annual_return=99.0))

    def test_max_drawdown_limit_bounds_enforced(self):
        with pytest.raises(ValidationError, match="max_drawdown_limit"):
            EvolutionTaskResponse(**_resp(max_drawdown_limit=0.99))


class TestSym3LegalDataZeroImpact:
    @pytest.mark.parametrize("ok", [1, 3, MAX_LEVERAGE])
    def test_legal_values_pass_all_models(self, ok):
        """SYM3：合法值（经请求侧防线的库内值域）构造全过——补边界
        前后响应逐位一致的声明面（越界值库内不存在：盘点核查全绿）。"""
        assert EvolutionTaskResponse(
            **_resp(leverage=ok)).leverage == ok
        assert EvolutionTaskCreate(
            symbol="BTCUSDT", timeframe="4h",
            leverage=ok).leverage == ok


class TestSym4BaselineDocumented:
    def test_symmetry_inventory_in_change_dir(self):
        """SYM4：对称基线文档（71 模型盘点清单）在 change 目录在案——
        「新增模型有对称基线可循」验收要点的载体。"""
        from pathlib import Path

        p = (Path(__file__).resolve().parents[2].parent
             / "openspec/changes/improve-schema-bound-symmetry"
             / "schema-symmetry-inventory.md")
        if not p.exists():
            # 归档后位置（archive/ 前缀日期动态）——退查 archive 目录
            archive = p.parent.parent / "archive"
            hits = list(archive.glob(
                "*-improve-schema-bound-symmetry/schema-symmetry-inventory.md"))
            assert hits, f"对称基线清单不在案：{p} 与 archive 均无"
            p = hits[0]
        text = p.read_text()
        assert "EvolutionTaskResponse" in text
        assert "leverage" in text
