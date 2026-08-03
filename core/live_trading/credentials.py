"""币安 demo mode 凭证管理（第1步，v3 修正：testnet→demo）。

安全设计：
- 凭证存系统钥匙串（macOS Keychain，经 keyring），**不落盘工程目录、不入 git**。
- 仅接受 demo 凭证（``demo=True``，模拟资金）；非 demo 凭证被 ``validate`` 拒绝
  （核心安全约束，防止误用主网/生产 key 下真实单）。
- 权限提示：demo key 应为专用 key（主账户 → Binance Demo Trading → API Key Management 创建），
  绑定主账户但仅操作 demo 虚拟资金；仍建议最小权限（禁提现）。

demo mode 端点：``demo-fapi.binance.com``（U本位永续），经 ``Client(demo=True)`` 接入。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from keyring import delete_password, get_password, set_password
from keyring.errors import PasswordDeleteError

# 钥匙串服务名（demo 专用，避免与生产混用）
SERVICE_DEMO = "myquant.binance.demo"
_USERNAME = "default"
# 币安 API key/secret 标准长度 64；过短视为异常
_MIN_CRED_LEN = 20


class CredentialError(ValueError):
    """凭证校验失败。"""


@dataclass(frozen=True)
class BinanceCredentials:
    """币安 demo 凭证。"""

    api_key: str
    api_secret: str
    demo: bool = True  # 必须 demo mode（模拟资金），拒生产凭证

    def validate(self) -> None:
        if not self.api_key or not self.api_secret:
            raise CredentialError("api_key/api_secret 不能为空")
        if len(self.api_key) < _MIN_CRED_LEN or len(self.api_secret) < _MIN_CRED_LEN:
            raise CredentialError(
                f"凭证长度异常（币安 key/secret 通常 64 字符；当前 "
                f"key={len(self.api_key)} secret={len(self.api_secret)}）"
            )
        if not self.demo:
            raise CredentialError(
                "实盘模块仅接受 demo 凭证（demo=True）。禁止使用生产/主网凭证。"
            )


def save_credentials(creds: BinanceCredentials, service: str = SERVICE_DEMO) -> None:
    """加密存储凭证到系统钥匙串（先校验）。"""
    creds.validate()
    payload = json.dumps(
        {
            "api_key": creds.api_key,
            "api_secret": creds.api_secret,
            "demo": creds.demo,
        }
    )
    set_password(service, _USERNAME, payload)


def load_credentials(service: str = SERVICE_DEMO) -> Optional[BinanceCredentials]:
    """从系统钥匙串加载凭证；不存在返回 None。"""
    payload = get_password(service, _USERNAME)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CredentialError(f"凭证 payload 解析失败：{exc}") from exc
    return BinanceCredentials(**data)


def has_credentials(service: str = SERVICE_DEMO) -> bool:
    """是否已存储凭证。"""
    return get_password(service, _USERNAME) is not None


def delete_credentials(service: str = SERVICE_DEMO) -> bool:
    """删除凭证；不存在返回 False。"""
    try:
        delete_password(service, _USERNAME)
        return True
    except PasswordDeleteError:
        return False
