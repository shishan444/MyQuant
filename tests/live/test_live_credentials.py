"""凭证管理单元测试（fix-audit-disclosure-security S-01 文件通道版）。

凭据存储=本地凭据文件（MYQUANT_CREDENTIALS_FILE 环境覆写指向 tmp_path
隔离，不触真实 data/credentials.json）。核心安全约束：禁止非 demo 凭证
（demo=False 被 reject，防生产/主网 key；load 侧恒 demo=True 代码承载）。
"""

import json
import os
import stat

import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活

from core.live_trading.credentials import (
    BinanceCredentials,
    CredentialError,
    delete_credentials,
    has_credentials,
    load_credentials,
    save_credentials,
)


@pytest.fixture
def cred_file(tmp_path, monkeypatch):
    """凭据文件隔离：MYQUANT_CREDENTIALS_FILE 覆写指向临时路径。"""
    path = tmp_path / "credentials.json"
    monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(path))
    return path


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
    """文件通道形制（keyring 退役）：save 写文件（两键+chmod 600）、
    load 读回恒 demo=True、缺失三形态 None、delete 删文件。"""

    def test_save_load_roundtrip(self, cred_file):
        c = _valid()
        save_credentials(c)
        assert has_credentials() is True
        loaded = load_credentials()
        assert loaded is not None
        assert loaded.api_key == c.api_key
        assert loaded.api_secret == c.api_secret
        assert loaded.demo is True, "load 侧恒构造 demo=True（代码承载）"

    def test_save_validates_first(self, cred_file):
        # 无效凭证（非 demo）不写入
        with pytest.raises(CredentialError):
            save_credentials(_valid(demo=False))
        assert has_credentials() is False
        assert not cred_file.exists()

    def test_load_none_when_absent(self, cred_file):
        assert load_credentials() is None
        assert has_credentials() is False

    def test_load_none_when_corrupt(self, cred_file):
        """缺失形态②：JSON 损坏按未配置处理（不炸启动）。"""
        cred_file.write_text("not-json{", encoding="utf-8")
        assert load_credentials() is None

    def test_load_none_when_key_empty(self, cred_file):
        """缺失形态③：任一键空按未配置处理。"""
        cred_file.write_text(json.dumps({
            "binance_api_key": "", "binance_secret_key": _VALID_SECRET}),
            encoding="utf-8")
        assert load_credentials() is None

    def test_delete(self, cred_file):
        save_credentials(_valid())
        assert delete_credentials() is True
        assert has_credentials() is False
        assert delete_credentials() is False  # 再删返回 False

    def test_saved_file_form(self, cred_file):
        """文件形制锚：仅两键+chmod 0600+无 demo 键（收紧）。"""
        save_credentials(_valid())
        data = json.loads(cred_file.read_text(encoding="utf-8"))
        assert set(data.keys()) == {"binance_api_key", "binance_secret_key"}, \
            "凭据文件仅两键——demo 约束由代码承载，文件无 demo 键"
        assert data["binance_api_key"] == _VALID_KEY
        mode = stat.S_IMODE(os.stat(cred_file).st_mode)
        assert mode == 0o600, f"凭据文件权限应 0600，实际 {oct(mode)}"

    def test_env_override_effective(self, tmp_path, monkeypatch):
        """MYQUANT_CREDENTIALS_FILE 覆写路径生效。"""
        alt = tmp_path / "alt-creds.json"
        monkeypatch.setenv("MYQUANT_CREDENTIALS_FILE", str(alt))
        save_credentials(_valid())
        assert alt.exists()
