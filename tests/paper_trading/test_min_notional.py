"""MIN_NOTIONAL 装载三来源测试（change paper-depletion-terminal
任务 1）：优先级 env>端点>内置；端点失败回退常量+日志留痕。

替身注入形制：fetch_exchange_info/env 均为函数注入面（真实网络
不进单测——生产装载在 app lifespan，两注入点即生产链路的两数据源）。
"""
from __future__ import annotations

import logging

import pytest

pytestmark = [pytest.mark.unit]

from core.data.fapi.stream import FAPI_SYMBOLS
from core.paper_trading import min_notional as mn
from core.paper_trading.constants import MIN_NOTIONAL_BUILTIN


@pytest.fixture(autouse=True)
def _restore_table():
    """用例隔离：装载表回内置基线（模块级单点不跨用例泄漏）。"""
    yield
    mn.MIN_NOTIONAL.clear()
    mn.MIN_NOTIONAL.update(MIN_NOTIONAL_BUILTIN)


def _info_with(symbol_values: dict) -> dict:
    """exchangeInfo 替身（只含 FAPI_SYMBOLS 的 symbols 段）。"""
    return {
        "symbols": [
            {"symbol": sym, "filters": [
                {"filterType": "MIN_NOTIONAL",
                 "notional": str(v)}]}
            for sym, v in symbol_values.items()
        ]
    }


class TestThreeSourcePriority:
    """①三来源优先级：环境变量>端点拉取>内置常量。"""

    def test_endpoint_overrides_builtin(self):
        """端点值覆写内置（无 env 时端点为事实源）。"""
        mn.load_min_notional(
            fetch_exchange_info=lambda: _info_with(
                {"BTCUSDT": 111.0, "ETHUSDT": 222.0}),
            env={})
        assert mn.MIN_NOTIONAL == {"BTCUSDT": 111.0, "ETHUSDT": 222.0}

    def test_env_overrides_endpoint_and_builtin(self):
        """env 覆写最高优（端点+env 并存时 env 赢）。"""
        mn.load_min_notional(
            fetch_exchange_info=lambda: _info_with(
                {"BTCUSDT": 111.0, "ETHUSDT": 222.0}),
            env={"FAPI_MIN_NOTIONAL_BTCUSDT": "333.0"})
        assert mn.MIN_NOTIONAL == {"BTCUSDT": 333.0, "ETHUSDT": 222.0}

    def test_env_only_over_builtin(self):
        """env 单键覆写，未覆写键保持内置。"""
        mn.load_min_notional(
            fetch_exchange_info=lambda: {},
            env={"FAPI_MIN_NOTIONAL_ETHUSDT": "9.0"})
        assert mn.MIN_NOTIONAL["ETHUSDT"] == 9.0
        assert mn.MIN_NOTIONAL["BTCUSDT"] == \
            MIN_NOTIONAL_BUILTIN["BTCUSDT"]

    def test_partial_endpoint_falls_back_per_symbol(self):
        """端点只含部分币种：缺键逐币回退内置（回退链是逐键兜底）。"""
        mn.load_min_notional(
            fetch_exchange_info=lambda: _info_with({"BTCUSDT": 77.0}),
            env={})
        assert mn.MIN_NOTIONAL["BTCUSDT"] == 77.0
        assert mn.MIN_NOTIONAL["ETHUSDT"] == \
            MIN_NOTIONAL_BUILTIN["ETHUSDT"]


class TestEndpointFailureFallback:
    """②端点失败（注入异常）回退常量路径可用+回退路径日志断言。"""

    def test_fetch_exception_falls_back_with_warning(self, caplog):
        def _boom():
            raise RuntimeError("network unreachable")

        logging.getLogger().addHandler(caplog.handler)  # fileConfig 坑位
        with caplog.at_level(logging.WARNING, logger="paper_runtime"):
            table = mn.load_min_notional(
                fetch_exchange_info=_boom, env={})
        assert table == dict(MIN_NOTIONAL_BUILTIN), \
            "回退常量路径可用（任务行为不中断）"
        assert "回退内置常量" in caplog.text, "回退事实日志留痕"

    def test_no_filter_symbol_falls_back(self):
        """端点可达但币种缺 MIN_NOTIONAL filter→该键回退内置。"""

        def _partial():
            return {"symbols": [
                {"symbol": "BTCUSDT", "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"}]}]}

        mn.load_min_notional(fetch_exchange_info=_partial, env={})
        assert mn.MIN_NOTIONAL["BTCUSDT"] == \
            MIN_NOTIONAL_BUILTIN["BTCUSDT"]

    def test_env_survives_endpoint_failure(self):
        """端点失败时 env 覆写仍然生效（覆写与回退独立并存）。"""
        def _boom():
            raise RuntimeError("down")

        table = mn.load_min_notional(
            fetch_exchange_info=_boom,
            env={"FAPI_MIN_NOTIONAL_BTCUSDT": "42.0"})
        assert table["BTCUSDT"] == 42.0
        assert table["ETHUSDT"] == MIN_NOTIONAL_BUILTIN["ETHUSDT"]


class TestBuiltinValueRecord:
    """③内置值域与核实记录面：两币种值在场（核实记录见 constants
    注释——随实施提交说明留档）。"""

    def test_builtin_covers_fapi_symbols(self):
        assert set(MIN_NOTIONAL_BUILTIN) == set(FAPI_SYMBOLS)
        assert all(v > 0 for v in MIN_NOTIONAL_BUILTIN.values())

    def test_default_table_is_builtin_copy(self):
        """模块级表缺省形态=内置副本（未装载即保持——拦截判定恒有值）。"""
        import importlib

        reloaded = importlib.reload(mn)
        assert reloaded.MIN_NOTIONAL == dict(MIN_NOTIONAL_BUILTIN)
        assert reloaded.MIN_NOTIONAL is not MIN_NOTIONAL_BUILTIN, \
            "副本非同对象（装载写回不 mutate 内置常量）"


class TestEnvOverrideSanitization:
    """C4-01/S-1：env 覆写畸形值校验（解析单点）——不可解析/NaN/
    inf/负/零值 warning 丢弃该键回退装载链（NaN 静默失效枯竭兜底
    与 abc 直穿崩启动两类实证面封死）。"""

    def test_unparseable_value_dropped_with_warning(self, caplog):
        from core.data.fapi.rest import fapi_min_notional_overrides

        logging.getLogger().addHandler(caplog.handler)  # fileConfig 坑位
        with caplog.at_level(logging.WARNING):
            got = fapi_min_notional_overrides(env={
                "FAPI_MIN_NOTIONAL_BTCUSDT": "abc",
                "FAPI_MIN_NOTIONAL_ETHUSDT": "20"})
        assert got == {"ETHUSDT": 20.0}, "不可解析键丢弃"
        assert "不可解析" in caplog.text

    @pytest.mark.parametrize("bad", [
        "nan", "inf", "-inf", "-5", "0"])
    def test_non_finite_or_non_positive_dropped(self, bad, caplog):
        from core.data.fapi.rest import fapi_min_notional_overrides

        logging.getLogger().addHandler(caplog.handler)
        with caplog.at_level(logging.WARNING):
            got = fapi_min_notional_overrides(env={
                "FAPI_MIN_NOTIONAL_BTCUSDT": bad})
        assert got == {}, f"{bad} 丢弃不装载"
        assert "非有限正值" in caplog.text or "不可解析" in caplog.text

    def test_load_with_bad_env_falls_back_cleanly(self, caplog):
        """装载链端到端：畸形 env 键丢弃后该币回退端点/内置（不崩
        启动、不带病装载）。"""
        logging.getLogger().addHandler(caplog.handler)
        with caplog.at_level(logging.WARNING):
            table = mn.load_min_notional(
                fetch_exchange_info=lambda: {},
                env={"FAPI_MIN_NOTIONAL_BTCUSDT": "nan",
                     "FAPI_MIN_NOTIONAL_ETHUSDT": "55"})
        assert table["BTCUSDT"] == MIN_NOTIONAL_BUILTIN["BTCUSDT"], \
            "NaN 覆写丢弃→内置回退"
        assert table["ETHUSDT"] == 55.0, "合法覆写照常生效"


class TestDefaultFetchAssembly:
    """CARD-04 装配面：缺省 fetch 闭包经 fapi_env 传 base_url/proxies
    （修复前无参构造——FAPI_PROXY/FAPI_REST_BASE 在此装配点不生效）。"""

    def test_default_fetch_env_params_passed(self, monkeypatch):
        from core.data.fapi import rest as rest_mod

        built: list[dict] = []

        class FakeClient:
            def __init__(self, **kwargs):
                built.append(kwargs)

            def fetch_exchange_info(self):
                return {}

        monkeypatch.setattr(rest_mod, "FapiRestClient", FakeClient)
        monkeypatch.setenv("FAPI_PROXY", "http://proxy:1080")
        monkeypatch.setenv("FAPI_REST_BASE", "https://fapi.example.com")
        mn.load_min_notional(fetch_exchange_info=None)  # 缺省闭包=装配点
        assert built[0]["proxies"] == {
            "http": "http://proxy:1080", "https": "http://proxy:1080"}
        assert built[0]["base_url"] == "https://fapi.example.com"
