"""POST/GET 面 series 档支持（dynamic-stop-loss-series CARD-03——
team-lead 裁决「POST 面 series 档应支持」沿 change-1 A8 直建先例）。

RiskGenesModel 补 sl_series_source 可选字段后：POST 直建可达、GET
回显不吞（dna=null 消失缺陷修复面）、半配置闸（validate_dna 非
series 档携带来源键拒——与登记面断言③同判）；C4-F1 POST 预检
集成锚随构造前提恢复闭合（库去重/POST 预检/进化 exclude-set 三
消费方最后一面）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

_SRC = {"indicator": "BB", "params": {"period": 20, "std": 2},
        "field": "lower"}


def _dna_dict(risk=None) -> dict:
    r = {"stop_loss": 0.05, "take_profit": 0.10, "position_size": 0.3}
    if risk is not None:
        r = risk
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}}],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": r,
    }


def _body(dna) -> dict:
    return {"name": "series 直建", "dna": dna,
            "symbol": "BTCUSDT", "timeframe": "4h"}


_SERIES_RISK = {"sl_mode": "series", "sl_series_source": dict(_SRC),
                "take_profit": 0.10, "position_size": 0.3}


class TestTransportLayer:
    def test_risk_genes_model_accepts_series_source(self):
        """传输层：RiskGenesModel 接受 sl_series_source 可选字段
        （extra=forbid 下显式键——漏键即 POST 422+GET dna=null 消失）。"""
        from api.schemas import RiskGenesModel
        m = RiskGenesModel(sl_mode="series", sl_series_source=dict(_SRC))
        assert m.sl_series_source == _SRC
        assert RiskGenesModel().sl_series_source is None  # 非 series 缺省

    def test_non_dict_source_rejected_by_pydantic(self):
        """传输层类型面：非 dict 形态 Pydantic 拒（结构校验前置）。"""
        from api.schemas import RiskGenesModel
        with pytest.raises(Exception):
            RiskGenesModel(sl_series_source="not-a-dict")


class TestPostSeriesLifecycle:
    def test_post_series_dna_created(self, api_client):
        """锚①：POST 直建 series 档 DNA → 201（结构合法过 validate_dna
        ——全局校验不因链路支持误伤）。"""
        r = api_client.post("/api/strategies",
                            json=_body(_dna_dict(_SERIES_RISK)))
        assert r.status_code == 201, r.text
        assert r.json()["strategy_id"]

    def test_get_echoes_series_source(self, api_client):
        """锚②：GET 回显——dna 非 null 且 risk_genes.sl_series_source
        原样回显（登记面落库同读回路径；dna=null 消失缺陷的检出锚）。"""
        r = api_client.post("/api/strategies",
                            json=_body(_dna_dict(_SERIES_RISK)))
        sid = r.json()["strategy_id"]
        g = api_client.get(f"/api/strategies/{sid}")
        assert g.status_code == 200, g.text
        dna = g.json()["dna"]
        assert dna is not None, "回显不吞 DNA"
        rg = dna["risk_genes"]
        assert rg["sl_mode"] == "series"
        assert rg["sl_series_source"] == _SRC
        assert "stop_loss" not in rg or rg["stop_loss"] == 0.05, \
            "stop_loss 残留回显（忠实披露——引擎不消费）"

    def test_half_config_post_rejected(self, api_client):
        """锚③：半配置闸——POST pct 档+sl_series_source → 422
        DNA_VALIDATION_FAILED，errors 含 half-config（validate_dna 与
        登记面断言③同判——传输层补键后 POST 成可达入口须落闸）。"""
        bad = {"stop_loss": 0.05, "take_profit": 0.10,
               "position_size": 0.3, "sl_series_source": dict(_SRC)}
        r = api_client.post("/api/strategies", json=_body(_dna_dict(bad)))
        assert r.status_code == 422, r.text
        blob = str(r.json())
        assert "DNA_VALIDATION_FAILED" in blob
        assert "half-config" in blob, \
            f"半配置判据在 errors 中可辨，实际 {blob[:300]}"


class TestF1PostPrecheckNoOverwrite:
    def test_series_post_not_caught_by_pct_duplicated(self, api_client):
        """C4-F1 POST 预检集成锚（三消费方最后一面闭合）：库内 pct 同
        信号基因既有行在场 → POST series 档同信号基因 → 201 新建（slsrc
        段签名不命中 duplicated 分流）；对照同 pct DNA 再 POST → 200
        duplicated（分流通道健在——不命中系签名不同非通道失效）。"""
        r_pct = api_client.post(
            "/api/strategies", json={"name": "pct 基因行",
                                     "dna": _dna_dict(),
                                     "symbol": "BTCUSDT", "timeframe": "4h"})
        assert r_pct.status_code == 201, r_pct.text
        r_series = api_client.post(
            "/api/strategies",
            json={"name": "series 同基因", "dna": _dna_dict(_SERIES_RISK),
                  "symbol": "BTCUSDT", "timeframe": "4h"})
        assert r_series.status_code == 201, \
            f"series 不命中 duplicated（实际 {r_series.status_code}: " \
            f"{r_series.text[:200]}"
        assert "duplicated" not in r_series.json()
        # 检出力对照：同 pct DNA 再 POST 命中 200 duplicated
        r_again = api_client.post(
            "/api/strategies", json={"name": "pct 重复",
                                     "dna": _dna_dict(),
                                     "symbol": "BTCUSDT", "timeframe": "4h"})
        assert r_again.status_code == 200
        assert r_again.json()["duplicated"] is True
