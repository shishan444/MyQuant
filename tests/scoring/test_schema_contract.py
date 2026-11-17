"""scoring 值域的 schema 边界契约锚点（CARD-08/09 方向 A）。

分层语义：API 边界由 Literal 收紧做 422 拒绝；core 层直调保留宽容
回退（scorer.py objective 非法回退 sharpe、score_strategy 别名透传）
——两层各司其职，本文件锚定 API 层拒绝与字面量镜像的值域一致性
（schemas.py 注释承诺「漂移由特征测试锚定」，即此处）。
"""
import ast
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

pytestmark = [pytest.mark.unit]

from api.schemas import (
    RequirementsConfigModel,
    ScoreTemplateName,
    ScoringObjective,
)
from core.scoring.scorer import _VALID_OBJECTIVES

_SCORER_PATH = Path(__file__).resolve().parents[2] / "core" / "scoring" / "scorer.py"


def _scorer_alias_keys() -> set[str]:
    """ast 抽 score_strategy 函数体内 _ALIASES 字典的键集（权威源）。"""
    tree = ast.parse(_SCORER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_ALIASES" for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("score_strategy 内未找到 _ALIASES 赋值——权威源结构变更须同步本锚点")


class TestLiteralMirrorConsistency:
    """schemas 字面量镜像与 scorer 权威源程序化比对（防字面量漂移）。"""

    def test_scoring_objective_matches_valid_objectives(self):
        assert set(get_args(ScoringObjective)) == _VALID_OBJECTIVES

    def test_score_template_names_full_inventory(self):
        # 字面清单权威锚点：核心 3 + 别名 7
        assert set(get_args(ScoreTemplateName)) == {
            "explorer", "optimizer", "max_return",
            "balanced", "steady", "custom",
            "aggressive", "profit_first", "conservative", "risk_first",
        }

    def test_score_template_covers_all_scorer_aliases(self):
        # scorer 的别名键集必须被 schema 值域完整覆盖（新增别名漏配即红）
        assert _scorer_alias_keys() <= set(get_args(ScoreTemplateName))

    def test_score_template_covers_core_branch_names(self):
        # score_strategy 的核心分支名（optimizer/max_return 与默认 explorer）
        # 必须在 schema 值域内——不在则该核心名被 API 层误拒
        assert {"explorer", "optimizer", "max_return"} <= set(get_args(ScoreTemplateName))


class TestSchemaRejection:
    """RequirementsConfigModel 非法值 422 拒绝（pydantic 层）——
    Backtest/Compare/EvolutionTaskCreate 三模型已随核算与进化端点
    退役（retire-legacy 11.3），score_template 值域守护由
    TestLiteralMirrorConsistency 承载。"""

    def test_requirements_invalid_objective_rejected(self):
        with pytest.raises(ValidationError):
            RequirementsConfigModel(objective="bogus")

    @pytest.mark.parametrize("objective", ["sharpe", "calmar", "annual_return"])
    def test_requirements_legal_objectives_accepted(self, objective):
        assert RequirementsConfigModel(objective=objective).objective == objective

    @pytest.mark.parametrize(
        "template", ["explorer", "optimizer", "max_return", "profit_first", "risk_first"]
    )
    def test_legal_templates_accepted_including_aliases(self, template):
        # 合法值（含别名）在 ScoreTemplateName 值域接受（三核算模型
        # 退役后值域锚收缩至 Literal 本体）
        assert template in get_args(ScoreTemplateName)
