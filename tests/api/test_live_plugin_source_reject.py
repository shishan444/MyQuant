"""实盘装载插件源拒绝测试（recognize-plugin-source-dna 任务 4.2）。

契约出处（design 卡 T4·第一问方向二，2026-10-19 用户裁决生效）：
live 链未接入插件信号组装，插件源在场即拒（含混合形态）。装载点=
POST /api/live-trading/tasks 创建通道（该点先过 validate_dna 结构
校验，结构放行后新增环境能力判据）→ 422
{"code": "PLUGIN_SOURCES_UNSUPPORTED", "message": 含「不支持」}。
错误码分立（补丁①）：PLUGIN_SOURCES_UNSUPPORTED（环境不支持）≠
DNA_VALIDATION_FAILED（策略无效）——「策略坏 vs 环境不支持」语义
区分的载体。

断言从 design T4 契约推导（不从实现反推）。经 TestClient 走真实
路由装配（integration 层，沿 tests/api 既有 api_client fixture
形制——create_app(db_path, data_dir) 带参构造，无环境前置校验豁免
问题：本路由的校验序为 from_json→validate_dna→has_plugin_sources
→validate_trading_compatibility，全部为请求内同步判定）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient  # noqa: F401（fixture 类型标注用）

from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA
from tests.helpers.data_factory import make_dna


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


def _create(client: TestClient, dna: StrategyDNA):
    return client.post("/api/live-trading/tasks", json={
        "symbol": "BTCUSDT", "timeframe": "4h",
        "dna_json": dna.to_json(), "direction": "long"})


# 字节基线（signal-sources-structure-gate 1.2 补强，采集时点=卡 V 判据
# 落地后、卡 G 收敛前〔组 1 工作区，基 commit 79642c7〕；live 入口
# library_dna=False 态与 channel="live" 映射的字面量锚——卡 G 收敛为
# helper 后须逐字节复现〔AC-9 双点绿判据的锚面〕）
LIVE_PLUGIN_MESSAGE = ("执行环境不支持插件信号源（live 链未接入插件"
                       "信号组装）——请经研究验证链沙盒回测")
LIVE_DNA_INLINE_MESSAGE = "DNA 值域/结构校验未通过"


class TestLivePluginSourceReject:
    """live 链装载拒绝（错误码分立+语义短语）。"""

    @pytest.mark.parametrize(
        "source_type", ["formula", "model"], ids=["formula源", "model源"])
    def test_pure_plugin_dna_rejected_422(self, api_client, source_type):
        """主格：纯插件策略（EXIT 基因+插件源，T2 下结构合法——请求
        先过 validate_dna）→ 422+code=PLUGIN_SOURCES_UNSUPPORTED+
        message 含「不支持」（语义=执行环境不支持而非策略无效）。"""
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[{"type": source_type,
                             "ref": "fx_fx_trend_pullback",
                             "threshold": 0.5, "operator": ">"}],
            risk_genes=RiskGenes(),
        )
        resp = _create(api_client, dna)
        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "PLUGIN_SOURCES_UNSUPPORTED", body
        assert "不支持" in body["message"], body
        # 二级断言（字节基线）：message 逐字节+detail 键集精确（PLUGIN
        # 段无 errors 键）
        assert body["message"] == LIVE_PLUGIN_MESSAGE, body
        assert set(body) == {"code", "message"}, body

    def test_hybrid_builtin_genes_plus_plugin_source_rejected_422(
            self, api_client):
        """在场即拒（混合形态）：内置 ENTRY/EXIT 基因齐全+插件源在场
        → 422 同拒——防误放「混合可跑」。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[_plugin_source()],
            risk_genes=RiskGenes(),
        )
        resp = _create(api_client, dna)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "PLUGIN_SOURCES_UNSUPPORTED"

    def test_plugin_reject_precedes_trading_compatibility(
            self, api_client):
        """顺位锚：既含插件源又 live 不兼容（sl_mode="trail"——值域
        合法的 live 链不支持形态）的策略 → 报
        PLUGIN_SOURCES_UNSUPPORTED 而非 DNA_TRADING_INCOMPATIBLE
        ——插件判据先于 trading_compatibility 判出（若判据顺序颠倒，
        本用例将以另一码失败）。"""
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[_plugin_source()],
            risk_genes=RiskGenes(sl_mode="trail"),
        )
        resp = _create(api_client, dna)
        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "PLUGIN_SOURCES_UNSUPPORTED", body


class TestLiveControlGroupAndDistinctCode:
    """对照组与分码：普通策略不因插件判据被拒；坏策略报另一码。"""

    def test_plain_valid_dna_create_succeeds(self, api_client):
        """对照组：普通合法 DNA（无插件源；pct 止损 long 方向=
        live 链兼容形态）→ 201 创建成功——新判据对无插件源策略
        零误伤。"""
        resp = _create(api_client, make_dna())
        assert resp.status_code == 201, resp.text
        assert resp.json()["task_id"].startswith("lt_")

    def test_invalid_value_dna_reports_dna_validation_failed(
            self, api_client):
        """分码对照：值域损坏 DNA（position_size 越上界——from_json
        过、validate_dna 拒）→ 422 DNA_VALIDATION_FAILED。与主格
        PLUGIN_SOURCES_UNSUPPORTED 两码不同值（「策略坏」与「环境
        不支持」分道）。"""
        dna = make_dna()
        dna.risk_genes.position_size = 2.0  # 越上界 1.0
        resp = _create(api_client, dna)
        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "DNA_VALIDATION_FAILED", body
        assert body["code"] != "PLUGIN_SOURCES_UNSUPPORTED"
        # 二级断言（字节基线）：直传态 message 逐字节+detail 键集精确
        assert body["message"] == LIVE_DNA_INLINE_MESSAGE, body
        assert set(body) == {"code", "message", "errors"}, body
