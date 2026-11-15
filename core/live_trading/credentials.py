"""币安 demo mode 凭证管理（fix-audit-disclosure-security S-01 方向 C：本地凭据文件）。

安全设计：
- 凭据唯一存储通道=本地凭据文件（缺省 ``<工程根>/data/credentials.json``，
  ``MYQUANT_CREDENTIALS_FILE`` 环境变量覆写，不随 db_path 切换——凭据是
  环境身份非实例数据）；写入 chmod 0600、.gitignore 显式排除。
- 仅接受 demo 凭证（``demo=True``，模拟资金）；demo 约束由代码承载——
  load 侧恒构造 ``BinanceCredentials(demo=True)``，文件无 demo 键（较
  keyring 时代收紧：非 demo 值无从进入）。非 demo 凭证被 ``validate``
  拒绝（核心安全约束，防止误用主网/生产 key 下真实单）。
- 缺失语义：文件不存在/JSON 损坏/任一键空 → load 返回 None（损坏不炸
  启动，按未配置处理+warning）；binance_client 抛 CredentialMissingError。
- keyring 通道已退役（单源非并存）；keyring 时代存量数据不迁移（生产
  零写入坐实），曾用脚本存过者手工导出。
- 权限提示：demo key 应为专用 key（主账户 → Binance Demo Trading →
  API Key Management 创建），绑定主账户但仅操作 demo 虚拟资金；仍建议
  最小权限（禁提现）。

demo mode 端点：``demo-fapi.binance.com``（U本位永续），经 ``Client(demo=True)`` 接入。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 凭据文件两键（binance_api_key/binance_secret_key）；demo 约束由代码承载
_CRED_KEY_API = "binance_api_key"
_CRED_KEY_SECRET = "binance_secret_key"
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


def _credentials_path() -> Path:
    """凭据文件路径：MYQUANT_CREDENTIALS_FILE 覆写，缺省工程根 data/。

    工程根由本文件位置上推（Path(__file__)），不依赖 cwd。
    """
    env = os.environ.get("MYQUANT_CREDENTIALS_FILE")
    if env:
        return Path(env)
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "data" / "credentials.json"


def save_credentials(creds: BinanceCredentials) -> None:
    """写入本地凭据文件（先校验；chmod 0600，沿 _write_config 先例）。"""
    creds.validate()
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            _CRED_KEY_API: creds.api_key,
            _CRED_KEY_SECRET: creds.api_secret,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def load_credentials() -> Optional[BinanceCredentials]:
    """从本地凭据文件加载；文件不存在/损坏/任一键空返回 None。

    demo 约束由代码承载：恒构造 demo=True（文件无 demo 键，非 demo
    值无从进入——较 keyring 时代收紧）。
    """
    path = _credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("凭据文件损坏（%s），按未配置处理：%s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("凭据文件形制异常（非 JSON 对象），按未配置处理：%s", path)
        return None
    api_key = data.get(_CRED_KEY_API)
    api_secret = data.get(_CRED_KEY_SECRET)
    if not api_key or not api_secret:
        logger.warning("凭据文件键缺失或为空，按未配置处理：%s", path)
        return None
    return BinanceCredentials(api_key=api_key, api_secret=api_secret, demo=True)


def has_credentials() -> bool:
    """是否已配置凭据（与 load 同判定，防判定分叉）。"""
    return load_credentials() is not None


def delete_credentials() -> bool:
    """删除凭据文件；不存在返回 False。"""
    path = _credentials_path()
    if not path.exists():
        return False
    path.unlink()
    return True
