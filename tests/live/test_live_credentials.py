"""第1步 凭证管理 单元测试。

用内存 keyring backend 隔离真实 macOS Keychain，不依赖真实凭证。
核心安全约束：禁止非 demo 凭证（demo=False 被 reject，防生产/主网 key）。
"""

import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
import json

import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from core.live_trading import credentials as cred_mod
from core.live_trading.credentials import (
    BinanceCredentials,
    CredentialError,
    delete_credentials,
    has_credentials,
    load_credentials,
    save_credentials,
)


class _MemoryKeyring(KeyringBackend):
    """内存钥匙串 backend（测试隔离用）。"""

    priority = 1

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):  # noqa: ANN001
        self._store[(service, username)] = password

    def get_password(self, service, username):  # noqa: ANN001
        return self._store.get((service, username))

    def delete_password(self, service, username):  # noqa: ANN001
        key = (service, username)
        if key not in self._store:
            raise PasswordDeleteError("not found")
        del self._store[key]


@pytest.fixture
def mem_keyring(monkeypatch):
    """替换 credentials 模块内的 keyring 调用为内存实现。"""
    backend = _MemoryKeyring()
    monkeypatch.setattr(cred_mod, "get_password", backend.get_password)
    monkeypatch.setattr(cred_mod, "set_password", backend.set_password)
    monkeypatch.setattr(cred_mod, "delete_password", backend.delete_password)
    return backend


_VALID_KEY = "t" * 64
_VALID_SECRET = "s" * 64


def _valid(demo: bool = True) -> BinanceCredentials:
    return BinanceCredentials(api_key=_VALID_KEY, api_secret=_VALID_SECRET, demo=demo)


class TestCredentialsValidate:
    def test_valid_demo_passes(self):
        _valid().validate()  # 不抛即通过

    def test_empty_rejected(self):
        with pytest.raises(CredentialError):
            BinanceCredentials(api_key="", api_secret=_VALID_SECRET).validate()
        with pytest.raises(CredentialError):
            BinanceCredentials(api_key=_VALID_KEY, api_secret="").validate()

    def test_short_rejected(self):
        with pytest.raises(CredentialError):
            BinanceCredentials(api_key="short", api_secret=_VALID_SECRET).validate()

    def test_non_demo_rejected(self):
        # 核心安全约束：禁止非 demo（生产/主网）凭证，防误下真实单
        with pytest.raises(CredentialError, match="demo"):
            _valid(demo=False).validate()


class TestCredentialsStore:
    def test_save_load_roundtrip(self, mem_keyring):
        c = _valid()
        save_credentials(c)
        assert has_credentials() is True
        loaded = load_credentials()
        assert loaded is not None
        assert loaded.api_key == c.api_key
        assert loaded.api_secret == c.api_secret
        assert loaded.demo is True

    def test_save_validates_first(self, mem_keyring):
        # 无效凭证（非 demo）不写入
        with pytest.raises(CredentialError):
            save_credentials(_valid(demo=False))
        assert has_credentials() is False

    def test_load_none_when_absent(self, mem_keyring):
        assert load_credentials() is None
        assert has_credentials() is False

    def test_delete(self, mem_keyring):
        save_credentials(_valid())
        assert delete_credentials() is True
        assert has_credentials() is False
        assert delete_credentials() is False  # 再删返回 False

    def test_stored_payload_is_json(self, mem_keyring):
        # keyring 存 JSON payload（供 load 解析），走 Keychain 不落盘工程目录
        save_credentials(_valid())
        payload = mem_keyring.get_password(cred_mod.SERVICE_DEMO, "default")
        assert payload is not None
        data = json.loads(payload)
        assert data["api_key"] == _VALID_KEY
        assert data["demo"] is True
