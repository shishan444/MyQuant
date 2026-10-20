"""signal_sources 结构校验——写入面与读库六入口红锚
（signal-sources-structure-gate 0.2）。

契约出处（design 卡 V 生效面矩阵+卡 G R-8 基准，2026-10-19 方案
放行）：validate_dna 新判据经全部消费入口统一生效——
- AC-5（crud 写入面）：create/update dict 形态畸形 → 422
  DNA_VALIDATION_FAILED+库内无新行/更新逐字节不变；非 dict 条目被
  Pydantic List[Dict] 前拦（A-3 边界——拦截层分工，不冒充结构码）；
  修正后恢复 201（拒绝可恢复）。
- AC-6（读库六入口）：三类畸形（拼错 type/非 dict 条目/缺 ref）经
  backtest/compare/verify/batch 四读库路径 → 422
  DNA_VALIDATION_FAILED（现状：拼错静默放行/非 dict 放行或异态/
  缺 ref 报 PLUGIN_SOURCES_UNSUPPORTED——错误码错位）；内联分支经
  Pydantic 收窄只剩 dict 形态两类；live 入口 dna_json 直传四形态
  （含顶层非 list——from_json 原样透传通道）全可达；D-3 迁移格
  （缺 ref 归位 DNA_VALIDATION_FAILED，反向断言防误绿）；判定顺序
  锚（结构坏+合法插件源→先报结构码）；对照（合法纯插件仍
  PLUGIN_SOURCES_UNSUPPORTED——判据不误伤能力边界）。

红锚语义：拒绝侧在卡 V 落地前为红（500/放行/错误码错位均以
「非 422 DNA_VALIDATION_FAILED」表现为红——TestClient
raise_server_exceptions 默认下 500 直接报 error 属合法红态）。

写入通道纪律：库内行经 save_strategy 原语直写（repository 层无
DNA 校验——读库侧防线是被测面，写入走直通道沿 T3 威胁模型）。
"""
from __future__ import annotations

import json as _json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient  # noqa: F401（fixture 类型标注用）

from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA
from core.strategy.validator import has_plugin_sources, validate_dna
from tests.helpers.data_factory import make_dna

ROUTES = ("backtest", "compare", "verify", "batch")

# 三类畸形（直改库/live dna_json 通道构造集——save_strategy 与
# from_json 均原样透传，三类全可达）
BAD_SOURCES = {
    "拼错type": [{"type": "fomula"}],
    "非dict条目": [{"type": "rule"}, "x"],
    "缺ref": [{"type": "formula", "threshold": 0.5}],
}

# crud 写入通道构造集（A-3 收窄）：请求体经 Pydantic List[Dict] 时
# 非 dict 条目被前拦为 FastAPI 校验明细形制（不达结构码）——结构码
# 断言面只覆盖 dict 形态畸形两类
BAD_SOURCES_DICT = {
    "拼错type": BAD_SOURCES["拼错type"],
    "缺ref": BAD_SOURCES["缺ref"],
}


def _exit_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", {"type": "lt", "threshold": 35})


def _plugin_source() -> dict:
    src = {"type": "formula", "ref": "fx_fx_trend_pullback",
           "threshold": 0.5, "operator": ">"}
    return src


def _dna_with_sources(sources) -> StrategyDNA:
    """合法骨架（entry/exit 齐全）+指定 signal_sources。"""
    return StrategyDNA(
        signal_genes=[_entry_gene(), _exit_gene()],
        signal_sources=sources,
        risk_genes=RiskGenes(),
    )


def _persist(db_path: Path, sid: str, dna_json: str) -> None:
    """策略行直写测试库（save_strategy 无 DNA 校验——读库防线是被测面）。"""
    from core.persistence.repository.strategy import save_strategy

    save_strategy(
        db_path, strategy_id=sid, dna_json=dna_json,
        symbol="BTCUSDT", timeframe="4h", name=sid,
        gene_signature=f"sig-{sid}")


def _post_route(client: TestClient, route: str, sid: str):
    """四读库路径同语义请求（strategy_id 消费面）。"""
    if route == "backtest":
        return client.post("/api/strategies/backtest", json={
            "strategy_id": sid, "dataset_id": "BTCUSDT_4h"})
    if route == "compare":
        return client.post("/api/strategies/compare", json={
            "strategy_ids": [sid], "dataset_id": "BTCUSDT_4h"})
    if route == "verify":
        return client.post("/api/strategies/verify", json={
            "strategy_ids": [sid],
            "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}]})
    if route == "batch":
        return client.post("/api/strategies/batch-backtest/stream", json={
            "strategy_ids": [sid],
            "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}]})
    raise ValueError(route)


def _strategy_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM strategy").fetchone()[0]
    finally:
        conn.close()


# ===========================================================================
# AC-5：crud 写入面（create/update dict 形态畸形——提交时红：现状放行）
# ===========================================================================

class TestCrudCreateRejectsBadSources:
    """create 通道：dict 形态畸形 → 422+库内无新行。"""

    @pytest.mark.parametrize(
        "label", BAD_SOURCES_DICT, ids=list(BAD_SOURCES_DICT))
    def test_create_bad_sources_422_no_row(self, api_client, label):
        """dict 形态畸形经 POST /api/strategies → 422
        DNA_VALIDATION_FAILED+errors 点名+库内计数不变（坏 DNA 不落库；
        非 dict 条目被 Pydantic 前拦，见 TestCreatePydanticBoundary）。"""
        db = api_client.app.state.db_path
        before = _strategy_count(db)

        resp = api_client.post("/api/strategies", json={
            "name": f"bad-create-{label}",
            "dna": _json.loads(_dna_with_sources(BAD_SOURCES[label]).to_json()),
            "symbol": "BTCUSDT", "timeframe": "4h",
        })

        assert resp.status_code == 422, (
            f"[{label}] 坏 DNA 未被写入校验拒绝：{resp.status_code} "
            f"{resp.text[:300]}")
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", f"[{label}] {body}"
        assert any("signal_sources" in e for e in body["errors"]), (
            f"[{label}] errors 未点名 signal_sources：{body['errors']}")
        assert _strategy_count(db) == before, (
            f"[{label}] 拒绝后库内出现新行（坏 DNA 落库）")

    def test_create_recovers_after_fix(self, api_client):
        """恢复锚：畸形被拒后，同构造修正 signal_sources → 201——
        拒绝是可恢复的参数错误而非通道损坏。"""
        db = api_client.app.state.db_path
        bad = _json.loads(
            _dna_with_sources([{"type": "fomula"}]).to_json())
        resp = api_client.post("/api/strategies", json={
            "name": "recover-after-fix", "dna": bad,
            "symbol": "BTCUSDT", "timeframe": "4h"})
        assert resp.status_code == 422

        bad["signal_sources"] = [{"type": "formula", "ref": "fx_demo"}]
        resp = api_client.post("/api/strategies", json={
            "name": "recover-after-fix", "dna": bad,
            "symbol": "BTCUSDT", "timeframe": "4h"})
        assert resp.status_code == 201, resp.text


class TestUpdatePydanticBoundary:
    """A-3 边界对照（update 侧对称）：非 dict 条目被前拦。"""

    def test_nondict_entry_intercepted(self, api_client):
        """PUT 请求体 signal_sources 含非 dict 条目 → FastAPI 校验明细
        形制（非结构码——与 create 侧同效的拦截层分工）。"""
        db = api_client.app.state.db_path
        _persist(db, "s-gate-pyb", make_dna().to_json())
        dna = _json.loads(make_dna().to_json())
        dna["signal_sources"] = ["x"]

        resp = api_client.put("/api/strategies/s-gate-pyb",
                              json={"dna": dna})

        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        is_structured = isinstance(detail, dict) and \
            detail.get("code") == "DNA_VALIDATION_FAILED"
        assert not is_structured, detail


class TestCrudUpdateRejectsBadSources:
    """update 通道：active 行 PUT 换畸形 DNA → 422+库内逐字节不变。"""

    @pytest.mark.parametrize(
        "label", BAD_SOURCES_DICT, ids=list(BAD_SOURCES_DICT))
    def test_update_bad_sources_422_unchanged(self, api_client, label):
        """dict 形态畸形经 PUT → 422+库内 dna_json 逐字节不变（更新被
        整体拒绝，不落半截；非 dict 条目被 Pydantic 前拦见本类
        test_nondict_entry_intercepted）。"""
        db = api_client.app.state.db_path
        original_json = make_dna().to_json()
        _persist(db, "s-gate-update", original_json)

        resp = api_client.put("/api/strategies/s-gate-update", json={
            "dna": _json.loads(
                _dna_with_sources(BAD_SOURCES[label]).to_json())})

        assert resp.status_code == 422, (
            f"[{label}] 坏 DNA 未被更新校验拒绝：{resp.status_code} "
            f"{resp.text[:300]}")
        assert resp.json()["detail"]["code"] == "DNA_VALIDATION_FAILED"

        from core.persistence.repository.strategy import get_strategy
        row = get_strategy(db, "s-gate-update")
        assert row["dna_json"] == original_json, (
            f"[{label}] 拒绝后库内 dna_json 被改写")


class TestCreatePydanticBoundary:
    """A-3 边界对照：非 dict 条目被 Pydantic 前拦，不冒充结构码。"""

    def test_nondict_entry_intercepted_before_dna_code(self, api_client):
        """请求体 signal_sources 含非 dict 条目（"x"）→ 422 为 FastAPI
        校验明细形制（detail 非 dict/无 DNA_VALIDATION_FAILED code）——
        拦截层分工：传输层 Pydantic 管「条目是 dict」，结构码只接管
        dict 形态内的键值畸形（非产品意图受损的边界锚）。"""
        dna = _json.loads(make_dna().to_json())
        dna["signal_sources"] = ["x"]

        resp = api_client.post("/api/strategies", json={
            "name": "pydantic-boundary", "dna": dna,
            "symbol": "BTCUSDT", "timeframe": "4h"})

        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        is_structured = isinstance(detail, dict) and \
            detail.get("code") == "DNA_VALIDATION_FAILED"
        assert not is_structured, (
            f"非 dict 条目应被 Pydantic 前拦（FastAPI 明细形制），"
            f"不应表现为结构码：{detail}")


# ===========================================================================
# AC-6：读库四路径+内联+live 六入口（提交时红：放行/500/错误码错位）
# ===========================================================================

class TestStoredBadSourcesRejectedOnReadPaths:
    """三类畸形 × 四读库路径（12 用例）→ 422 DNA_VALIDATION_FAILED。"""

    @pytest.mark.parametrize("label", BAD_SOURCES, ids=list(BAD_SOURCES))
    @pytest.mark.parametrize("route", ROUTES)
    def test_bad_sources_row_rejected(self, api_client, route, label):
        """直改库畸形行经读库路径 → 422+结构码+errors 点名
        signal_sources（现状：拼错型静默放行、非 dict 型放行或异态、
        缺 ref 型报 PLUGIN_SOURCES_UNSUPPORTED——均非目标态，红）。"""
        db = api_client.app.state.db_path
        sid = f"s-bad-{label}"
        _persist(db, sid, _dna_with_sources(BAD_SOURCES[label]).to_json())

        resp = _post_route(api_client, route, sid)

        assert resp.status_code == 422, (
            f"[{route}/{label}] {resp.status_code} {resp.text[:300]}")
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", (
            f"[{route}/{label}] {body}")
        assert any("signal_sources" in e for e in body["errors"]), (
            f"[{route}/{label}] {body['errors']}")

    def test_missing_ref_not_plugin_code(self, api_client):
        """D-3 迁移格（错误码归位——本 change 唯一对外行为变化）：
        缺 ref 形态断言 code==DNA_VALIDATION_FAILED 且 **不等于**
        PLUGIN_SOURCES_UNSUPPORTED（反向断言防误绿——现状报 PLUGIN 码，
        只断言正向会被现状满足的「422+某码」假绿）。"""
        db = api_client.app.state.db_path
        _persist(db, "s-missing-ref",
                 _dna_with_sources(BAD_SOURCES["缺ref"]).to_json())

        resp = _post_route(api_client, "backtest", "s-missing-ref")

        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", body
        assert body["code"] != "PLUGIN_SOURCES_UNSUPPORTED", (
            f"缺 ref 形态仍报能力边界码（D-3 未归位）：{body}")

    def test_structural_bad_precedes_plugin_capability(self, api_client):
        """判定顺序锚：同 DNA 含结构坏条目（fomula）+合法插件源
        （formula 带 ref）→ 报结构码（validate_dna 先于
        has_plugin_sources——「策略坏」优先于「环境不支持」）。"""
        db = api_client.app.state.db_path
        _persist(db, "s-order", _dna_with_sources(
            [{"type": "fomula"}, _plugin_source()]).to_json())

        resp = _post_route(api_client, "backtest", "s-order")

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "DNA_VALIDATION_FAILED", (
            resp.text)

    @pytest.mark.parametrize("route", ROUTES)
    def test_legal_pure_plugin_still_plugin_code(self, api_client, route):
        """对照：合法纯插件策略（结构过+插件源在场）仍报
        PLUGIN_SOURCES_UNSUPPORTED——新判据不误伤能力边界判据
        （R-8 基准的负面锚）。"""
        db = api_client.app.state.db_path
        _persist(db, "s-legal-plugin",
                 _dna_with_sources([_plugin_source()]).to_json())

        resp = _post_route(api_client, route, "s-legal-plugin")

        assert resp.status_code == 422, f"[{route}] {resp.text}"
        body = resp.json()["detail"]
        assert body["code"] == "PLUGIN_SOURCES_UNSUPPORTED", (
            f"[{route}] {body}")


class TestInlineDnaBadSources:
    """内联直传入口（backtest dna 字段）：Pydantic 收窄后只剩 dict
    形态畸形两类可达。"""

    @pytest.mark.parametrize(
        "label", ["拼错type", "缺ref"], ids=["拼错type", "缺ref"])
    def test_inline_dict_form_malformed_422(self, api_client, label):
        """内联 dna 的 signal_sources 为 dict 形态畸形（拼错/缺 ref）
        → 422 DNA_VALIDATION_FAILED（现状放行——直传即跑）。"""
        dna = _json.loads(
            _dna_with_sources(BAD_SOURCES[label]).to_json())
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": dna, "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h"})
        assert resp.status_code == 422, (
            f"[{label}] {resp.status_code} {resp.text[:300]}")
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", f"[{label}] {body}"

    def test_inline_nondict_intercepted_by_pydantic(self, api_client):
        """内联 dna 的 signal_sources 非 dict 条目 → Pydantic 前拦
        （非结构码——A-3 边界在内联分支同效）。"""
        dna = _json.loads(make_dna().to_json())
        dna["signal_sources"] = ["x"]
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": dna, "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h"})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        is_structured = isinstance(detail, dict) and \
            detail.get("code") == "DNA_VALIDATION_FAILED"
        assert not is_structured, detail


class TestLiveEntryDnaJsonDirect:
    """live 入口（POST /api/live-trading/tasks）：dna_json 字符串直传
    ——不经 Pydantic List[Dict]，四形态全可达（含顶层非 list）。"""

    @pytest.mark.parametrize(
        ("label", "sources"),
        [
            pytest.param("拼错type", [{"type": "fomula"}], id="拼错type"),
            pytest.param("非dict条目", [{"type": "rule"}, "x"], id="非dict条目"),
            pytest.param("缺ref", [{"type": "formula"}], id="缺ref"),
            pytest.param("顶层非list", 5, id="顶层非list"),
        ],
    )
    def test_live_dna_json_malformed_422(self, api_client, label, sources):
        """四形态畸形经 dna_json 直传 → 422 DNA_VALIDATION_FAILED
        （现状：拼错/缺 ref 型行为错位〔缺 ref 报 PLUGIN 码、拼错型
        放行落库〕、非 dict/顶层非 list 型 500 或裸异常——from_json
        原样透传后无判据拦截）。"""
        dna = _dna_with_sources(sources)
        resp = api_client.post("/api/live-trading/tasks", json={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dna_json": dna.to_json(), "direction": "long"})

        assert resp.status_code == 422, (
            f"[{label}] {resp.status_code} {resp.text[:300]}")
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", f"[{label}] {body}"
        assert any("signal_sources" in e for e in body["errors"]), (
            f"[{label}] {body['errors']}")
