"""链路 A · 数据导入链 L2（3.1）：导入→落盘→meta 入库→列表→预览→删除。

每步断言到 L2（数值跨步自洽）：落盘 parquet=源 CSV 数值、meta 行=导入参数、
列表计数自洽、preview 数值=落盘数值。TestClient 真栈（tmp db+data_dir），
模块交界处零 mock——外部世界边界=测试构造的 CSV 源数据。
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from tests.reference.fixture_gen import gen_ohlcv

N = 120


@pytest.fixture()
def csv_bytes():
    df = gen_ohlcv("normal", seed=42, n=N)
    buf = io.StringIO()
    buf.write("timestamp,open,high,low,close,volume\n")
    for ts, row in df.iterrows():
        buf.write(
            f"{ts.strftime('%Y-%m-%d %H:%M:%S')},"
            f"{float(row['open'])!r},{float(row['high'])!r},{float(row['low'])!r},"
            f"{float(row['close'])!r},{float(row['volume'])!r}\n"
        )
    return buf.getvalue().encode(), df


def test_data_import_journey_l2(api_client, csv_bytes):
    client = api_client
    content, source_df = csv_bytes

    # ── A2-A3: 导入 → parquet 落盘
    r = client.post(
        "/api/data/import",
        files={"file": ("BTCUSDT_4h.csv", content, "text/csv")},
        # D-7（已登记）：endpoint 用临时文件名（原名-随机后缀）调
        # parse_filename，自动检测恒失败——必须显式传 symbol/interval
        data={"mode": "replace", "symbol": "BTCUSDT", "interval": "4h"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dataset_id"] == "BTCUSDT_4h"
    assert body["rows_imported"] == N, "导入行数与源不一致"

    # ── L2: 落盘数值 = 源 CSV 数值（索引+close 序列逐元素）
    from pathlib import Path
    pq = Path(client.app.state.data_dir) / "BTCUSDT_4h.parquet"
    assert pq.exists(), "parquet 未落盘"
    disk = pd.read_parquet(pq)
    assert len(disk) == N
    pd.testing.assert_index_equal(
        disk.index.tz_convert("UTC"), source_df.index,
    )
    assert (disk["close"].to_numpy() == pytest.approx(
        source_df["close"].to_numpy(), rel=1e-12)), "落盘 close 与源漂移"
    assert (disk["volume"].to_numpy() == pytest.approx(
        source_df["volume"].to_numpy(), rel=1e-12)), "落盘 volume 与源漂移"

    # ── A4: meta 入库 = 导入参数（数值自洽）
    meta = client.get("/api/data/datasets/BTCUSDT_4h").json()
    assert meta["row_count"] == N
    assert meta["symbol"] == "BTCUSDT"
    assert str(meta["time_start"])[:10] == str(source_df.index[0].date())
    assert str(meta["time_end"])[:10] == str(source_df.index[-1].date())

    # ── A5: 列表含新集（计数自洽：恰好一条且 id 匹配）
    listing = client.get("/api/data/datasets").json()
    items = listing["items"] if isinstance(listing, dict) else listing
    ours = [d for d in items if d["dataset_id"] == "BTCUSDT_4h"]
    assert len(ours) == 1
    assert ours[0]["row_count"] == N

    # ── A6: preview 数值 = 落盘数值（首 20 行 close 逐元素）
    prev = client.get("/api/data/datasets/BTCUSDT_4h/preview?limit=20").json()
    rows = prev["rows"] if isinstance(prev, dict) and "rows" in prev else prev["data"]
    assert prev["total_rows"] == N
    assert len(rows) == 20
    for k in range(20):
        assert float(rows[k]["close"]) == pytest.approx(
            float(disk["close"].iloc[k]), rel=1e-12
        ), f"preview 第 {k} 行 close 与落盘漂移"

    # ── A7: ohlcv 端点同源
    ohl = client.get("/api/data/datasets/BTCUSDT_4h/ohlcv").json()
    data = ohl.get("data", ohl)
    assert len(data) == N

    # ── A8: 删除 → 列表消失
    dele = client.delete("/api/data/datasets/BTCUSDT_4h")
    assert dele.status_code in (200, 204)
    listing2 = client.get("/api/data/datasets").json()
    items2 = listing2["items"] if isinstance(listing2, dict) else listing2
    assert all(d["dataset_id"] != "BTCUSDT_4h" for d in items2), "删除后仍在列表"


def test_import_bad_csv_rejected(api_client):
    """坏数据不进链路：低>min(open,close) 的 CSV 被显式拒绝。"""
    client = api_client
    bad = (
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01 00:00:00,100,101,102,100.5,10\n"
    ).encode()
    # D-8 已由 CARD-01/02/03 修复消解：endpoint 捕 ValueError 转 400
    # （此前为未处理异常冒泡）；数据仍未落盘（防线下键保持）
    import pathlib
    resp = client.post(
        "/api/data/import",
        files={"file": ("BAD_4h.csv", bad, "text/csv")},
        data={"mode": "replace", "symbol": "BAD", "interval": "4h"},
    )
    assert resp.status_code == 400
    assert "OHLCV validation failed" in resp.json()["detail"]
    assert not (pathlib.Path(client.app.state.data_dir) / "BAD_4h.parquet").exists()
