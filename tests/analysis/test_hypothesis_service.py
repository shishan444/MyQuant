"""假设服务入口校验特征测试（修复期特征网：记录现状行为）。

hypothesis_service 的登记/迁移主链路由 test_strategy_v2 / test_mtf_-
conditions / test_draft_lifecycle 承接；本文件补两处零覆盖：
allowed_subject_columns（subject 可引用列清单——此前全域零引用）与
transition_hypothesis 的 superseded 证据校验分支。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.column_ref import BASE_SUBJECT_COLUMNS
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import (
    ResearchError,
    allowed_subject_columns,
    register_hypothesis,
    transition_hypothesis,
)
from core.persistence.db_ext import init_db_ext


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _register(db: Path, statement: str = "s") -> str:
    return register_hypothesis(
        db,
        conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        },
        statement=statement,
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2024-01-01T00:00:00Z",
                      "end": "2024-02-01T00:00:00Z"}],
    )["hypothesis_id"]


class TestAllowedSubjectColumns:
    def test_空库_基础列全集与注册表前缀集(self, db: Path) -> None:
        cols, prefixes = allowed_subject_columns(db)

        # 精确列全集 ⊇ 八个基础列（open/high/low/close/volume/trades/
        # open_interest/funding_rate）
        assert BASE_SUBJECT_COLUMNS <= cols
        # 前缀集 = 指标注册表 output_fields 拍平（单一事实源锚——
        # 前缀集不得另立来源）
        from core.features.registry import INDICATOR_REGISTRY
        expected = {f for defn in INDICATOR_REGISTRY.values()
                    for f in defn.output_fields}
        assert prefixes == expected
        assert prefixes  # 非空（注册表非空）

    def test_已注册插件列_并入精确列集(self, db: Path) -> None:
        from core.analysis.plugin_service import register_formula_plugin
        before_cols, _ = allowed_subject_columns(db)
        register_formula_plugin(db, name="fxa", expression="zscore(close, 20)")

        cols, _ = allowed_subject_columns(db)

        added = cols - before_cols
        # 现状：version=1 注册列名 = fx_<name>（并入 subject 可引用集）
        assert added == {"fx_fxa"}

    def test_前缀集不含基础列(self, db: Path) -> None:
        # 前缀集仅指标输出字段——基础列走精确列集，两集职责分离
        _, prefixes = allowed_subject_columns(db)
        assert not (prefixes & BASE_SUBJECT_COLUMNS)


class TestTransitionSuperseded:
    def test_接替假设不存在_拒绝(self, db: Path) -> None:
        hid = _register(db)
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="superseded",
                                  evidence_ref="hyp_ghost")
        assert ei.value.code == "RESEARCH_EVIDENCE_NOT_FOUND"

    def test_接替假设存在_迁移成功(self, db: Path) -> None:
        succ = _register(db, statement="接替者")
        hid = _register(db, statement="被接替")

        out = transition_hypothesis(db, hid, target_status="superseded",
                                    evidence_ref=succ)

        assert out["hypothesis_id"] == hid
        assert out["status"] == "superseded"
        assert out["idempotent"] is False
        # superseded 非转正态
        assert out["promoted"] is False

    def test_被接替后即终态_不可再迁移(self, db: Path) -> None:
        succ = _register(db, statement="接替者")
        hid = _register(db, statement="被接替")
        transition_hypothesis(db, hid, target_status="superseded",
                              evidence_ref=succ)

        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="abandoned",
                                  evidence_ref="说明文本")
        assert ei.value.code == "RESEARCH_HYPOTHESIS_TERMINAL"
        assert ei.value.status == 409
