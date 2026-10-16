"""策略层参数声明块（0.4.2 R1，strategy-param-declaration 规格）。

B1+B2 验证组（挂 tasks 2.2/2.3/2.4/2.5，R1 口径=五参数）：
- 五键域与关系校验正反用例（值域按 sl_mode 分叉三维参数化）
- 三键（exit_fraction/add_fraction/max_exposure）未知键 422
  ——R1 阶段形态锚（4.1 扩白名单时同任务即时翻转为域校验断言，
  防中间态 CI 红窗；翻转后本组退役改写）
- 幂等四分支 + 显式空对象三条（{} vs NULL / {} vs 非空 / 键缺失）
- 翻译声明→DNA 逐字段一致 + 返回体 declared/platform_default 两值
- 缺省路径回归（无声明登记+翻译与现状逐位一致）
- 变体端到端（终态后重登记异参→新假设→新实体，同签名异参数并存）
- 分流两线（线 1 签名路径 / 线 2 九字段 D2 兜底含 atr_period 异值锚）
- 连续性锚（存量 dna_json 无新键读 None）
- API 层 {} 传输保全（strategy_params={} 经 API 全链到达 service 仍是
  空 dict 而非 None——传输层不得丢成缺省）
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
from core.persistence.repository.strategy import get_strategy, save_strategy
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _cond(period: int = 14, lo: int = 30, hi: int = 70) -> dict:
    return {
        "when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "lt", "target": lo}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "gt", "target": hi}],
    }


def _ranges() -> list:
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]


def _register(db: Path, sp=None, period: int = 14, lo: int = 30, hi: int = 70,
              statement: str = "s"):
    return register_hypothesis(
        db, conditions=_cond(period, lo, hi), statement=statement,
        data_ranges=_ranges(), strategy_params=sp)


# ── B1：五键域与关系校验（三维参数化：入口=声明块、参数、sl_mode）──


class TestParamDomainValidation:
    """R1 口径五参数：值域按 sl_mode 分叉 + 关系约束 + 未知键拒绝。"""

    @pytest.mark.parametrize("params,ok", [
        # pct 档（缺省 sl_mode=pct）
        ({"stop_loss": 0.05}, True),
        ({"stop_loss": 0.005}, True),   # 下边界
        ({"stop_loss": 0.20}, True),    # 上边界
        ({"stop_loss": 0.001}, False),
        ({"stop_loss": 2.0}, False),    # pct 档比例域外（同值异判锚）
        ({"stop_loss": 0.05, "take_profit": 0.10}, True),
        ({"stop_loss": 0.05, "take_profit": 0.03}, False),  # tp<=sl
        # atr 档（倍数域）
        ({"sl_mode": "atr", "stop_loss": 2.0, "take_profit": 4.0}, True),
        ({"sl_mode": "atr", "stop_loss": 0.5}, True),   # 下边界
        ({"sl_mode": "atr", "stop_loss": 5.0}, True),   # 上边界
        ({"sl_mode": "atr", "stop_loss": 8.0}, False),
        ({"sl_mode": "atr", "stop_loss": 0.05}, False),  # 误放防护锚
        ({"sl_mode": "atr", "stop_loss": 1.5}, True),    # 1.5 在 atr 过（pct 拒）
        ({"sl_mode": "atr", "stop_loss": 2.0, "take_profit": 1.0}, False),  # tp<=sl 保留
        # trail 档（pct 同域 + TP>SL 豁免）
        ({"sl_mode": "trail", "stop_loss": 0.05}, True),
        ({"sl_mode": "trail", "stop_loss": 2.0}, False),
        ({"sl_mode": "trail", "stop_loss": 0.05, "take_profit": 0.03}, True),  # 豁免
        # sl_mode 枚举
        ({"sl_mode": "trailing"}, False),
        # position_size / leverage
        ({"position_size": 0.10}, True),
        ({"position_size": 1.0}, True),
        ({"position_size": 0.05}, False),
        ({"position_size": 1.5}, False),
        ({"leverage": 1}, True),
        ({"leverage": 10}, True),
        ({"leverage": 11}, False),
        ({"leverage": 0}, False),
    ])
    def test_domain_validation(self, db, params, ok):
        if ok:
            r = _register(db, params, period=20)
            assert r["duplicated"] is False
        else:
            with pytest.raises(ResearchError) as ei:
                _register(db, params)
            assert ei.value.code == "RESEARCH_PARAM_INVALID"
            assert "field" in (ei.value.detail or {})

    def test_atr_fork_translation_anchor(self, db):
        """atr 档声明经翻译产物止损=入场价∓2.0×ATR 语义锚（DNA 携带倍数）。"""
        h = _register(db, {"sl_mode": "atr", "stop_loss": 2.0, "take_profit": 4.0})
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        rg = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])["risk_genes"]
        assert rg["sl_mode"] == "atr" and rg["stop_loss"] == 2.0

    def test_scaling_keys_domain_validation(self, db):
        """三键域校验（4.1 扩白名单后形态——原「未知键 422」断言已翻转
        退役）：exit_fraction/add_fraction∈(0,1]、max_exposure∈(0,生效杠杆]。"""
        _GROUP_ADD = [{"subject": {"indicator": "RSI",
                                    "params": {"period": 14},
                                    "field": "rsi"},
                        "action": "gt", "target": 50}]
        # 域校验拒（配对组在——拒因是值域不是配对）
        for bad in ({"exit_fraction": 1.5}, {"exit_fraction": 0},
                    {"add_fraction": 0}, {"max_exposure": 1.5}):
            with pytest.raises(ResearchError) as ei:
                register_hypothesis(
                    db, conditions={
                        "when_conditions": _cond(33)["when_conditions"],
                        "then_conditions": _cond(33)["then_conditions"],
                        "add_trigger_conditions": _GROUP_ADD,
                        "exit_partial_conditions": _GROUP_ADD,
                    },
                    statement="pair", data_ranges=_ranges(),
                    strategy_params=bad)
            assert ei.value.code == "RESEARCH_PARAM_INVALID"
            assert "field" in (ei.value.detail or {})
        # max_exposure 上界随声明 leverage 联动（1.5+leverage=2 过）
        r = register_hypothesis(
            db, conditions={
                "when_conditions": _cond(34)["when_conditions"],
                "then_conditions": _cond(34)["then_conditions"],
                "add_trigger_conditions": _GROUP_ADD,
            },
            statement="pair-ok", data_ranges=_ranges(),
            strategy_params={"max_exposure": 1.5, "leverage": 2,
                             "add_fraction": 0.1})
        assert r["duplicated"] is False

    def test_max_exposure_hint_names_leverage_link(self, db):
        """max_exposure 超生效杠杆上界（未声明 leverage=默认 1）的 detail
        附指引：声明更大上限须同时声明 leverage（错误即文档——现状锚）。"""
        _GROUP_ADD = [{"subject": {"indicator": "RSI",
                                    "params": {"period": 14},
                                    "field": "rsi"},
                        "action": "gt", "target": 50}]
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions={
                    "when_conditions": _cond(31)["when_conditions"],
                    "then_conditions": _cond(31)["then_conditions"],
                    "add_trigger_conditions": _GROUP_ADD,
                },
                statement="mx", data_ranges=_ranges(),
                strategy_params={"max_exposure": 1.5, "add_fraction": 0.1})
        d = ei.value.detail
        assert d["field"] == "max_exposure"
        assert d["legal_range"] == [0, 1]  # 上界=未声明 leverage 的默认 1
        assert "leverage" in d["hint"], "detail 须附 leverage 约束指引"

    def test_scaling_pairing_rules(self, db):
        """组间配对四规则：比例无宿主组拒/抑制无触发组拒/空列表拒/
        有组无值归一化字面化。"""
        base_cond = {"when_conditions": _cond(35)["when_conditions"],
                     "then_conditions": _cond(35)["then_conditions"]}
        # exit_fraction 无宿主组 → 422
        with pytest.raises(ResearchError):
            _register(db, {"exit_fraction": 0.5}, period=35)
        # add_fraction 无宿主组 → 422
        with pytest.raises(ResearchError):
            _register(db, {"add_fraction": 0.1}, period=35)
        # add_suppress 组无 add_trigger 组 → 422
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=dict(
                    base_cond,
                    add_suppress_conditions=[{"subject": "close",
                                              "action": "gt", "target": 0}]),
                statement="sup", data_ranges=_ranges())
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        # 组显式空列表 → 422
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=dict(base_cond, add_trigger_conditions=[]),
                statement="empty-group", data_ranges=_ranges())
        assert "非空列表" in ei.value.message
        # 组条目带 timeframe（unblock-research-validation Q1 翻转）：
        # add_trigger 放行（跟随 when 侧）；exit_partial/add_suppress 拒
        ok = register_hypothesis(
            db, conditions=dict(
                base_cond,
                add_trigger_conditions=[{
                    "subject": "close", "action": "gt", "target": 0,
                    "timeframe": "1d"}]),
            statement="tf", data_ranges=_ranges())
        assert ok["hypothesis_id"], "加仓触发组带 tf 登记放行"
        for group_key in ("exit_partial_conditions", "add_suppress_conditions"):
            with pytest.raises(ResearchError) as ei:
                register_hypothesis(
                    db, conditions=dict(
                        base_cond,
                        add_trigger_conditions=[{
                            "subject": "close", "action": "gt", "target": 0}],
                        **{group_key: [{
                            "subject": "close", "action": "gt", "target": 0,
                            "timeframe": "1d"}]}),
                    statement=f"tf-{group_key}", data_ranges=_ranges())
            assert ei.value.code == "TIMEFRAME_ON_THEN", \
                f"{group_key} 带 tf 应拒"

    def test_group_without_value_normalized(self, db):
        """有组无值→归一化字面化：组存在而未声明比例，落库声明块含
        字面缺省（0.5/0.1/1.0——幂等与翻译比对按字面值）。"""
        group = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                              "field": "rsi"},
                  "action": "gt", "target": 50}]
        r = register_hypothesis(
            db, conditions={
                "when_conditions": _cond(36)["when_conditions"],
                "then_conditions": _cond(36)["then_conditions"],
                "exit_partial_conditions": group,
                "add_trigger_conditions": group,
            },
            statement="norm", data_ranges=_ranges())
        assert r["duplicated"] is False
        row = get_hypothesis(db, r["hypothesis_id"])
        params = json.loads(row["strategy_params"])
        assert params == {"exit_fraction": 0.5, "add_fraction": 0.1,
                          "max_exposure": 1.0}

    def test_group_keys_participate_identity(self, db):
        """组键参与身份指纹：同条件集一个含组一个不含 → 两个独立假设。"""
        group = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                              "field": "rsi"},
                  "action": "gt", "target": 50}]
        base_cond = {"when_conditions": _cond(37)["when_conditions"],
                     "then_conditions": _cond(37)["then_conditions"]}
        r1 = register_hypothesis(db, conditions=dict(base_cond),
                                 statement="a", data_ranges=_ranges())
        r2 = register_hypothesis(
            db, conditions=dict(base_cond, exit_partial_conditions=group),
            statement="b", data_ranges=_ranges())
        assert r1["hypothesis_id"] != r2["hypothesis_id"]

    def test_error_detail_shape(self, db):
        """错误 detail 含 field 与 legal_range（错误即文档）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, {"leverage": 50})
        d = ei.value.detail
        assert d["field"] == "leverage" and d["legal_range"] == [1, 10]


# ── B2：幂等四分支 + 显式空对象三条 ────────────────────────────────


class TestIdempotencyFourBranches:
    def test_branch_missing_key_duplicated(self, db):
        """①键缺失：不参与比对 duplicated 不拒（旧调用方零适配）+摘要。"""
        _register(db, {"stop_loss": 0.05})
        r = _register(db)  # 键缺失
        assert r["duplicated"] is True
        assert r["existing_strategy_params"] == {"stop_loss": 0.05}

    def test_branch_equal_duplicated(self, db):
        """③带键全等（键序无关）：duplicated。"""
        _register(db, {"stop_loss": 0.05, "leverage": 2})
        r = _register(db, {"leverage": 2, "stop_loss": 0.05})
        assert r["duplicated"] is True

    def test_branch_unequal_rejected(self, db):
        """③带键不等：422 防静默丢弃，detail 附既有摘要与变体路径。"""
        _register(db, {"stop_loss": 0.05})
        with pytest.raises(ResearchError) as ei:
            _register(db, {"stop_loss": 0.10})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        d = ei.value.detail
        assert d["existing_strategy_params"] == {"stop_loss": 0.05}
        assert "slow_path" in d

    def test_empty_object_three_anchors(self, db):
        """②显式空对象三条：{} vs NULL→duplicated / {} vs 非空→422 / 键缺失不拒。"""
        _register(db)  # 既有 NULL 声明
        r = _register(db, {})  # {} vs NULL → 全等 duplicated
        assert r["duplicated"] is True
        assert r["existing_strategy_params"] == {}

        r2 = _register(db)  # 键缺失 vs NULL → duplicated 不拒
        assert r2["duplicated"] is True

        _register(db, {"stop_loss": 0.05}, period=21)  # 既有非空声明（新身份）
        with pytest.raises(ResearchError):
            _register(db, {}, period=21)  # {} vs 非空 → 422

    def test_declaration_not_in_identity(self, db):
        """声明块不进 conditions_hash（身份指纹零变化）：同条件集异声明
        不产生第二假设——首个 active 在，第二个带异声明被 422（非 duplicated
        亦非新实体）。"""
        _register(db, {"stop_loss": 0.05})
        with pytest.raises(ResearchError):
            _register(db, {"stop_loss": 0.10})


# ── 翻译映射与缺省回归 ─────────────────────────────────────────────


class TestTranslationMapping:
    def test_declared_to_dna_field_by_field(self, db):
        h = _register(db, {"stop_loss": 0.08, "sl_mode": "trail",
                           "position_size": 0.5, "leverage": 3})
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["risk_genes"] == "declared"
        rg = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])["risk_genes"]
        assert rg["stop_loss"] == 0.08
        assert rg["sl_mode"] == "trail"
        assert rg["position_size"] == 0.5
        assert rg["leverage"] == 3
        # 未声明键走平台默认
        assert rg["take_profit"] is None
        assert rg["direction"] == "long"
        assert rg["atr_period"] == 14

    def test_default_path_regression(self, db):
        """缺省路径回归：无声明登记+翻译与现状逐位一致（platform_default）。"""
        h = _register(db)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["duplicated"] is False and t["same_dna_existing"] is False
        assert t["risk_genes"] == "platform_default"
        rg = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])["risk_genes"]
        assert rg == RiskGenes().to_dict()  # 逐字段与平台默认一致

    def test_retranslate_idempotent_origin_kept(self, db):
        h = _register(db, {"stop_loss": 0.05})
        translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        t2 = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t2["duplicated"] is True and t2["risk_genes"] == "declared"


# ── 变体端到端 + 分流两线 ──────────────────────────────────────────


def _save_manual(db: Path, strategy_id: str, genes, **risk):
    dna = StrategyDNA(
        signal_genes=genes, logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(),
        risk_genes=RiskGenes(**risk))
    save_strategy(db, strategy_id=strategy_id, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name=strategy_id,
                  source="manual")


def _genes(period: int, lo: int, hi: int):
    return [
        SignalGene("RSI", {"period": period}, SignalRole.ENTRY_TRIGGER,
                   condition={"type": "lt", "threshold": lo}),
        SignalGene("RSI", {"period": period}, SignalRole.EXIT_TRIGGER,
                   condition={"type": "gt", "threshold": hi}),
    ]


class TestVariantEndToEnd:
    def test_variant_after_terminal(self, db):
        """登记带参→翻译→终态→同条件集异参重登记→翻译产新实体。"""
        from core.analysis.hypothesis_service import transition_hypothesis

        h1 = _register(db, {"stop_loss": 0.05}, statement="v1")
        t1 = translate_hypothesis(db, hypothesis_id=h1["hypothesis_id"])
        rg1 = json.loads(get_strategy(db, t1["strategy_id"])["dna_json"])["risk_genes"]
        assert rg1["stop_loss"] == 0.05

        # 终态（abandoned 免证据）
        transition_hypothesis(db, h1["hypothesis_id"],
                              target_status="abandoned", evidence_ref="variant path")
        # 同条件集+同区间集+异声明重登记 → 新假设实体（active 唯一索引只约束 active）
        h2 = _register(db, {"stop_loss": 0.10}, statement="v2")
        assert h2["hypothesis_id"] != h1["hypothesis_id"]
        t2 = translate_hypothesis(db, hypothesis_id=h2["hypothesis_id"])
        assert t2["strategy_id"] != t1["strategy_id"]
        rg2 = json.loads(get_strategy(db, t2["strategy_id"])["dna_json"])["risk_genes"]
        assert rg2["stop_loss"] == 0.10  # 同签名异参数并存且不误命中


class TestDiversionTwoLines:
    def test_line1_signature_path(self, db):
        """线 1：lev/dir 异→签名不同→建新实体（方案 A 证明：签名含 lev/dir）。"""
        h = _register(db, period=50, lo=20, hi=80)  # 无声明（lev=1）
        # 手工策略同信号基因但 leverage=5 → gene_signature 不同（lev 进签名）
        _save_manual(db, "st_l1", _genes(50, 20, 80), leverage=5)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["same_dna_existing"] is False  # 签名不同不命中
        # 反向锚：同签名同参数（lev=1 默认）命中
        _save_manual(db, "st_l1b", _genes(50, 20, 80))
        h2 = _register(db, period=46, lo=20, hi=80, statement="s2")
        # h2 与 h 同条件集会 duplicated——用新 period 区分身份
        h3 = _register(db, period=46, lo=20, hi=80, statement="s3")
        _save_manual(db, "st_l1c", _genes(46, 20, 80))
        t3 = translate_hypothesis(db, hypothesis_id=h3["hypothesis_id"])
        assert t3["same_dna_existing"] is True and t3["strategy_id"] == "st_l1c"

    def test_line2_d2_nine_fields(self, db):
        """线 2：签名同→九字段 D2 兜底——任一异建新（含 atr_period 异值锚）。"""
        h = _register(db, period=47, lo=25, hi=75)
        # 同签名 stop_loss 异 → 不分流建新
        _save_manual(db, "st_d2a", _genes(47, 25, 75), stop_loss=0.10)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["same_dna_existing"] is False and t["duplicated"] is False
        # atr_period 异值锚：库内 21 vs 翻译产物 14 → 建新（防语义偷换）
        _save_manual(db, "st_d2b", _genes(48, 25, 75), atr_period=21)
        h2 = _register(db, period=48, lo=25, hi=75, statement="s2")
        t2 = translate_hypothesis(db, hypothesis_id=h2["hypothesis_id"])
        assert t2["same_dna_existing"] is False and t2["duplicated"] is False
        # 全等反向锚：九字段全等 → 分流命中
        _save_manual(db, "st_d2c", _genes(49, 25, 75))
        h3 = _register(db, period=49, lo=25, hi=75, statement="s3")
        t3 = translate_hypothesis(db, hypothesis_id=h3["hypothesis_id"])
        assert t3["same_dna_existing"] is True and t3["strategy_id"] == "st_d2c"

    def test_legacy_dna_continuity(self, db):
        """连续性锚：存量 dna_json 无新键读 None——与新产物比对双方缺省连续。"""
        # 手工插无 exit_fraction 键的存量形 dna（risk_genes 无新键）
        h = _register(db, period=45, lo=25, hi=75)
        manual = {
            "strategy_id": "st_legacy", "generation": 0, "parent_ids": [],
            "mutation_ops": [],
            "signal_genes": [g.to_dict() for g in _genes(45, 25, 75)],
            "logic_genes": LogicGenes().to_dict(),
            "execution_genes": ExecutionGenes().to_dict(),
            "risk_genes": RiskGenes().to_dict(),
            "cross_layer_logic": "AND", "mtf_mode": None,
            "confluence_threshold": 0.3, "proximity_mult": 1.5,
            "layers": None,
        }
        save_strategy(db, strategy_id="st_legacy", dna_json=json.dumps(manual),
                      symbol="BTCUSDT", timeframe="4h", name="legacy",
                      source="manual")
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        # 存量双方九字段全等（新键双方均缺省）→ 分流命中现状语义不变
        assert t["same_dna_existing"] is True and t["strategy_id"] == "st_legacy"


# ── API 传输层（B2：{} 保全 + 全链到达 service + existing 透传）────


class TestApiTransport:
    def test_api_full_chain_reaches_service(self, tmp_data_dir, tmp_path):
        """声明块经 POST /api/research/hypotheses 全链到达 service 校验点
        （防 API 层静默丢键断裂）。"""
        from fastapi.testclient import TestClient

        from api.app import create_app

        db = tmp_path / "api.db"
        init_db_ext(db)
        ensure_research_tables(db)
        app = create_app(db_path=db, data_dir=tmp_data_dir)
        with TestClient(app) as client:
            body = {
                "conditions": _cond(),
                "statement": "api chain",
                "data_ranges": _ranges(),
                "strategy_params": {"stop_loss": 0.05, "sl_mode": "pct"},
            }
            r = client.post("/api/research/hypotheses", json=body)
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["duplicated"] is False
            # 列写入经 API 全链到达（service 校验点已过、列已存）
            row = get_hypothesis(db, out["hypothesis_id"])
            assert json.loads(row["strategy_params"]) == {
                "stop_loss": 0.05, "sl_mode": "pct"}

            # API 层坏值 422（全链校验拒绝）
            bad = dict(body, strategy_params={"leverage": 50})
            assert client.post("/api/research/hypotheses",
                               json=bad).status_code == 422

    def test_api_empty_object_preserved(self, tmp_data_dir, tmp_path):
        """B2 {} 传输保全锚：JSON 体 strategy_params={} 经全链到达 service
        仍是空 dict 而非 None（否则 {} 语义坍缩为键缺失，三锚失效）。"""
        from fastapi.testclient import TestClient

        from api.app import create_app

        db = tmp_path / "api2.db"
        init_db_ext(db)
        ensure_research_tables(db)
        app = create_app(db_path=db, data_dir=tmp_data_dir)
        with TestClient(app) as client:
            base = {"conditions": _cond(), "statement": "empty obj",
                    "data_ranges": _ranges()}
            r1 = client.post("/api/research/hypotheses", json=base)
            assert r1.status_code == 200
            # 显式 {} 登记同身份（既有 NULL）→ duplicated（{}≠键缺失语义成立的前提）
            r2 = client.post("/api/research/hypotheses",
                             json=dict(base, strategy_params={}))
            assert r2.status_code == 200
            out = r2.json()
            assert out["duplicated"] is True
            assert out["existing_strategy_params"] == {}

    def test_api_existing_summary_passthrough(self, tmp_data_dir, tmp_path):
        """existing_strategy_params 经 response_model 透传（MCP 面前置）。"""
        from fastapi.testclient import TestClient

        from api.app import create_app

        db = tmp_path / "api3.db"
        init_db_ext(db)
        ensure_research_tables(db)
        app = create_app(db_path=db, data_dir=tmp_data_dir)
        with TestClient(app) as client:
            base = {"conditions": _cond(), "statement": "summary",
                    "data_ranges": _ranges()}
            client.post("/api/research/hypotheses",
                        json=dict(base, strategy_params={"stop_loss": 0.05}))
            r = client.post("/api/research/hypotheses", json=base)  # 键缺失
            assert r.status_code == 200
            assert r.json()["existing_strategy_params"] == {"stop_loss": 0.05}
