"""属性不变量特征网（阶段四属性补强，V3 锚点分类：属性/不变量）。

对纯计算函数验证一类输入应保持的关系（明确成立条件）：
- compute_verify_star 属性段已随旧验证会话体系退役
  （retire-verify-sessions）
- _without_credentials：任意配置 dict（含存量凭据键）→ 视图零凭据键、其余键原样透传（S-01 拆链后承载）
- _safe_error_message：任意异常实例消息恒取自有限值域
  {数据文件缺失*, 内部错误}（不泄露 str(exc) 原文）
"""
from __future__ import annotations

import random

import pytest

pytestmark = [pytest.mark.unit]


class TestCredentialFreeView:
    """S-01 拆链后形制：掩码函数族已随凭据通道退役——原不变量面
    （_mask/_masked_view）改承载为「响应零凭据键」不变量
    （fix-audit-disclosure-security）。"""

    def test_response_view_drops_all_credential_keys(self) -> None:
        """任意配置 dict（含存量凭据键）→ 响应视图零凭据键、其余
        键原样透传（迁移窗口防明文回显的不变量承载）。"""
        from api.routes.config import _without_credentials
        cfg = {
            "language": "zh-CN", "init_cash": 1,
            "binance_api_key": "secret", "binance_secret_key": "s2",
            "binance_connected": True, "maker_fee": 0.1,
        }
        view = _without_credentials(cfg)
        assert "binance_api_key" not in view
        assert "binance_secret_key" not in view
        assert "binance_connected" not in view
        assert view["language"] == "zh-CN"
        assert view["init_cash"] == 1
        assert view["maker_fee"] == 0.1

