"""词表端点与单源跟随（alignment-batch γ，spec research-vocabulary）。

验收面：响应形制与四族口径计数、注册表跟随（monkeypatch——封死启动
快照/复制常量形制）、启动完整性断言红路径（reload——扩词忘映射即红）。
"""

import importlib

import pytest

pytestmark = [pytest.mark.integration]


def test_vocabulary_shape_and_caliber(api_client):
    """响应形制：total==26、四族计数 12/2/10/2、口径双披露单列。"""
    resp = api_client.get("/api/research/vocabulary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 26
    assert body["counts"] == {
        "时序变换": 12, "时序结构": 2, "逐元素": 10, "数学词汇": 2,
    }
    assert sum(body["counts"].values()) == body["total"]
    # 双口径披露：主口径数字与「历史口径差异」须同现（单列不混合）
    note = body["caliber_note"]
    assert "12" in note and "2" in note
    assert "历史口径" in note and "rolling_quantile" in note
    # 词粒度抽锚：kalman 族归数学词汇、sqrt 归逐元素（v2.1 主口径）
    assert "kalman" in body["families"]["数学词汇"]
    assert "sqrt" in body["families"]["逐元素"]


def test_vocabulary_follows_registry_not_snapshot(api_client, monkeypatch):
    """注册表跟随（失效侧）：monkeypatch 扩词 → 响应含该词——封死启动
    快照/复制常量实现形制（端点读 formula.FORMULA_FUNCTIONS 属性现算）。"""
    from core.signals import formula

    patched = dict(formula.FORMULA_FUNCTIONS)
    patched["hypothetical_new_word"] = lambda s: s
    monkeypatch.setattr(formula, "FORMULA_FUNCTIONS", patched)

    resp = api_client.get("/api/research/vocabulary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 27
    assert "hypothetical_new_word" in body["families"]["未映射"]


def test_startup_assertion_fires_on_mapping_drift(monkeypatch):
    """启动断言红路径：键集漂移（映射少一词）→ 模块 import 即
    AssertionError（fail-loud——扩词忘映射即断言红，不静默漏族）。"""
    from core.signals import formula
    from api.routes.research import registry

    patched = dict(formula.FORMULA_FUNCTIONS)
    patched.pop("kalman")  # 制造映射多出一词的键集漂移
    monkeypatch.setattr(formula, "FORMULA_FUNCTIONS", patched)
    try:
        with pytest.raises(AssertionError, match="词族映射"):
            importlib.reload(registry)
    finally:
        # 恢复正常态：先撤销 patch（teardown 前模块须已复原），再重载回原版
        monkeypatch.undo()
        importlib.reload(registry)
