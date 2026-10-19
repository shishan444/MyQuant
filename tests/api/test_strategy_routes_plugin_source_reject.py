"""REST 回测四路径插件源显式拒绝测试（recognize-plugin-source-dna 任务 4.3）。

契约出处（design 卡 T4·第二问乙，2026-10-19 用户裁决生效）：REST
回测通道（引擎该通道未接插件信号集，结果将为零交易）在读库四路径
（backtest strategy_id 分支 / batch init / compare 循环 / verify init）
的 T3 二道校验（DNA_VALIDATION_FAILED）通过后、送引擎前，对插件源
在场（type∈{formula,model}——在场即拒，不分 role、含混合形态）的
策略显式 422 拒绝：code=PLUGIN_SOURCES_UNSUPPORTED+message 说明能力
边界与替代通道（含「插件信号源」「沙盒」短语）。静默零交易从行为面
消除。

断言从 design T4 契约推导（不从实现反推）：
- 主格：纯插件策略行经四路径 → 422+PLUGIN_SOURCES_UNSUPPORTED+
  message 含「插件信号源」与「沙盒」。
- 在场即拒覆盖面锚：混合形态（内置 ENTRY 基因+插件源）与库存两行
  混合形态（st_fa536d692525/st_7cd382d439fa，只读实测资产）经四路径
  同拒——防误收窄为「无 ENTRY 基因才拒」致两行漏网、防误放「混合
  可跑」致静默失真回潮（designer 意图确认：在场即拒）。
- 分码断言：同请求构造下损坏 DNA 行 → DNA_VALIDATION_FAILED（两码
  不同值——「策略坏」与「通道不支持」分道）。
- 对照：普通策略（无插件源）正常回测 200（判据零误伤面）。
- 判据单元面：has_plugin_sources 对无源/None/rule 型为 False，对
  formula/model 型（含 role:"exit"）为 True——「在场即真」的判据
  字面直证。

前端契约锚（⑤·只读核验，2026-10-19 grep 实读）：四路径前端消费全走
错误通道，无「200 假结果」依赖——web/src/services/strategies.ts:65
（backtest）/:73（compare）/:83（verify）均为 api.post（ky Promise，
非 2xx reject）；web/src/services/api.ts G16.0 统一错误契约把 422 转
ApiError("validation") throw；batch 前端（strategies.ts:125 起
streamBatchBacktest）走 streamSse 的 onError 事件通道。API 返回从
「假结果」变「真错误」无前端契约变更。

两层归属注记（沿红灯基线锚 0.1 注记）：库存两行在 validate_dna 层
预期 is_valid=True（结构合法，tests/strategy/test_plugin_source_
validation_baseline.py ⑤b 锚），在 REST 通道层预期 422
PLUGIN_SOURCES_UNSUPPORTED——同一行策略两个正交判定，并行不悖。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient  # noqa: F401（fixture 类型标注用）

from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA
from core.strategy.validator import has_plugin_sources
from tests.helpers.data_factory import make_dna

# 库存验证资产定位（判定并集纪律外的库文件只读保护——mode=ro）
_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUANT_DB = _REPO_ROOT / "data" / "quant.db"

# 库存两行混合形态策略 id（组 0 验证资产，非测试装置——禁改库）
HYBRID_SIDS = ("st_fa536d692525", "st_7cd382d439fa")

# 四路径注册键（api/routes/strategies/ 聚合 prefix=/api/strategies）
ROUTES = ("backtest", "compare", "verify", "batch")


def _exit_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", {"type": "lt", "threshold": 35})


def _plugin_source(**extra) -> dict:
    src = {"type": "formula", "ref": "fx_fx_trend_pullback",
           "threshold": 0.5, "operator": ">"}
    src.update(extra)
    return src


def _pure_plugin_dna() -> StrategyDNA:
    """纯插件策略：signal_genes 仅 EXIT 角色+formula 源（T2 下结构
    合法——与红灯基线锚 R1 同构）。"""
    return StrategyDNA(
        signal_genes=[_exit_gene()],
        signal_sources=[_plugin_source()],
        risk_genes=RiskGenes(),
    )


def _persist(db_path: Path, sid: str, dna_json: str) -> None:
    """策略行写入测试库（save_strategy 无 DNA 校验——repository 层
    原语；读库侧防线是被测面，写入走直通道沿 T3 威胁模型）。"""
    from core.persistence.repository.strategy import save_strategy

    save_strategy(
        db_path, strategy_id=sid, dna_json=dna_json,
        symbol="BTCUSDT", timeframe="4h", name=sid,
        gene_signature=f"sig-{sid}")  # 显式唯一签名防同签名去重吞行


def _post_route(client: TestClient, route: str, sid: str):
    """四路径同语义请求（strategy_id 消费面）。"""
    if route == "backtest":
        return client.post("/api/strategies/backtest", json={
            "strategy_id": sid, "dataset_id": "BTCUSDT_4h"})
    if route == "compare":
        return client.post("/api/strategies/compare", json={
            "strategy_ids": [sid], "dataset_id": "BTCUSDT_4h"})
    if route == "verify":
        return client.post("/api/strategies/verify", json={
            "strategy_ids": [sid],
            "data_ranges": [{"start": "2024-01-01",
                             "end": "2024-06-01"}]})
    if route == "batch":
        return client.post("/api/strategies/batch-backtest/stream", json={
            "strategy_ids": [sid],
            "data_ranges": [{"start": "2024-01-01",
                             "end": "2024-06-01"}]})
    raise ValueError(route)


def _read_stored_dna_json(strategy_id: str) -> str:
    """只读通道取库存策略 dna_json（mode=ro——禁改库纪律）。

    库文件或目标行不存在时 skip 保护（删库等环境差异下锚不误报）。
    """
    if not _QUANT_DB.exists():
        pytest.skip(f"库文件不存在，跳过库存实测锚：{_QUANT_DB}")
    conn = sqlite3.connect(f"file:{_QUANT_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT dna_json FROM strategy WHERE strategy_id=?",
            (strategy_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        pytest.skip(f"库存策略 {strategy_id} 不存在（库状态变化），"
                    "跳过库存实测锚")
    return row[0]


class TestHasPluginSourcesUnit:
    """判据单元面：「插件源在场即真」（type∈{formula,model}，不分
    role）——REST 四路径共同消费的判据字面直证。"""

    @pytest.mark.parametrize(
        ("sources", "expected"),
        [
            pytest.param(None, False, id="sources为None"),
            pytest.param([], False, id="sources为空列表"),
            pytest.param([{"type": "rule"}], False,
                         id="rule型非插件源"),
            pytest.param([{"type": "formula", "ref": "fx"}], True,
                         id="formula源在场"),
            pytest.param([{"type": "model", "ref": "mx"}], True,
                         id="model源在场"),
            pytest.param([{"type": "rule"},
                          {"type": "formula", "ref": "fx"}], True,
                         id="混合列表含插件源"),
            pytest.param([{"type": "formula", "ref": "fx",
                           "role": "exit"}], True,
                         id="role=exit源在场即真（不分role）"),
        ],
    )
    def test_has_plugin_sources_presence(self, sources, expected):
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=sources,
            risk_genes=RiskGenes(),
        )
        assert has_plugin_sources(dna) is expected


class TestFourRoutesPluginReject:
    """主格：纯插件策略行经四路径 → 422 PLUGIN_SOURCES_UNSUPPORTED。"""

    @pytest.mark.parametrize("route", ROUTES)
    def test_pure_plugin_row_rejected_on_all_routes(
            self, api_client, route):
        """纯插件策略行 → 422+code+message 含「插件信号源」与
        「沙盒」（能力边界声明+替代通道指引）。"""
        db = api_client.app.state.db_path
        _persist(db, "s-plugin", _pure_plugin_dna().to_json())

        resp = _post_route(api_client, route, "s-plugin")

        assert resp.status_code == 422, f"[{route}] {resp.text}"
        body = resp.json()["detail"]
        assert body["code"] == "PLUGIN_SOURCES_UNSUPPORTED", \
            f"[{route}] {body}"
        assert "插件信号源" in body["message"], f"[{route}] {body}"
        assert "沙盒" in body["message"], f"[{route}] {body}"

    @pytest.mark.parametrize("route", ROUTES)
    def test_hybrid_builtin_genes_row_rejected_on_all_routes(
            self, api_client, route):
        """在场即拒（构造混合形态）：内置 ENTRY/EXIT 基因齐全+插件源
        在场 → 四路径同拒——防误放「混合可跑」（混合形态插件源被静默
        忽略、结果只反映内置基因，比零交易更隐蔽）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[{"type": "rule"}, _plugin_source()],
            risk_genes=RiskGenes(),
        )
        db = api_client.app.state.db_path
        _persist(db, "s-hybrid", dna.to_json())

        resp = _post_route(api_client, route, "s-hybrid")

        assert resp.status_code == 422, f"[{route}] {resp.text}"
        assert resp.json()["detail"]["code"] == \
            "PLUGIN_SOURCES_UNSUPPORTED", f"[{route}] {resp.text}"

    @pytest.mark.parametrize("sid_store", HYBRID_SIDS)
    @pytest.mark.parametrize("route", ROUTES)
    def test_stored_hybrid_rows_rejected_on_all_routes(
            self, api_client, route, sid_store):
        """在场即拒覆盖面锚（库存两行混合形态实测，mode=ro 只读
        取行→写入测试库→经四路径）：两行含内置 ENTRY 基因本就过结构
        校验（红灯基线锚 ⑤b：is_valid=True），但 REST 通道层预期 422
        ——「在场即拒」判据字面的覆盖面锚：防误收窄为「无 ENTRY 基因
        才拒」致两行漏网。库行不可得时 skip（资产缺席非行为缺失）。"""
        dna_json = _read_stored_dna_json(sid_store)
        db = api_client.app.state.db_path
        _persist(db, f"store-{sid_store}", dna_json)

        resp = _post_route(api_client, route, f"store-{sid_store}")

        assert resp.status_code == 422, f"[{route}] {resp.text}"
        assert resp.json()["detail"]["code"] == \
            "PLUGIN_SOURCES_UNSUPPORTED", f"[{route}] {resp.text}"


class TestDistinctErrorCodeOnDamagedDna:
    """分码断言：损坏 DNA 行 → DNA_VALIDATION_FAILED（两码不同值）。"""

    @pytest.mark.parametrize("route", ROUTES)
    def test_damaged_row_reports_dna_validation_failed(
            self, api_client, route):
        """同请求构造下损坏 DNA 行（from_json 可还原、validate_dna
        拒——双空信号形态）→ 422 DNA_VALIDATION_FAILED。与主格
        PLUGIN_SOURCES_UNSUPPORTED 分码：结构/值域非法与通道不支持是
        两个正交判定（补丁①分码契约的通道层证明）。"""
        damaged = StrategyDNA(
            signal_genes=[],
            signal_sources=[],
            risk_genes=RiskGenes(),
        )
        db = api_client.app.state.db_path
        _persist(db, "s-damaged", damaged.to_json())

        resp = _post_route(api_client, route, "s-damaged")

        assert resp.status_code == 422, f"[{route}] {resp.text}"
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", f"[{route}] {body}"
        assert body["code"] != "PLUGIN_SOURCES_UNSUPPORTED"


class TestPlainStrategyControlGroup:
    """对照组：普通策略（无插件源）不因新判据被拒。"""

    def test_plain_strategy_backtests_200(self, api_client):
        """普通策略行经 backtest 路径正常回测 200（判据零误伤面——
        造 300 根 4h bar 使指标 warmup 充足、装载链走通）。"""
        import numpy as np
        import pandas as pd

        data_dir = api_client.app.state.data_dir
        rng = np.random.default_rng(3)
        idx = pd.date_range("2024-01-01", periods=300, freq="4h",
                            tz="UTC", name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 100, 300))
        pd.DataFrame({
            "open": close, "high": close + 50, "low": close - 50,
            "close": close, "volume": rng.uniform(100, 800, 300),
        }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")

        db = api_client.app.state.db_path
        _persist(db, "s-plain", make_dna().to_json())

        resp = _post_route(api_client, "backtest", "s-plain")

        assert resp.status_code == 200, resp.text
        assert resp.json()["total_trades"] >= 0


class TestInlineDnaEntryRejected:
    """内联直传入口（POST /api/strategies/backtest 带 dna 字段）在场
    即拒——REST 回测通道第五入口（实施裁量补齐：T4 目标「静默零交易
    从行为面消除」覆盖同通道全部入口；补齐前实测 200 total_trades=0
    的静默零交易残留，裁量依据随交付呈报披露）。"""

    def test_inline_plugin_dna_rejected(self, api_client):
        import numpy as np
        import pandas as pd

        data_dir = api_client.app.state.data_dir
        rng = np.random.default_rng(3)
        idx = pd.date_range("2024-01-01", periods=300, freq="4h",
                            tz="UTC", name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 100, 300))
        pd.DataFrame({
            "open": close, "high": close + 50, "low": close - 50,
            "close": close, "volume": rng.uniform(100, 800, 300),
        }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")

        import json as _json
        dna = _json.loads(_pure_plugin_dna().to_json())

        resp = api_client.post("/api/strategies/backtest", json={
            "dna": dna,
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
        })

        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "PLUGIN_SOURCES_UNSUPPORTED", body
        assert "插件信号源" in body["message"], body
        assert "沙盒" in body["message"], body
