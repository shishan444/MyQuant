"""Config 路由特征网（修复期安全网，现状记录语义）。

覆盖 api/routes/config.py 的 GET/PUT 端点与凭证掩码语义：
- GET 默认配置回显（无 config.json 时全默认值）
- GET 凭证键 masked 回显（B9.2 S1：前4后4中 ***；短值全掩；空串回空）
- PUT 普通键更新并落盘
- PUT 凭证哨兵语义（值含 *** = 未修改回显，不覆盖已存值）
- PUT 未知键被忽略（不在 _DEFAULT_CONFIG 内）
- config.json 损坏时回退默认值（容错分支）
"""
from __future__ import annotations

import json
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
        assert data["binance_api_key"] == ""
        assert data["binance_secret_key"] == ""
        assert data["binance_connected"] is False

    def test_get_masks_long_credential_fully(self, api_client: TestClient, db_path: Path) -> None:
        """长凭证值全掩码回显（CARD-06 后不泄露首尾片段）。"""
        _write_config_file(db_path, {"binance_api_key": "abcdefghij"})
        response = api_client.get("/api/config")
        assert response.status_code == 200
        assert response.json()["binance_api_key"] == "***"

    def test_get_masks_short_credential_fully(self, api_client: TestClient, db_path: Path) -> None:
        """短凭证值整体掩码（CARD-06 后长值同形制，不泄露任何片段）。"""
        _write_config_file(db_path, {"binance_api_key": "ab"})
        response = api_client.get("/api/config")
        assert response.json()["binance_api_key"] == "***"

    def test_get_keeps_empty_credential_empty(self, api_client: TestClient, db_path: Path) -> None:
        """未配置（空串）凭证回显空串。"""
        _write_config_file(db_path, {"binance_api_key": ""})
        response = api_client.get("/api/config")
        assert response.json()["binance_api_key"] == ""

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

    def test_put_credential_sentinel_does_not_overwrite(
        self, api_client: TestClient, db_path: Path,
    ) -> None:
        """哨兵语义：PUT 含 *** 的凭证值 = 前端回显未修改，不覆盖已存值。

        CARD-06 后回显恒 ***（无区分度），底层值是否被覆盖改从
        落盘文件明文断言——比旧版回显推断更强。
        """
        _write_config_file(db_path, {"binance_api_key": "real-secret-key-123"})
        response = api_client.put("/api/config", json={"binance_api_key": "sentinel-***-value"})
        assert response.status_code == 200
        assert response.json()["binance_api_key"] == "***"
        # 落盘验证：底层原值未被哨兵覆盖（明文仍在文件）
        saved = json.loads(_config_file(db_path).read_text(encoding="utf-8"))
        assert saved["binance_api_key"] == "real-secret-key-123"
        # 再次 GET 回显仍为全掩码
        assert api_client.get("/api/config").json()["binance_api_key"] == "***"

    def test_put_credential_plain_value_overwrites(
        self, api_client: TestClient, db_path: Path,
    ) -> None:
        """非哨兵的新凭证值正常覆盖旧值（回显全掩码、落盘明文新值）。"""
        _write_config_file(db_path, {"binance_api_key": "old-secret-value-9"})
        response = api_client.put("/api/config", json={"binance_api_key": "new-plain-value-99"})
        assert response.status_code == 200
        assert response.json()["binance_api_key"] == "***"
        saved = json.loads(_config_file(db_path).read_text(encoding="utf-8"))
        assert saved["binance_api_key"] == "new-plain-value-99"

    def test_put_response_is_masked_like_get(self, api_client: TestClient) -> None:
        """PUT 响应与 GET 一致走全掩码回显。"""
        response = api_client.put("/api/config", json={"binance_secret_key": "1234567890abc"})
        assert response.status_code == 200
        assert response.json()["binance_secret_key"] == "***"


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
