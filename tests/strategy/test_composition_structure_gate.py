"""composition 结构判据红锚（dna-strong-typing-and-disclosure-hygiene 件②
H4-a，design 2.2 / specs strategy-expression「composition 结构判据」）。

契约出处：validate_dna 新增 composition 结构判据块（沿 signal_sources
结构判据 R-1b 同族形制：None 合法退化、errors 收集不 raise、逐条至多
一条消息、点名实际类型/值）。判据集五条：
- composition is None → 合法退化（存量语义，零迁移——纯 rule 策略
  全量不受影响）
- 非 dict → ``composition must be a dict, got {类型名}``
- mode 显式值 ∉ COMPOSITION_MODES → ``composition: mode must be one of
  {COMPOSITION_MODES}, got {值!r}``（缺省 weighted_vote 合法，与组装
  求值缺省一致）
- mode=="weighted_vote"（含缺省）且 weights 在场且非 dict →
  ``composition.weights must be a dict, got {类型名}``
- threshold/weights 各值/operator/role 不判（执行侧既有防线承接——
  求值点 _to_float_or_reject 与 _SOURCE_COMPARE_OPS 已收口）

红锚语义：拒绝侧（R1/R2/R3）在判据落地前为红——现状坏 composition
静默放行（is_valid=True）→ 201 落库 → 回测期才 422
RESEARCH_COMPOSITION_INVALID（延迟拒绝带）；判据落地后前置为创建/更新
即 422 DNA_VALIDATION_FAILED。护栏（G 组）双态绿（判据落地前后都
绿，防误伤存量合法形态）。

骨架纪律：合法骨架（ENTRY+EXIT 基因齐全+缺省 risk_genes）保证
entry/exit/值域判据全绿，畸形只放 composition——单错误面定位，
拒绝归因不串位。

值域分工（spec「组合参数值域显式拒绝」分工句）：结构形制（类型形
+mode 枚举）归 validate_dna 前置；数值键值域（operator 枚举/
threshold/weights 数值化）维持组装求值单点既有权威——R3 只判
weights 键的类型形（dict 与否），不判其内部值。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (
    RiskGenes, SignalGene, SignalRole, StrategyDNA,
)
from core.strategy.validator import validate_dna


def _exit_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", {"type": "lt", "threshold": 35})


def _skeleton_dna(composition) -> StrategyDNA:
    """合法骨架（entry/exit 齐全+缺省值域）——畸形只放 composition。"""
    return StrategyDNA(
        signal_genes=[_entry_gene(), _exit_gene()],
        composition=composition,
        risk_genes=RiskGenes(),
    )


# ===========================================================================
# 拒绝侧（判据落地前红：现状静默放行）
# ===========================================================================

class TestCompositionStructureReject:
    """坏 composition 结构形态 → is_valid=False+消息点名实际类型/值。"""

    @pytest.mark.parametrize(
        ("bad", "type_name"),
        [
            pytest.param(["weighted_vote"], "list", id="list 形态"),
            pytest.param("weighted_vote", "str", id="str 形态"),
            pytest.param(0.5, "float", id="数值形态"),
        ],
    )
    def test_non_dict_rejected(self, bad, type_name):
        """非 dict 形态 → 拒绝+D-5 点名实际类型名。"""
        result = validate_dna(_skeleton_dna(bad))
        assert not result.is_valid, (
            f"composition 非 dict 形态 {bad!r} 被静默放行（判据未生效）："
            f"errors={result.errors}")
        named = [e for e in result.errors
                 if e == f"composition must be a dict, got {type_name}"]
        assert named, f"消息未按模板点名类型：errors={result.errors}"

    @pytest.mark.parametrize(
        ("mode", "rendered"),
        [
            pytest.param("vote", "'vote'", id="白名单外 vote"),
            pytest.param("WeightedVote", "'WeightedVote'", id="大小写变体"),
            pytest.param("", "''", id="空串"),
        ],
    )
    def test_bad_mode_rejected(self, mode, rendered):
        """白名单外 mode → 拒绝+值 repr 渲染点名。"""
        result = validate_dna(_skeleton_dna({"mode": mode}))
        assert not result.is_valid, (
            f"白名单外 mode={mode!r} 被静默放行：errors={result.errors}")
        expected = (f"composition: mode must be one of "
                    f"('weighted_vote', 'gate', 'logic'), got {rendered}")
        assert expected in result.errors, (
            f"消息未按模板渲染：errors={result.errors}")

    @pytest.mark.parametrize(
        ("comp", "type_name"),
        [
            pytest.param({"weights": [0.5, 0.5]}, "list",
                         id="缺省 mode+weights 为 list"),
            pytest.param({"mode": "weighted_vote", "weights": "half"},
                         "str", id="显式 weighted_vote+weights 为 str"),
            pytest.param({"mode": "gate", "weights": 0.5}, "float",
                         id="非 weighted_vote 模式 weights 不判（护栏放行侧）"),
        ],
    )
    def test_weights_non_dict(self, comp, type_name):
        """weighted_vote 模式（含缺省）weights 非 dict → 拒绝点名类型。

        第三参数化为护栏负对照：gate 模式下 weights 键不判（值域
        分工——weights 仅在 weighted_vote 求值路径被消费）。
        """
        if comp.get("mode") == "gate":
            result = validate_dna(_skeleton_dna(comp))
            assert result.is_valid, (
                f"gate 模式 weights 形态不应判：errors={result.errors}")
            return
        result = validate_dna(_skeleton_dna(comp))
        assert not result.is_valid, (
            f"weights 非 dict 被静默放行：errors={result.errors}")
        expected = (f"composition.weights must be a dict, got {type_name}")
        assert expected in result.errors


# ===========================================================================
# 护栏侧（双态绿：判据落地前后都绿——防误伤存量合法形态）
# ===========================================================================

class TestCompositionLegalGuard:
    """None 退化与合法三模式零变化（存量语义逐位不变）。"""

    def test_none_degenerate_passes(self):
        """composition=None 合法退化（纯 rule 策略全量不受影响）。"""
        assert validate_dna(_skeleton_dna(None)).is_valid

    def test_legal_three_modes_pass(self):
        """合法三模式（weighted_vote 含法 weights/threshold、gate、
        logic 含法 op）+ 缺 mode 键（缺省 weighted_vote）全放行。"""
        legal = [
            {"mode": "weighted_vote",
             "weights": {"fx_a": 0.6, "fx_b": 0.4}, "threshold": 0.5},
            {"mode": "gate"},
            {"mode": "logic", "op": "AND"},
            {"weights": {"fx_a": 1.0}},  # 缺 mode 键=缺省 weighted_vote
            {},  # 空 dict：无键全缺省
        ]
        for comp in legal:
            result = validate_dna(_skeleton_dna(comp))
            assert result.is_valid, (
                f"合法形态 {comp!r} 被误拒：errors={result.errors}")

    def test_weights_values_not_judged(self):
        """值域分工护栏：weights 各值可数值化性不判（执行侧
        _to_float_or_reject 既有防线承接——validator 只判类型形）。"""
        result = validate_dna(_skeleton_dna(
            {"weights": {"fx_a": "not-a-number"}}))
        assert result.is_valid, (
            f"weights 内部值域不应在本判据拒（求值单点权威）："
            f"errors={result.errors}")


class TestDefaultModeSingleSource:
    """缺省 mode 单源锚（CARD-07）：DEFAULT_COMPOSITION_MODE 为
    validator 模块级常量，结构校验与求值两侧 get 缺省同源引用——
    双写字面量漂移（缺省值两处失同步）形态的防复发红界。"""

    def test_constant_membership(self):
        """常量成员关系：缺省值属白名单（缺省合法的判据前提）。"""
        from core.strategy.validator import (
            COMPOSITION_MODES, DEFAULT_COMPOSITION_MODE)
        assert DEFAULT_COMPOSITION_MODE == "weighted_vote"
        assert DEFAULT_COMPOSITION_MODE in COMPOSITION_MODES

    def test_assembly_side_imports_same_constant(self):
        """求值侧同源引用：signal_assembly 模块引用的是同一常量对象
        （import 链在场——若改回字面量双写，本断言不感知但缺省行为
        锚与下条源码清零断言兜住）。"""
        from core.analysis import signal_assembly
        from core.strategy.validator import DEFAULT_COMPOSITION_MODE
        assert signal_assembly.DEFAULT_COMPOSITION_MODE \
            is DEFAULT_COMPOSITION_MODE

    def test_no_default_literal_duplication(self):
        """缺省 get 双写清零：两文件的 get("mode", ...) 第二参均为
        常量符号（源码文本不再出现 get("mode", "weighted_vote") 形）。"""
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        for rel in ("core/strategy/validator.py",
                    "core/analysis/signal_assembly.py"):
            text = (root / rel).read_text(encoding="utf-8")
            assert 'get("mode", "weighted_vote")' not in text, (
                f"{rel} 缺省 get 回退字面量双写（CARD-07 单源回潮）")


# ===========================================================================
# 入口层（REST 通道——前置 422 落库前；判据落地前红：现状 201 落库）
# ===========================================================================


class TestStrategyCreateCompositionGate:
    """POST /api/strategies 带 composition——坏 mode 落库前 422
    DNA_VALIDATION_FAILED（validate_dna 判据经 crud.py 通道消费）。"""

    @pytest.mark.integration
    def test_bad_mode_422_before_persist(self, api_client):
        body = {
            "name": "comp-gate",
            "dna": _skeleton_dna({"mode": "vote"}).to_dict(),
            "symbol": "BTCUSDT", "timeframe": "4h",
        }
        r = api_client.post("/api/strategies", json=body)
        assert r.status_code == 422, (
            f"坏 mode 现状 {r.status_code} 落库（延迟到回测期才拒）"
            f"——判据未前置：{r.text[:300]}")
        detail = r.json()["detail"]
        assert detail["code"] == "DNA_VALIDATION_FAILED"
        assert any("mode must be one of" in e for e in detail["errors"])

    @pytest.mark.integration
    def test_legal_composition_creates(self, api_client):
        """合法 composition 创建 201（防线不误伤正常创建）。"""
        body = {
            "name": "comp-ok",
            "dna": _skeleton_dna(
                {"mode": "weighted_vote",
                 "weights": {"fx_a": 0.6}, "threshold": 0.5}).to_dict(),
            "symbol": "BTCUSDT", "timeframe": "4h",
        }
        r = api_client.post("/api/strategies", json=body)
        assert r.status_code == 201, r.text


class TestCompositionGateOtherEntries:
    """AC-2「经创建/更新/进化入口」三入口的另两入口：更新（PUT）与
    进化（initial_dna）——判据经 validate_dna 单点生效，创建入口已锚
    （TestStrategyCreateCompositionGate），此处补齐契约点名的其余
    两入口（crud.py:304 / evolution.py:241 同款 422 通道）。"""

    @pytest.mark.integration
    def test_bad_mode_422_on_update(self, api_client):
        """PUT /api/strategies/{id} 换坏 composition DNA → 422
        DNA_VALIDATION_FAILED（update 通道同款早拒绝——经 POST 建合法
        策略再 PUT 换坏形不得绕过）。"""
        create = api_client.post("/api/strategies", json={
            "name": "comp-put",
            "dna": _skeleton_dna(None).to_dict(),
            "symbol": "BTCUSDT", "timeframe": "4h",
        })
        assert create.status_code == 201, create.text
        sid = create.json()["strategy_id"]
        r = api_client.put(f"/api/strategies/{sid}", json={
            "dna": _skeleton_dna({"mode": "vote"}).to_dict()})
        assert r.status_code == 422, (
            f"更新入口坏 mode 现状 {r.status_code}——判据未在 update "
            f"通道生效：{r.text[:300]}")
        detail = r.json()["detail"]
        assert detail["code"] == "DNA_VALIDATION_FAILED"
        assert any("mode must be one of" in e for e in detail["errors"])

    @pytest.mark.integration
    def test_bad_mode_422_on_evolution_entry(self, api_client):
        """POST /api/evolution/tasks initial_dna 坏 composition → 422
        DNA_VALIDATION_FAILED+任务无新行（坏祖先不落库——落库后变异
        扩散整代种群）。"""
        import sqlite3

        db = api_client.app.state.db_path

        def _task_count() -> int:
            conn = sqlite3.connect(db)
            try:
                return conn.execute(
                    "SELECT COUNT(*) FROM evolution_task").fetchone()[0]
            finally:
                conn.close()

        before = _task_count()
        r = api_client.post("/api/evolution/tasks", json={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "target_score": 80.0, "score_template": "explorer",
            "population_size": 15, "max_generations": 200,
            "initial_dna": _skeleton_dna({"mode": "vote"}).to_dict(),
        })
        assert r.status_code == 422, (
            f"进化入口坏 composition 现状 {r.status_code}——判据未在 "
            f"initial_dna 通道生效：{r.text[:300]}")
        detail = r.json()["detail"]
        assert detail["code"] == "DNA_VALIDATION_FAILED"
        assert any("mode must be one of" in e for e in detail["errors"])
        assert _task_count() == before, "拒绝后任务表出现新行（坏祖先落库）"
