"""Config 路由特征网（修复期安全网，现状记录语义）。

覆盖 api/routes/config.py 的 GET/PUT 端点（fix-audit-disclosure-security
S-01 拆链后契约：GET/PUT 零凭据语义——凭据唯一通道=data/credentials.json，
掩码/哨兵语义已随通道退役）：
- GET 默认配置回显（无 config.json 时全默认值）
- GET 响应零凭据键（含迁移前存量窗口防明文回显）
- PUT 普通键更新并落盘
- PUT 凭据键经 extra=ignore 静默忽略（200 但不落盘、不新增凭据键）
- PUT 未知键被忽略（不在 _DEFAULT_CONFIG 内）
- config.json 损坏时回退默认值（容错分支）
- 存量迁移三分支+warning 断言（lifespan，TestLegacyCredentialMigration）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


def _config_file(db_path: Path) -> Path:
    return db_path.parent / "config.json"


def _write_config_file(db_path: Path, payload: dict) -> None:
    _config_file(db_path).write_text(json.dumps(payload), encoding="utf-8")


class TestConfigGet:
    def test_get_returns_defaults_when_no_file(self, api_client: TestClient) -> None:
        """无 config.json 时 GET 返回完整默认配置。"""
        response = api_client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "zh-CN"
        assert data["timezone"] == "UTC+8"
        assert data["init_cash"] == 100000
        assert data["maker_fee"] == 0.1
        assert data["taker_fee"] == 0.1
        assert data["max_positions"] == 1
        # S-01 拆链：默认响应零凭据键（凭据唯一通道=credentials.json）
        assert "binance_api_key" not in data
        assert "binance_secret_key" not in data
        assert "binance_connected" not in data

    def test_get_drops_credential_keys_from_response(
        self, api_client: TestClient, db_path: Path,
    ) -> None:
        """S-01 拆链：GET 响应零凭据键（含迁移前存量窗口防明文回显）。"""
        _write_config_file(db_path, {
            "binance_api_key": "abcdefghij",
            "binance_secret_key": "ab",
            "binance_connected": True,
        })
        response = api_client.get("/api/config")
        assert response.status_code == 200
        body = response.json()
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in body, f"拆链后 GET 响应不应含 {k}"

    def test_get_merges_saved_with_defaults(self, api_client: TestClient, db_path: Path) -> None:
        """存量文件只存部分键时，与默认值合并返回。"""
        _write_config_file(db_path, {"language": "en-US"})
        data = api_client.get("/api/config").json()
        assert data["language"] == "en-US"
        assert data["timezone"] == "UTC+8"  # 未存键回默认

    def test_get_falls_back_on_corrupt_file(self, api_client: TestClient, db_path: Path) -> None:
        """config.json 内容非法 JSON 时回退默认值（不 500）。"""
        _config_file(db_path).write_text("{not valid json", encoding="utf-8")
        response = api_client.get("/api/config")
        assert response.status_code == 200
        assert response.json()["language"] == "zh-CN"


class TestConfigPut:
    def test_put_updates_known_key_and_persists(self, api_client: TestClient) -> None:
        """PUT 更新已知键：响应生效且落盘（再次 GET 仍生效）。"""
        response = api_client.put("/api/config", json={"language": "zh-TW", "init_cash": 50000})
        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "zh-TW"
        assert data["init_cash"] == 50000
        # 落盘验证：新 GET 反映同一值
        assert api_client.get("/api/config").json()["language"] == "zh-TW"

    def test_put_ignores_unknown_keys(self, api_client: TestClient) -> None:
        """不在 _DEFAULT_CONFIG 内的键被忽略，不写入。"""
        response = api_client.put("/api/config", json={"evil_key": "x", "timezone": "UTC+9"})
        assert response.status_code == 200
        assert "evil_key" not in response.json()
        assert response.json()["timezone"] == "UTC+9"

    def test_put_credential_keys_ignored_not_persisted(
        self, api_client: TestClient, db_path: Path,
    ) -> None:
        """S-01 拆链：ConfigUpdate 无凭据字段——客户端误传被 extra=ignore
        静默忽略（200 但不落盘、不新增凭据键）。"""
        _write_config_file(db_path, {"language": "zh-CN"})
        response = api_client.put("/api/config", json={
            "binance_api_key": "leaked-plain-value-99",
            "binance_secret_key": "another-plain",
            "binance_connected": True,
        })
        assert response.status_code == 200
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in response.json()
        saved = json.loads(_config_file(db_path).read_text(encoding="utf-8"))
        for k in ("binance_api_key", "binance_secret_key",
                  "binance_connected"):
            assert k not in saved, f"凭据键 {k} 不得经 PUT 落盘"

    def test_put_normal_keys_unchanged(self, api_client: TestClient) -> None:
        """S-01 拆链回归：普通配置键 PUT/GET 行为逐字节不变。"""
        response = api_client.put("/api/config", json={
            "language": "en-US", "init_cash": 200000})
        assert response.status_code == 200
        body = api_client.get("/api/config").json()
        assert body["language"] == "en-US"
        assert body["init_cash"] == 200000


class TestPutTypeValidation:
    """CARD-29：ConfigUpdate 类型锁定（旧 Dict[str, Any] 全放行）。"""

    def test_dict_value_for_int_key_422(self, api_client: TestClient) -> None:
        """init_cash 传 dict → 422（旧 200 落盘任意结构）。"""
        response = api_client.put(
            "/api/config", json={"init_cash": {"evil": [1, 2]}},
        )
        assert response.status_code == 422

    def test_bool_for_int_field_422(self, api_client: TestClient) -> None:
        """max_positions 传 bool → 422（lax 下 True→1 的隐式收窄被拒）。"""
        response = api_client.put("/api/config", json={"max_positions": True})
        assert response.status_code == 422

    def test_list_for_bool_field_422(self, api_client: TestClient) -> None:
        """bool 字段传 list → 422（注：lax 下 "yes" 等字符串属合法 bool
        解析集，与数字字符串→int 同为保留的 lax 行为，不在收严面）。"""
        response = api_client.put(
            "/api/config", json={"notify_signal": [True]},
        )
        assert response.status_code == 422

    def test_list_for_str_field_422(self, api_client: TestClient) -> None:
        response = api_client.put(
            "/api/config", json={"timezone": ["UTC+8"]},
        )
        assert response.status_code == 422

    def test_valid_typed_update_passes(self, api_client: TestClient) -> None:
        """类型合法的部分更新正常生效（int/float/bool 均过）。"""
        response = api_client.put("/api/config", json={
            "init_cash": 200000, "maker_fee": 0.05,
            "notify_signal": False, "language": "zh-CN",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["init_cash"] == 200000
        assert data["maker_fee"] == 0.05
        assert data["notify_signal"] is False

    def test_partial_update_leaves_unmentioned_keys(
        self, api_client: TestClient,
    ) -> None:
        """exclude_unset 部分更新：未传键保持原值。"""
        api_client.put("/api/config", json={"init_cash": 300000})
        response = api_client.put("/api/config", json={"language": "en-US"})
        data = response.json()
        assert data["language"] == "en-US"
        assert data["init_cash"] == 300000  # 未提及不动

    def test_explicit_null_written_as_null(self, api_client: TestClient, db_path: Path) -> None:
        """显式传 JSON null 落 null（与原 Dict 行为一致，未扩收严面）。"""
        response = api_client.put("/api/config", json={"notify_evolution": None})
        assert response.status_code == 200
        saved = json.loads(_config_file(db_path).read_text(encoding="utf-8"))
        assert saved["notify_evolution"] is None

    def test_unknown_key_ignored_still(self, api_client: TestClient) -> None:
        """未知键静默忽略（extra="ignore"，与旧白名单效果一致）。"""
        response = api_client.put(
            "/api/config", json={"evil_key": "x", "timezone": "UTC+9"},
        )
        assert response.status_code == 200
        assert "evil_key" not in response.json()
        assert response.json()["timezone"] == "UTC+9"
