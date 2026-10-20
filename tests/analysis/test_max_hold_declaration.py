"""时间离场表达——声明与基因面（add-max-hold-exit D1/D2，DEC-1~DEC-9
+ VAL/LIVE）。

覆盖 tasks D1/D2 完成标准与 D5③：
- 值域常量双消费方（登记声明块+手工 DNA validate_dna）
- 拒绝四形态与 null 负向；幂等四分支携带；变体十一字段
- 基因扩展三处一致性（Optional 键/非 None 才写/from_dict 零迁移）
- 序列化与比对集回归（存量逐位不变）
- 三关+沙盒行为链走通（VAL-1/VAL-2）；live 范围外互证（LIVE-1）
"""

import inspect
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.hypothesis_to_strategy import (
    _D2_RISK_FIELDS,
    translate_hypothesis,
)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import get_hypothesis
from core.persistence.repository.strategy import get_strategy
from core.strategy.dna import RiskGenes
from core.strategy.validator import (
    MAX_HOLD_MAX,
    MAX_HOLD_MIN,
    validate_dna,
)


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
                         "field": "rsi"}, "action": "gt", "target": 999}],
    }


def _ranges() -> list:
    # 登记自报区间=warmup 段（自报区间直接进判定并集——VAL 链路请求
    # 区间须在自报段之外）
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-03-01T00:00:00Z"}]


def _register(db: Path, sp=None, direction=None):
    return register_hypothesis(
        db, conditions=_cond(), statement="max_hold 测试",
        data_ranges=_ranges(), strategy_params=sp, direction=direction)


def _risk_of(db: Path, strategy_id: str) -> dict:
    return json.loads(
        get_strategy(db, strategy_id)["dna_json"])["risk_genes"]


class TestDeclarationDomain:
    """DEC-2/DEC-3：正向边界与拒绝四形态。"""

    def test_positive_boundaries_pass(self, db):
        for i, v in enumerate((MAX_HOLD_MIN, 6, MAX_HOLD_MAX)):
            # 逐值独立条件集（同条件集异声明会被幂等四分支显式拒绝——
            # 参数变体走终态后重登记，见 TestIdempotentAndVariant）
            r = register_hypothesis(
                db, conditions=_cond(period=14 + i), statement=f"b{i}",
                data_ranges=_ranges(), strategy_params={"max_hold": v})
            assert r["duplicated"] is False

    @pytest.mark.parametrize("bad", [0, True, 300, 2.5])
    def test_reject_four_forms(self, db, bad):
        """下界/bool/上界/非整数四形态（DEC-3 拒绝参数化）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, sp={"max_hold": bad})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["field"] == "max_hold"
        assert ei.value.detail["legal_range"] == [1, 200]

    def test_explicit_null_rejected_by_existing_face(self, db):
        """null 负向：显式 null 由既有全键 null 拒绝面覆盖。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, sp={"max_hold": None})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "null" in str(ei.value)


class TestIdempotentAndVariant:
    """DEC-4/DEC-5：幂等四分支与变体路径。"""

    def test_idempotent_branches_carry_max_hold(self, db):
        r1 = _register(db, sp={"max_hold": 6})
        # 同值 → duplicated
        r2 = _register(db, sp={"max_hold": 6})
        assert r2["duplicated"] is True
        assert r2["hypothesis_id"] == r1["hypothesis_id"]
        # 不带键 → duplicated（旧调用方零适配）
        r3 = _register(db)
        assert r3["duplicated"] is True
        # 异值 → 422 附变体路径指引
        with pytest.raises(ResearchError) as ei:
            _register(db, sp={"max_hold": 24})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_variant_after_terminal_new_entity(self, db):
        """DEC-5：终态后异 max_hold 重登记→新实体→新 draft（十一字段比对
        max_hold 异→不误返既有）。"""
        from core.analysis.hypothesis_service import transition_hypothesis
        h1 = _register(db, sp={"max_hold": 6})
        t1 = translate_hypothesis(db, hypothesis_id=h1["hypothesis_id"])
        assert _risk_of(db, t1["strategy_id"])["max_hold"] == 6
        # 终态后重登记（abandoned 免证据）
        transition_hypothesis(db, h1["hypothesis_id"],
                              target_status="abandoned",
                              evidence_ref="测试变体路径")
        h2 = _register(db, sp={"max_hold": 24})
        assert h2["hypothesis_id"] != h1["hypothesis_id"]
        t2 = translate_hypothesis(db, hypothesis_id=h2["hypothesis_id"])
        assert t2["strategy_id"] != t1["strategy_id"]
        assert _risk_of(db, t2["strategy_id"])["max_hold"] == 24


class TestGeneAndTranslation:
    """DEC-1/DEC-6/DEC-8：表达链、无声明、序列化回归。"""

    def test_expression_chain_declared(self, db):
        """DEC-1：声明→翻译→DNA 一致（risk_genes="declared"）。"""
        h = _register(db, sp={"max_hold": 6})
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["risk_genes"] == "declared"
        rg = _risk_of(db, t["strategy_id"])
        assert rg["max_hold"] == 6

    def test_no_declaration_product_has_no_key(self, db):
        """DEC-6：无声明产物无该键（RiskGenes 默认 None 不进 to_dict）。"""
        h = _register(db)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        rg = _risk_of(db, t["strategy_id"])
        assert "max_hold" not in rg

    def test_to_dict_none_omitted_from_dict_zero_migration(self):
        """DEC-8：非 None 才写；存量 dict 缺键读回 None（零迁移）。"""
        assert "max_hold" not in RiskGenes().to_dict()
        assert RiskGenes().to_dict() == RiskGenes(stop_loss=0.05).to_dict()
        legacy = {k: v for k, v in RiskGenes(stop_loss=0.05).to_dict().items()}
        assert RiskGenes.from_dict(legacy).max_hold is None
        assert RiskGenes.from_dict(
            RiskGenes(max_hold=6).to_dict()).max_hold == 6

    def test_d2_fields_ten_and_legacy_parity(self):
        """DEC-8 补：比对集十五字段（complete-research-tooling 任务 4
        扩 position_mode/risk_fraction；add-position-sizing-params 5.2
        扩 initial_fraction/max_position_loss）；存量侧（手写字面 dict
        模拟库内旧 dna_json——无 max_hold 键）与无声明产物经比对集
        逐字段恒等——扩键连续（get 缺键读 None 语义）。"""
        assert "max_hold" in _D2_RISK_FIELDS
        assert len(_D2_RISK_FIELDS) == 15
        legacy_dict = {  # 存量库内 dna_json 形（无新键字面）
            "stop_loss": 0.05, "take_profit": None,
            "position_size": 0.3, "leverage": 1, "direction": "long",
            "sl_mode": "pct", "atr_period": 14,
            "exit_fraction": None, "add_fraction": None,
            "max_exposure": None,
        }
        produced_dict = RiskGenes(stop_loss=0.05).to_dict()
        assert tuple(legacy_dict.get(f) for f in _D2_RISK_FIELDS) == \
            tuple(produced_dict.get(f) for f in _D2_RISK_FIELDS)


class TestManualDnaValidation:
    """DEC-7：validate_dna unit（手工 DNA 双消费方）。"""

    def _dna_with(self, **kw):
        from core.strategy.dna import (
            ExecutionGenes,
            LogicGenes,
            SignalGene,
            SignalRole,
            StrategyDNA,
        )
        entry = SignalGene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
            condition={"type": "lt", "threshold": 35})
        exit_g = SignalGene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.EXIT_TRIGGER, field_name="rsi_14",
            condition={"type": "gt", "threshold": 65})
        return StrategyDNA(
            signal_genes=[entry, exit_g], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(**kw))

    def test_out_of_range_flagged(self):
        result = validate_dna(self._dna_with(max_hold=250))
        assert not result.is_valid
        assert any("max_hold" in e and "range" in e for e in result.errors)

    def test_none_tolerated(self):
        result = validate_dna(self._dna_with())
        assert result.is_valid

    def test_boundaries_valid(self):
        assert validate_dna(self._dna_with(max_hold=1)).is_valid
        assert validate_dna(self._dna_with(max_hold=200)).is_valid

    def test_bool_flagged(self):
        """S-2：bool 是 int 子类穿透值域比较（1<=True<=200 成立，
        等效 N=1=入场次 bar 即平仓）——类型层排除，与登记面
        「bool 排除」（strategy-param-declaration）同口径。"""
        result = validate_dna(self._dna_with(max_hold=True))
        assert not result.is_valid
        assert any("max_hold" in e and "integer" in e
                   for e in result.errors)

    def test_float_flagged(self):
        """S-2：float 穿透值域取整（3.5 等效 ceil=第 4 根平仓的
        未定义行为）——类型层拦截（spec「正整数 N」）。"""
        result = validate_dna(self._dna_with(max_hold=3.5))
        assert not result.is_valid
        assert any("max_hold" in e and "integer" in e
                   for e in result.errors)

    def test_str_flagged_not_raise(self):
        """S-2：str 形态修复前在值域比较裸抛 TypeError（int<=str
        不可比较）——现类型层 errors.append，不抛异常。"""
        result = validate_dna(self._dna_with(max_hold="4"))
        assert not result.is_valid
        assert any("max_hold" in e and "integer" in e
                   for e in result.errors)

    def test_valid_int_unaffected(self):
        """回归锚：合法 int 不受类型分支影响（防过度收紧）。"""
        result = validate_dna(self._dna_with(max_hold=4))
        assert result.is_valid


class TestMcpDisclosure:
    def test_description_nine_keys(self):
        """DEC-9：register_hypothesis description 披露第九键语义。"""

        class _FakeMcp:
            registered = {}

            def tool(self):
                def deco(fn):
                    self.registered[fn.__name__] = fn
                    return fn
                return deco

        class _FakeApi:
            def post(self, url, body):
                return {}

        from mcpserver import tools as tools_mod
        mcp, api = _FakeMcp(), _FakeApi()
        tools_mod.register_research_tools(mcp, api)
        doc = inspect.getdoc(mcp.registered["register_hypothesis"])
        assert "max_hold" in doc
        assert "[1,200]" in doc
        assert "时间出场" in doc


class TestValidationLinkBehavior:
    """VAL-1/VAL-2（D5③）：带 max_hold 的 draft 走三关+沙盒——链路走通
    与口径不变（行为佐证=同数据同信号有无 max_hold 的 total_trades
    差异——时间出场改变交易切割必现差异）。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(3)
        n = 500
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 150, n))
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        pd.DataFrame({
            "open": close, "high": close + 80, "low": close - 80,
            "close": close, "volume": rng.uniform(100, 1000, n),
            "trades": 100,
        }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        return {"db": db, "data_dir": data_dir}

    def test_iso_and_sandbox_with_max_hold(self, env):
        from MyQuant.api.app import create_app
        from fastapi.testclient import TestClient
        from core.persistence.repository.strategy import save_strategy
        from core.strategy.dna import (
            ExecutionGenes,
            LogicGenes,
            SignalGene,
            SignalRole,
            StrategyDNA,
        )

        gene = SignalGene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
            condition={"type": "lt", "threshold": 45})

        # 卡 R 起装载挂全量 validate_dna（Q-1）：补恒假 exit（行为
        # 等效——max_hold 出场才是被测面，RSI 出场恒不触发）
        exit_g = SignalGene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.EXIT_TRIGGER, field_name="rsi_14",
            condition={"type": "gt", "threshold": 1e9})

        def _save(sid, mh):
            rg = RiskGenes(max_hold=mh) if mh else RiskGenes()
            dna = StrategyDNA(
                signal_genes=[gene, exit_g], logic_genes=LogicGenes(),
                execution_genes=ExecutionGenes(timeframe="4h"),
                risk_genes=rg)
            # status=draft 免签名去重（既有机制：非 draft 同签名保留
            # best_score 更高者——max_hold 不进签名，两策略签名相同）
            save_strategy(env["db"], strategy_id=sid, dna_json=dna.to_json(),
                          symbol="BTCUSDT", timeframe="4h", name=sid,
                          status="draft")

        h = _register(env["db"])
        _save("st_mh2", 2)
        _save("st_plain", None)

        app = create_app(db_path=env["db"], data_dir=env["data_dir"])
        with TestClient(app) as client:
            r = client.post("/api/research/validate-backtest", json={
                "hypothesis_id": h["hypothesis_id"], "strategy_id": "st_mh2",
                "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                                  "start": "2024-03-01T00:00:00Z",
                                  "end": "2024-03-20T00:00:00Z"}})
            assert r.status_code == 200, r.text
            body = r.json()
            assert "total_return" in body["metrics"]
            assert "win_rate" in body["metrics"]
            trades_mh = body["metrics"]["total_trades"]
            assert trades_mh > 0

            sb = client.post("/api/research/backtest-sandbox", json={
                "strategy_id": "st_mh2", "symbol": "BTCUSDT",
                "timeframe": "4h", "start": "2024-03-01T00:00:00Z",
                "end": "2024-03-20T00:00:00Z"})
            assert sb.status_code == 200, sb.text
            assert "total_return" in sb.json()["metrics"]

            # 行为佐证：同数据同信号，有无 max_hold 的交易切割必现差异
            r0 = client.post("/api/research/validate-backtest", json={
                "hypothesis_id": h["hypothesis_id"],
                "strategy_id": "st_plain",
                "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                                  "start": "2024-03-21T00:00:00Z",
                                  "end": "2024-04-09T00:00:00Z"}})
            assert r0.status_code == 200, r0.text
            trades_plain = r0.json()["metrics"]["total_trades"]
            assert trades_mh != trades_plain, \
                "max_hold=2 与无声明在触发充分段必现交易数差异"


class TestLiveOutOfRange:
    def test_live_executor_does_not_consume_max_hold(self):
        """LIVE-1：live 范围外互证——core/live_trading 不消费 max_hold
        （出场承载现状缺口族成员，实盘化前统一处理）。"""
        src = Path("core/live_trading/executor.py").read_text(encoding="utf-8")
        assert "max_hold" not in src


class TestReviewFindingsRegression:
    """team3 审查发现（security2 S-1/S-2）的回归锚。"""

    def test_transport_model_accepts_max_hold_dna(self):
        """S-1：api 传输模型同步声明 max_hold——含键 DNA 可直传/回显
        （extra=forbid 漏键会拒收，且 crud 读回 ValidationError 被吞
        → GET dna=null 的 DNA 静默消失）。"""
        from api.schemas import DNAModel
        from core.strategy.dna import (
            ExecutionGenes,
            LogicGenes,
            SignalGene,
            SignalRole,
            StrategyDNA,
        )
        gene = SignalGene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
            condition={"type": "lt", "threshold": 35})
        dna = StrategyDNA(
            signal_genes=[gene], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(max_hold=6))
        model = DNAModel.model_validate(json.loads(dna.to_json()))
        assert model.risk_genes.max_hold == 6

    @pytest.mark.parametrize("bad", [True, 3.5, "6"])
    def test_validate_dna_type_guard(self, bad):
        """S-2：手工 DNA 面类型防护——bool 穿透值域静默化为 N=1、
        float 穿透取整、str 裸 TypeError，均在类型层拦截。"""
        from core.strategy.dna import (
            ExecutionGenes,
            LogicGenes,
            SignalGene,
            SignalRole,
            StrategyDNA,
        )
        gene = SignalGene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
            condition={"type": "lt", "threshold": 35})
        exit_g = SignalGene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.EXIT_TRIGGER, field_name="rsi_14",
            condition={"type": "gt", "threshold": 65})
        dna = StrategyDNA(
            signal_genes=[gene, exit_g], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(max_hold=bad))
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("max_hold" in e for e in result.errors)
