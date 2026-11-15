"""数据导入端点非法参数 400 契约（CARD-01/02/03 API 面）。

经真实路由装配（TestClient）验证：symbol/interval 白名单拒绝与
OHLCV 校验失败的 ValueError 在 /api/data/import 与 /import-batch
两端点转换为 HTTP 400，且数据不落盘。
"""

import pytest

pytestmark = [pytest.mark.integration]

from pathlib import Path

from fastapi.testclient import TestClient

GOOD_CSV = (
    "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
).encode()
BAD_OHLCV_CSV = (
    "timestamp,open,high,low,close,volume\n"
    "2024-01-01 00:00:00,100,101,102,100.5,10\n"  # low > min(open, close)
).encode()


def _data_dir(client: TestClient) -> Path:
    return Path(client.app.state.data_dir)


class TestImportEndpointValidation:
    """POST /api/data/import：非法参数 400 + 无落盘。"""

    def test_traversal_symbol_rejected_400(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("data.csv", GOOD_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "../../outside", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "symbol 非法" in resp.json()["detail"]
        assert list(_data_dir(api_client).glob("outside*")) == []

    def test_traversal_interval_rejected_400(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("data.csv", GOOD_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "BTCUSDT", "interval": "../../evil"},
        )
        assert resp.status_code == 400
        assert "interval 非法" in resp.json()["detail"]
        assert list(_data_dir(api_client).glob("BTCUSDT_..*")) == []

    def test_bad_ohlcv_rejected_400_no_write(self, api_client: TestClient) -> None:
        """坏 CSV（OHLCV 校验失败）→ 400 且数据不落盘。"""
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("BAD_4h.csv", BAD_OHLCV_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "BAD", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "OHLCV validation failed" in resp.json()["detail"]
        assert not (_data_dir(api_client) / "BAD_4h.parquet").exists()

    def test_valid_import_still_succeeds(self, api_client: TestClient) -> None:
        """合法导入不受守卫影响（防误伤回归锚）。"""
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("BTCUSDT-4h-2025-01.csv", GOOD_CSV, "text/csv")},
            data={"mode": "replace", "symbol": "BTCUSDT", "interval": "4h"},
        )
        assert resp.status_code == 200
        assert resp.json()["dataset_id"] == "BTCUSDT_4h"
        assert resp.json()["timestamp_precision"] == "ms"  # BINANCE 探测面

    def test_generic_import_precision_null(self, api_client: TestClient) -> None:
        """GENERIC 导入响应 precision 为 null（CARD-13：不可探测不入伪值）。"""
        generic_csv = (
            "timestamp,open,high,low,close,volume\n"
            "2024-01-01 00:00:00,42000.0,42580.0,41850.0,42310.0,312.5\n"
            "2024-01-01 04:00:00,42310.0,43120.0,42100.0,42850.0,289.3\n"
        ).encode()
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("GEN-4h.csv", generic_csv, "text/csv")},
            data={"mode": "replace", "symbol": "GEN", "interval": "4h"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["timestamp_precision"] is None
        assert body["format_detected"] == "generic_ohlcv"


class TestGenericMissingColumns422:
    """CARD-28：GENERIC CSV 缺列从 KeyError 裸穿 500 收口为结构化 400。"""

    def test_missing_ohlcv_columns_400_structured_detail(
        self, api_client: TestClient,
    ) -> None:
        """缺 OHLCV 列 → 400，detail 含缺失列清单与期望列。"""
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("data.csv", (
                "timestamp,open\n2024-01-01 00:00:00,100\n"
            ).encode(), "text/csv")},
            data={"mode": "merge", "symbol": "BTCUSDT", "interval": "4h"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "CSV missing required columns" in detail
        assert "high" in detail and "volume" in detail  # 缺失列点名
        assert "open" in detail  # 期望列清单含未缺列

    def test_missing_timestamp_column_400(
        self, api_client: TestClient,
    ) -> None:
        """缺 timestamp 列 → 400（原 set_index 无条件依赖的隐式必需显式化）。"""
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("data.csv", (
                "open,high,low,close,volume\n100,101,99,100.5,10\n"
            ).encode(), "text/csv")},
            data={"mode": "merge", "symbol": "BTCUSDT", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "timestamp" in resp.json()["detail"]


class TestImportEndpointModeValidation:
    """mode 形制校验（fix-audit-correctness A-03——原 ModeFallback 类
    固化缺陷现状「非法回退 MERGE」已反转）：非法 mode 422 显式拒零
    副作用；batch 空 files 400 面保留。"""

    def test_invalid_mode_rejected_422_structured_detail(
        self, api_client: TestClient
    ) -> None:
        """mode 非法字符串 422：detail 三键（code/实际值/合法值清单）。

        修复前静默回退 MERGE——「替换」意图被静默改写为「合并」。"""
        first = (
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
        ).encode()
        resp = api_client.post(
            "/api/data/import",
            files={"file": ("BTCUSDT-4h-2025-01.csv", first, "text/csv")},
            data={"mode": "bogus", "symbol": "BTCUSDT", "interval": "4h"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "IMPORT_MODE_INVALID"
        assert "bogus" in detail["message"]
        assert detail["legal_values"] == ["merge", "replace", "new"]

    def test_legal_three_modes_unchanged(self, api_client: TestClient) -> None:
        """合法三值（merge/replace/new）行为与修复前一致。

        三值各用独立 dataset（fixture 预置的 naive 索引 dummy 与系统
        导入的 tz-aware 合并会 tz 冲突——原 batch 族测试在案坑位）。"""
        first = (
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
        ).encode()
        cases = [
            ("merge", "ETHUSDT", "4h"),
            ("replace", "BTCUSDT", "1h"),
            ("new", "ETHUSDT", "1h"),
        ]
        for mode, sym, iv in cases:
            resp = api_client.post(
                "/api/data/import",
                files={"file": (f"{sym}-{iv}-2025-01.csv", first,
                                "text/csv")},
                data={"mode": mode, "symbol": sym, "interval": iv},
            )
            assert resp.status_code == 200, f"mode={mode} 应照旧 200"

    def test_batch_empty_files_rejected_422(self, api_client: TestClient) -> None:
        """空 files：FastAPI 请求校验先行拦 422（装载层责任）——
        端点内 `if not files: 400` 为防御分支，真实路由不可达。"""
        resp = api_client.post("/api/data/import-batch", files=[])
        assert resp.status_code == 422

    def test_batch_invalid_mode_rejected_422_same_form(
        self, api_client: TestClient
    ) -> None:
        """batch 端点同款 422（与单端点复制粘贴级一致）。"""
        csv = (
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
        ).encode()
        resp = api_client.post(
            "/api/data/import-batch",
            files=[("files", ("ETHUSDT-4h-2025-01.csv", csv, "text/csv"))],
            data={"symbol": "ETHUSDT", "interval": "4h", "mode": "bogus"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "IMPORT_MODE_INVALID"
        assert detail["legal_values"] == ["merge", "replace", "new"]


class TestImportBatchEndpointValidation:
    """POST /api/data/import-batch：非法参数 400 + 无落盘。"""

    def test_traversal_symbol_rejected_400(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/data/import-batch",
            files=[("files", ("data.csv", GOOD_CSV, "text/csv"))],
            data={"symbol": "../../outside", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "symbol 非法" in resp.json()["detail"]
        assert list(_data_dir(api_client).glob("outside*")) == []

    def test_unicode_digit_symbol_rejected_400(self, api_client: TestClient) -> None:
        """全角数字 symbol（Unicode 匹配陷阱）→ 400。"""
        resp = api_client.post(
            "/api/data/import-batch",
            files=[("files", ("data.csv", GOOD_CSV, "text/csv"))],
            data={"symbol": "１２３", "interval": "4h"},
        )
        assert resp.status_code == 400
        assert "symbol 非法" in resp.json()["detail"]
