"""strategy_params 显式 null 全链拒绝（review-042 CARD-01）。

修复前现象：七数值键显式 null 通过登记校验（值域检查 is not None
全跳过）→ 落库 {"stop_loss": null} → 翻译 None 直写 RiskGenes →
下游 falsy→0.0 止损静默禁用、live 链 float(None) 崩溃；
{"leverage": null, "max_exposure": 0.5} 在 service 层 ``0.5 <= None``
TypeError → HTTP 500。

锚组：
- service 层七数值键各一 null → 422 RESEARCH_PARAM_INVALID
  （detail 附 field 与 null_keys）
- 键缺失仍合法对照（null 拒绝不外溢到缺省语义）
- API 层 null 422（含原 HTTP 500 组合的修复锚）
- 翻译侧纵深：null 值键不写入 RiskGenes（存量库绕过登记链的
  形态按缺省态处理）
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import get_hypothesis
from core.persistence.repository.strategy import get_strategy
from core.strategy.dna import RiskGenes


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


def _register(db: Path, sp=None, period: int = 14):
    return register_hypothesis(
        db, conditions=_cond(period), statement="null-anchor",
        data_ranges=_ranges(), strategy_params=sp)


# ── service 层：七数值键各一 null → 422 ────────────────────────────

# 八键白名单中 sl_mode 为字符串枚举（null 已被枚举检查拒，错误信息
# 不同）；七数值键的 null 与「无值」形似实异——值域检查 is not None
# 跳过即静默放行，故单列锚组
_NULLABLE_NUMERIC_KEYS = (
    "stop_loss", "take_profit", "position_size", "leverage",
    "exit_fraction", "add_fraction", "max_exposure",
)


class TestNullParamsRejected:
    @pytest.mark.parametrize("key", _NULLABLE_NUMERIC_KEYS)
    def test_each_numeric_key_null_rejected(self, db, key):
        """七数值键各一 null：422 RESEARCH_PARAM_INVALID + detail 断言。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, {key: None})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        d = ei.value.detail
        assert d["field"] == f"strategy_params.{key}"
        assert d["null_keys"] == [key]

    def test_multi_null_keys_reported(self, db):
        """多 null 键一并披露（null_keys 全量，field 取首键）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, {"stop_loss": None, "leverage": None})
        assert ei.value.detail["null_keys"] == ["stop_loss", "leverage"]
        assert ei.value.detail["field"] == "strategy_params.stop_loss"

    def test_missing_key_still_legal(self, db):
        """键缺失仍合法对照锚：null 拒绝不外溢——不带键登记照常成功。"""
        r = _register(db)
        assert r["duplicated"] is False
        # 落库列 NULL（与显式 {} 在幂等层同形）
        assert get_hypothesis(db, r["hypothesis_id"])["strategy_params"] is None

    def test_null_rejected_before_pairing_check(self, db):
        """null 拒绝先于组间配对：无宿主组时仍因 null 拒（detail 证因）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, {"exit_fraction": None})
        assert ei.value.detail["field"] == "strategy_params.exit_fraction"


# ── API 层：全链 422（含原 HTTP 500 修复锚）────────────────────────


class TestApiNullRejected:
    def _client(self, tmp_path: Path, tmp_data_dir: Path):
        from fastapi.testclient import TestClient

        from api.app import create_app

        d = tmp_path / "api.db"
        init_db_ext(d)
        ensure_research_tables(d)
        app = create_app(db_path=d, data_dir=tmp_data_dir)
        return TestClient(app)

    def test_api_null_422(self, tmp_data_dir, tmp_path):
        """JSON 显式 null 经 API 全链 422（传输层不得吞成缺省）。"""
        with self._client(tmp_path, tmp_data_dir) as client:
            r = client.post("/api/research/hypotheses", json={
                "conditions": _cond(), "statement": "api null",
                "data_ranges": _ranges(),
                "strategy_params": {"stop_loss": None},
            })
            assert r.status_code == 422
            assert r.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"

    def test_api_leverage_null_max_exposure_422(self, tmp_data_dir, tmp_path):
        """原 HTTP 500 组合修复锚：{"leverage": null, "max_exposure": 0.5}
        曾在 service 层 ``0.5 <= None`` TypeError → 500，现 null 先拒 422。"""
        with self._client(tmp_path, tmp_data_dir) as client:
            r = client.post("/api/research/hypotheses", json={
                "conditions": _cond(), "statement": "api 500 fix",
                "data_ranges": _ranges(),
                "strategy_params": {"leverage": None, "max_exposure": 0.5},
            })
            assert r.status_code == 422
            assert r.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"


# ── 翻译侧纵深：null 键不写入 RiskGenes ─────────────────────────────


class TestTranslationNullDefense:
    def test_declared_null_key_translates_to_default(self, db):
        """纵深锚：库内 strategy_params 含 null 键（绕过登记链的存量/
        手工形态）翻译不穿透 None——null 键按缺省态，产物为平台默认。"""
        import sqlite3

        r = _register(db)
        hid = r["hypothesis_id"]
        # 直改列模拟存量 null 形态（登记链已拒新写入——纵深防的是旧库）
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE research_hypotheses SET strategy_params = ? "
                "WHERE hypothesis_id = ?",
                (json.dumps({"stop_loss": None}), hid))
        t = translate_hypothesis(db, hypothesis_id=hid)
        rg = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])["risk_genes"]
        assert rg == RiskGenes().to_dict()  # null 不写 None，缺省态逐位一致

    def test_declared_risk_genes_skips_null_keys(self):
        """_declared_risk_genes 直调锚：null 值键不进构造参数。"""
        from core.analysis.hypothesis_to_strategy import _declared_risk_genes

        rg = _declared_risk_genes({"stop_loss": None, "leverage": 3})
        assert rg.stop_loss == RiskGenes().stop_loss  # null 键走默认
        assert rg.leverage == 3                        # 非 null 键照常写入


# ── CARD-03：声明值=平台默认时来源标注一致性 ────────────────────────


class TestOriginAnnotationConsistency:
    """声明值恰等于平台默认（如 stop_loss=0.05）时，任何返回路径的
    risk_genes 标注均须为 "declared"（「有声明→declared」）；修复前
    分流命中路径按九字段与默认全等判误标 "platform_default"。"""

    def test_declared_equal_to_default_annotates_declared(self, db):
        """新建路径：登记 {"stop_loss": 0.05}（=RiskGenes 默认）→ declared。"""
        h = _register(db, {"stop_loss": 0.05})
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["duplicated"] is False
        assert t["risk_genes"] == "declared"

    def test_diversion_hit_keeps_declared_annotation(self, db):
        """分流命中路径：手工预插同签名同九字段（全等平台默认）既有
        active 策略后翻译——命中 active_hit 分流仍标 declared。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, SignalGene, SignalRole, StrategyDNA,
        )
        from core.persistence.repository.strategy import save_strategy

        genes = [
            # field_name="rsi" 对齐翻译产物形制（等价复核逐键比对含
            # field_name——语义字段不归一，构造侧对齐；与 test_strategy_
            # param_declaration._genes() 同款修正）
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       field_name="rsi",
                       condition={"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       field_name="rsi",
                       condition={"type": "gt", "threshold": 70}),
        ]
        manual = StrategyDNA(
            signal_genes=genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes())  # 九字段全等平台默认
        save_strategy(db, strategy_id="st_card03", dna_json=manual.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="card03")

        h = _register(db, {"stop_loss": 0.05})  # 声明值=默认，九字段全等
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["same_dna_existing"] is True   # 命中分流（修复目标场景）
        assert t["strategy_id"] == "st_card03"
        assert t["risk_genes"] == "declared"    # 修复前此处误标 platform_default


# ── CARD-14：结构化分支 bool target 排除（与插件分支对称）──────────


class TestBoolTargetRejected:
    """结构化 subject 分支的 target=True/False 是布尔断言非阈值——
    修复前 isinstance(True, (int, float)) 放行且 float(True)=1.0
    静默收窄；HTTP/MCP 双入口 pydantic 已拦，此锚服务内部直调纵深
    （build_signal_genes 公开入口直调）。"""

    def test_bool_target_rejected(self, db):
        from core.analysis.hypothesis_to_strategy import build_signal_genes

        cond = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"},
                 "action": "gt", "target": True}]
        with pytest.raises(ResearchError) as ei:
            build_signal_genes(cond, [], db)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"

    def test_numeric_target_still_accepted(self, db):
        """对照锚：数值 target 照常翻译（bool 排除不外溢）。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes

        cond = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"},
                 "action": "gt", "target": 70}]
        genes, _ = build_signal_genes(cond, [], db)
        assert len(genes) == 1
