"""/api/validate 统计链事件流锚（trigger-policy 卡3 / tasks 4.1+4.2）。

独立对照法：掩码与事件位置在测试内独立复算（close > 阈值的手算
掩码 + mask_to_events 权威变换），与引擎经真实路由产出的
triggers[].time / total_count 对拍——锚「引擎正确消费权威变换」
的接缝（变换本体语义归 test_event_policy）。

尾部触发语义：trigger_loc+1 越界的尾部事件在引擎既有逻辑中被
continue 跳过（triggers 与 total_count 同步不含——期望集同款过滤）。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from api.app import create_app
from core.analysis.event_policy import mask_to_events
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def env(tmp_data_dir: Path):
    """200 bar 4h 确定性数据 + 中位阈值（掩码必有连续段与间隔段）。"""
    df = make_ohlcv(n=200, freq="4h", start="2024-01-01", seed=7)
    df.to_parquet(tmp_data_dir / f"{SYMBOL}_4h.parquet")
    return df, float(df["close"].median())


@pytest.fixture
def client(tmp_path: Path, tmp_data_dir: Path):
    app = create_app(db_path=tmp_path / "test.db", data_dir=tmp_data_dir)
    with TestClient(app) as c:
        yield c


def _payload(env, policy=None):
    df, threshold = env
    body = {
        "pair": SYMBOL,
        "timeframe": "4h",
        "start": df.index[0].isoformat(),
        "end": df.index[-1].isoformat(),
        # target 为 str 形（ConditionInput 契约——引擎侧解析为数值）
        "when": [{"subject": "close", "action": "gt",
                  "target": repr(threshold)}],
        "then": [{"subject": "close", "action": "rise", "target": "0"}],
    }
    if policy is not None:
        body["trigger_policy"] = policy
    return body


def _expected_times(env, policy):
    """独立复算：手算掩码 → 权威变换 → 过滤尾部（then_window 根不足）。"""
    df, threshold = env
    mask = (df["close"] > threshold).values
    events = mask_to_events(mask, policy)
    events = events[events + 1 < len(df)]   # 引擎尾部 continue 语义
    return [str(df.index[i]) for i in events]


class TestValidateTriggerPolicy:
    def test_缺省_逐bar直译对照(self, client, env) -> None:
        resp = client.post("/api/validate", json=_payload(env))
        assert resp.status_code == 200
        data = resp.json()
        assert [t["time"] for t in data["triggers"]] == _expected_times(env, None)
        assert data["total_count"] == len(_expected_times(env, None))

    def test_first_true_触发点为事件位置(self, client, env) -> None:
        resp = client.post("/api/validate", json=_payload(
            env, {"mode": "first_true"}))
        assert resp.status_code == 200
        data = resp.json()
        expected = _expected_times(env, {"mode": "first_true"})
        assert [t["time"] for t in data["triggers"]] == expected
        assert data["total_count"] == len(expected)
        # 事件数 ≤ 逐 bar 触发数（密度上界——first_true 只减不增）
        assert data["total_count"] <= len(_expected_times(env, None))

    def test_suppress_bars_事件口径(self, client, env) -> None:
        resp = client.post("/api/validate", json=_payload(
            env, {"mode": "suppress_bars", "bars": 24}))
        assert resp.status_code == 200
        data = resp.json()
        expected = _expected_times(env, {"mode": "suppress_bars", "bars": 24})
        assert [t["time"] for t in data["triggers"]] == expected
        assert data["total_count"] == len(expected)

    def test_显式_none_与缺省_输出一致(self, client, env) -> None:
        plain = client.post("/api/validate", json=_payload(env)).json()
        explicit = client.post("/api/validate", json=_payload(
            env, {"mode": "none"})).json()
        assert plain == explicit            # 归一 None=缺省同路径

    def test_bars_超界_求值侧_422(self, client, env) -> None:
        """bars > len(df)-1（200 bar 上界 199）→ 422 附 legal_range。"""
        resp = client.post("/api/validate", json=_payload(
            env, {"mode": "suppress_bars", "bars": 500}))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["legal_range"] == "1..199"

    def test_非法策略_路由归一层_422(self, client, env) -> None:
        for bad in ({"mode": "cooldown"}, {"mode": "suppress_bars"},
                    {"mode": "first_true", "bars": 4}):
            resp = client.post("/api/validate", json=_payload(env, bad))
            assert resp.status_code == 422
            assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"

    def test_bars_等于上界_合法(self, client, env) -> None:
        """bars = len(df)-1（恰在上界）放行——闭区间端点语义。"""
        resp = client.post("/api/validate", json=_payload(
            env, {"mode": "suppress_bars", "bars": 199}))
        assert resp.status_code == 200
