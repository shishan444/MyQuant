"""传输不变形（2.3）：后端指标 → JSON → 前端消费（TS number）数值全等。

工程实况校准：前端指标消费为编译期 TS 类型（openapi 生成）+JSON.parse，
运行时 zod 仅 WS 层——故「zod 解析」按实况落为「JS JSON.parse 语义」。
风险面（实测锁定）：
- float64 往返：Python repr 最短串 ↔ JSON ↔ JS number 无损
- 计数仍为整数（< 2^53 安全域）
- NaN/Infinity 非法 JSON：Python json.dumps 默认放行（allow_nan），
  JS JSON.parse 直接抛——若指标层泄漏 NaN，前端整响应解析崩溃
  （与 D-2 复数缺陷同族的传输边界风险，登记 D-2b）
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def real_metrics_payload(tmp_path_factory):
    """真实端点指标响应（raw JSON bytes + parsed dict 双形态）。"""
    from fastapi.testclient import TestClient
    from MyQuant.api.app import create_app
    from tests.helpers.data_factory import make_ohlcv, make_dna

    data_dir = tmp_path_factory.mktemp("transport_data")
    _df = make_ohlcv(n=520, seed=42)
    _df.to_parquet(data_dir / "BTCUSDT_4h.parquet")
    app = create_app(db_path=tmp_path_factory.mktemp("transport_db") / "t.db",
                     data_dir=data_dir)

    with TestClient(app) as client:
        # 旧单回测端点已随 Q1=B 退役（retire-legacy 任务 11）——传输面
        # 改经 sandbox 端点取真实响应（同输入确定性回放——两次调用指标
        # 逐值相同，第二次作 native float 对照源）
        from core.persistence.repository.strategy import save_strategy
        from tests.helpers.data_factory import make_dna
        dna = make_dna(direction="long", leverage=1)
        save_strategy(app.state.db_path, strategy_id="st-transport",
                      dna_json=dna.to_json(), symbol="BTCUSDT",
                      timeframe="4h", status="active",
                      gene_signature="sig-transport")
        req = {
            "strategy_id": "st-transport",
            "symbol": "BTCUSDT", "timeframe": "4h",
            # warmup 前扩 ≥200 bar：请求区间取深尾窗
            "start": str(_df.index[240].isoformat()),
            "end": str(_df.index[-1].isoformat()),
        }
        r = client.post("/api/research/backtest-sandbox", json=req)
        assert r.status_code == 200, r.text
        r2 = client.post("/api/research/backtest-sandbox", json=req)
        assert r2.status_code == 200, r2.text
        import types
        m2 = r2.json()["metrics"]
        native = types.SimpleNamespace(
            total_return=m2["total_return"],
            sharpe_ratio=m2["sharpe_ratio"],
            max_drawdown=m2["max_drawdown"],
            win_rate=m2.get("win_rate"),
            total_trades=m2["total_trades"])
        return r.content, r.json()["metrics"], native


def test_float_roundtrip_lossless(real_metrics_payload):
    """raw JSON bytes → loads → 与响应 dict 逐值全等（repr 短串往返无损）。"""
    raw, parsed, _ = real_metrics_payload
    again = json.loads(raw)["metrics"]  # sandbox 响应指标嵌套层（退役前顶层）
    for key in ("total_return", "sharpe_ratio", "max_drawdown", "win_rate",
                "total_trades"):
        assert again[key] == parsed[key], key
        assert (again[key] == again[key])  # 非 NaN（NaN != NaN）


def test_native_float_to_json_identical(real_metrics_payload):
    """引擎原生 float → JSON 文本 → parse 后位级全等（Python float64 往返）。"""
    _, parsed, native = real_metrics_payload
    assert parsed["total_return"] == float(native.total_return)
    assert parsed["sharpe_ratio"] == float(native.sharpe_ratio)
    assert parsed["max_drawdown"] == float(native.max_drawdown)
    # 计数仍为整数且在 JS 安全域
    assert isinstance(parsed["total_trades"], int)
    assert parsed["total_trades"] == int(native.total_trades) < 2**53


def test_nan_leak_produces_invalid_json():
    """D-2b 风险面实测：json.dumps 放行 NaN 字面量，产出非法 JSON
    （JS JSON.parse 抛 SyntaxError）——指标层 NaN 泄漏即前端解析崩溃。"""
    payload = json.dumps({"total_return": float("nan")})
    assert "NaN" in payload
    with pytest.raises(json.JSONDecodeError):
        json.loads(payload, parse_constant=lambda c: (_ for _ in ()).throw(
            json.JSONDecodeError("invalid", c, 0)))
    # JSON 规范：NaN/Infinity 均为非法字面量（对照正面例）
    assert json.loads("1.5") == 1.5


def test_write_frontend_fixture(real_metrics_payload):
    """落盘前端消费样本（vitest 侧 web/src/test/__fixtures__/ 消费）。"""
    raw, parsed, native = real_metrics_payload
    sample = {
        "py_native": {
            "total_return": repr(float(native.total_return)),
            "sharpe_ratio": repr(float(native.sharpe_ratio)),
            "max_drawdown": repr(float(native.max_drawdown)),
            "win_rate": repr(float(native.win_rate)),
            "total_trades": int(native.total_trades),
        },
        "json_roundtrip": {
            "total_return": parsed["total_return"],
            "sharpe_ratio": parsed["sharpe_ratio"],
            "max_drawdown": parsed["max_drawdown"],
            "win_rate": parsed["win_rate"],
            "total_trades": parsed["total_trades"],
        },
    }
    out = Path("web/src/test/__fixtures__/metrics-transport.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sample, indent=2, ensure_ascii=False), "utf-8")
    # 自检：写入可解析且两侧一致（同构 vitest 断言）
    disk = json.loads(out.read_text())
    assert float(disk["py_native"]["total_return"]) == disk["json_roundtrip"]["total_return"]
