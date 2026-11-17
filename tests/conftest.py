"""Shared test fixtures for MyQuant test suite.

All data generation is delegated to tests.helpers.data_factory.
Individual test files may define local fixtures with custom parameters.
"""

import sys as _sys
from pathlib import Path

import pytest

# worktree 双身份解析（retire-legacy 终波五目录 conftest 合并单点）：
# pytest 从 worktree 根启动时 prepend 短路使仓库父目录不进 sys.path，
# `from MyQuant.api.app import create_app` 双身份导入面全灭。三种布局
# 探测——主 checkout（仓库父目录直含 MyQuant 包）/worktree 内挂
# worktrees/MyQuant 自引用/共享 .claude/worktrees/MyQuant 引用，取实含
# MyQuant 的候选目录注入；幂等无害。原五目录各置一份的同款补丁因顶层
# conftest 名撞车（劫持 mcpserver 目录裸 `from conftest import
# _FakeMcp` 解析，xdist 全量 27 红）合并至此。
_HERE = Path(__file__).resolve()
for _cand in (_HERE.parents[2], _HERE.parents[1] / "worktrees",
              _HERE.parents[3]):
    if (_cand / "MyQuant").exists() and str(_cand) not in _sys.path:
        _sys.path.insert(0, str(_cand))


# Re-export data_factory helpers as fixtures for backward compatibility.
# New tests should import directly from tests.helpers.data_factory.
from tests.helpers.data_factory import (
    make_ohlcv,
    make_dna,
    make_engine,
    make_enhanced_df,
    make_mtf_dna,
    make_ema_dna,
)


# ---------------------------------------------------------------------------
# Shared infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with a minimal BTCUSDT_4h parquet.

    Most API/runner tests need at least one parquet file to avoid "no data" errors.
    Tests that need larger/custom data can override this fixture locally.
    """
    import pandas as pd

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dummy_df = pd.DataFrame(
        {"open": [60000], "high": [61000], "low": [59000],
         "close": [60500], "volume": [100]},
        index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
    )
    dummy_df.to_parquet(data_dir / "BTCUSDT_4h.parquet")
    return data_dir


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a temporary SQLite database path."""
    return tmp_path / "test.db"


@pytest.fixture
def api_client(db_path: Path, tmp_data_dir: Path):
    """Create a FastAPI TestClient with test configuration.

    Yields the client within a lifespan context so startup/shutdown events fire.
    """
    from MyQuant.api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app(db_path=db_path, data_dir=tmp_data_dir)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fapi_stream_test_bypass(monkeypatch):
    """fapi 行情流装配全域旁路（FAPI_STREAM_DISABLED 置位）。

    app lifespan 的 WS 流与建库线程装配含真实网络依赖；测试全域默认
    旁路（健康面走 K2 未启动缺省语义），装配专项测试经
    monkeypatch.delenv 显式开启或注入 fapi_stream_factory 替身驱动。
    """
    monkeypatch.setenv("FAPI_STREAM_DISABLED", "1")
    yield


@pytest.fixture(autouse=True)
def _clear_indicator_cache_between_tests():
    """Clear the global indicator column cache before and after every test.

    core.strategy.executor._indicator_column_cache is a module-level dict keyed
    by id(df) that is never auto-cleared. Without isolation, indicator Series
    leak across tests, and Python id reuse can make a new df hit a stale key
    (the root cause of several order-dependent flaky failures). This is
    belts-and-suspenders with the dna_to_signal_set entry-level clear.
    """
    from core.strategy.executor import clear_indicator_cache
    clear_indicator_cache()
    yield
    clear_indicator_cache()
