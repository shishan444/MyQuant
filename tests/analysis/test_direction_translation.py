"""做空假设通道——翻译直通（add-direction-channel A3，A2-1~A2-4）。

覆盖 tasks A3 完成标准四项：
- short translate 产物 risk_genes.direction=="short" 且 gene_signature 含 "dir:short"
- 存量/无键假设产物 direction 保持 long（缺省路径产物零变化）
- strategy_params 带 direction 仍按未知键 422（负向锚防双源）
- short draft 挂 short 假设、同签名分流不跨方向误命中
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _cond(period: int = 14) -> dict:
    return {
        "when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "lt", "target": 30}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "gt", "target": 70}],
    }


def _ranges() -> list:
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]


def _register(db: Path, direction=None, sp=None):
    return register_hypothesis(
        db, conditions=_cond(), statement="方向翻译测试",
        data_ranges=_ranges(), strategy_params=sp, direction=direction)


def _dna_of(db: Path, strategy_id: str) -> dict:
    return json.loads(get_strategy(db, strategy_id)["dna_json"])


def _signature_of(db: Path, strategy_id: str) -> str:
    from core.strategy.dna import StrategyDNA
    return StrategyDNA.from_json(
        get_strategy(db, strategy_id)["dna_json"]).gene_signature


class TestDirectionThroughTranslation:
    def test_short_product_direction_and_signature(self, db):
        """A2-1：short 假设翻译产物 direction="short"、签名含 dir:short。"""
        h = _register(db, direction="short")
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        dna = _dna_of(db, t["strategy_id"])
        assert dna["risk_genes"]["direction"] == "short"
        assert "dir:short" in _signature_of(db, t["strategy_id"])

    def test_missing_key_product_stays_long(self, db):
        """A2-2：无键假设产物 direction 保持 long（缺省路径零变化），
        签名含 dir:long（引擎方向机制既有编码）。"""
        h = _register(db)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        dna = _dna_of(db, t["strategy_id"])
        assert dna["risk_genes"]["direction"] == "long"
        assert "dir:long" in _signature_of(db, t["strategy_id"])

    def test_strategy_params_direction_rejected(self, db):
        """A2-3：往声明块塞 direction 仍按未知键 422（方向无双源）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, direction="short", sp={"direction": "short"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["unknown_keys"] == ["direction"]

    def test_short_draft_lineage_and_no_cross_direction_hit(self, db):
        """A2-4：short draft 挂 short 假设；先 long 后 short 翻译分流
        不跨方向误命中（签名 dir: 异 → 建新实体而非 duplicated）。"""
        h_long = _register(db)
        t_long = translate_hypothesis(db, hypothesis_id=h_long["hypothesis_id"])

        h_short = _register(db, direction="short")
        t_short = translate_hypothesis(
            db, hypothesis_id=h_short["hypothesis_id"])

        # short draft 谱系挂 short 假设
        short_dna = _dna_of(db, t_short["strategy_id"])
        assert short_dna["parent_ids"] == [h_short["hypothesis_id"]]
        # 分流不跨方向：两产物为不同实体（签名异 → 不命中既有）
        assert t_short["strategy_id"] != t_long["strategy_id"]
        assert t_short["duplicated"] is False
        assert t_short["same_dna_existing"] is False


class TestRiskGenesOfRow:
    """_risk_genes_of_row 直测（fix-fullrun-baseline-4red 阶段四盲区
    :1011-1012——身份回读消费的解析兜底此前未触达）。"""

    @pytest.mark.parametrize(
        ("row", "expected"),
        [
            pytest.param({"dna_json": '{"risk_genes": {"direction": "short"}}'},
                         {"direction": "short"}, id="合法段原样返回"),
            pytest.param({"dna_json": '{"risk_genes": [1, 2]}'}, {},
                         id="rg 非 dict 归空"),
            pytest.param({"dna_json": '{"signal_genes": []}'}, {},
                         id="缺段归空"),
            pytest.param({"dna_json": None}, {}, id="缺 dna_json 归空"),
            pytest.param({"dna_json": "{not-json"}, {}, id="坏 JSON 归空"),
            pytest.param({"dna_json": 42}, {}, id="非 str dna_json 归空"),
        ])
    def test_row_to_risk_genes(self, row, expected):
        from core.analysis.hypothesis_to_strategy import _risk_genes_of_row
        assert _risk_genes_of_row(row) == expected


# ---------------- 不可达判定锚（阶段四盲区清单） ----------------
# 以下防御分支经调用图核验结构上不可达，锚定判定依据防误判为测试缺口：
# 1. hypothesis_to_strategy.py:505-509（g_sources 非空收口）：源码自述
#    「结构上不可达（插件列在非 ENTRY 角色已被拒）」——插件列条件
#    在进入该分组翻译前已由角色守卫拒绝，g_sources 恒空，raise 仅作
#    防御性收口（不静默丢弃插件源）。
# 2. hypothesis_to_strategy.py:718-722（action 不可表达列名 target）：
#    源码自述「保留为 _ACTION_MAP 未来扩键的防御纵深」——当前
#    _ACTION_MAP 键集与上游 action 白名单同步，扩键未同步时显式
#    拒绝优于静默漏翻译；正常链到不了该 raise。
