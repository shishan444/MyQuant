"""缺省数据目录单源化等价四面与读写同源回环（fix-data-dir-single-source A-2）。

四面（tasks.md A-2 完成标准）：
- a. create_app() 缺省装配：app.state.data_dir == default_data_dir()
  （setenv tmp 下 == tmp——FAPI_DATA_DIR 既有通道语义经单点保留）；
- b. 四引擎缺省（engine/rule_engine/mtf/scene_engine 各一）：拦截
  core.data.mtf_loader.find_parquet 实参，断言缺省解析到
  default_data_dir()（四文件原各自 __file__ 锚定链已收编）；
- c. PaperScheduler._default_data_dir() == default_data_dir()
  （静态方法形制保留、方法体委托单点）；
- d. updater 缺省写入：setenv tmp 后落 tmp 目录（chdir 漂移面下
  tmp 内无 data/ 生成——cwd 相对写副作用消除）；写一读一回环——
  同 env 下写入目录 == 引擎缺省读取目录（default_data_dir() 同点）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.data.fapi.rest import default_data_dir
from core.data.updater import update_funding_data


class _Intercepted(Exception):
    """find_parquet 拦截哨兵——记录 data_dir 实参后中断装载链。"""


@pytest.fixture
def intercept_finder(monkeypatch):
    """拦截 mtf_loader.find_parquet（四引擎缺省装载的共同触点）。"""
    seen: list[Path] = []

    def _fake_find_parquet(data_dir, symbol, tf):
        seen.append(Path(data_dir))
        raise _Intercepted

    monkeypatch.setattr("core.data.mtf_loader.find_parquet", _fake_find_parquet)
    return seen


class TestCreateAppDefault:
    """a. 装配面：create_app 缺省挂载与解析单点一致。"""

    def test_state_data_dir_equals_single_source(
            self, monkeypatch, tmp_path, db_path):
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))
        from fastapi.testclient import TestClient
        from MyQuant.api.app import create_app

        app = create_app(db_path=db_path)
        with TestClient(app):
            assert Path(app.state.data_dir) == default_data_dir() == tmp_path


class TestEngineDefaults:
    """b. 四引擎缺省：装载实参经单点解析（setenv tmp 下 == tmp）。"""

    def test_validate_hypothesis_default(self, monkeypatch, tmp_path,
                                         intercept_finder):
        from core.validation.engine import validate_hypothesis
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))

        with pytest.raises(_Intercepted):
            validate_hypothesis(
                "NOEXIST", "4h", "2024-01-01", "2025-01-01",
                when_conditions=[{"subject": "close", "action": "gt",
                                  "target": 0}],
                then_conditions=[{"action": "rise", "target": 1}],
            )
        assert intercept_finder[0].resolve() == tmp_path.resolve()

    def test_evaluate_rules_default(self, monkeypatch, tmp_path,
                                    intercept_finder):
        from core.validation.rule_engine import evaluate_rules
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))

        with pytest.raises(_Intercepted):
            evaluate_rules(
                "NOEXIST", "4h", "2024-01-01", "2025-01-01",
                entry_conditions=[{"subject": "close", "action": "lt",
                                   "target": 95}],
                exit_conditions=[{"subject": "close", "action": "gt",
                                  "target": 105}],
            )
        assert intercept_finder[0].resolve() == tmp_path.resolve()

    def test_load_mtf_data_default(self, monkeypatch, tmp_path,
                                   intercept_finder):
        from core.validation.mtf import load_mtf_data
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))

        with pytest.raises(_Intercepted):
            load_mtf_data("NOEXIST", ["4h"])
        assert intercept_finder[0].resolve() == tmp_path.resolve()

    def test_scene_verification_default(self, monkeypatch, tmp_path,
                                        intercept_finder):
        from core.validation.scene.scene_engine import run_scene_verification
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))

        with pytest.raises(_Intercepted):
            run_scene_verification(
                symbol="NOEXIST", timeframe="4h", scene_type="volume_spike")
        assert intercept_finder[0].resolve() == tmp_path.resolve()


class TestSchedulerDefault:
    """c. 调度器缺省：静态方法形制保留、方法体委托单点。"""

    def test_default_data_dir_delegates(self, monkeypatch, tmp_path):
        from core.paper_trading.runtime.scheduler import PaperScheduler
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))

        assert PaperScheduler._default_data_dir() == default_data_dir()
        assert PaperScheduler._default_data_dir() == tmp_path


class TestUpdaterWriteReadLoop:
    """d. 写一读一回环：updater 缺省写入目录 == 引擎缺省读取目录。"""

    def test_write_dir_equals_read_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # cwd 漂移面：旧 cwd 相对行为会写 tmp/data/
        idx = pd.date_range("2024-01-01", periods=5, freq="8h", tz="UTC")
        fake = pd.DataFrame(
            {"funding_rate": np.linspace(0.0001, 0.0002, 5)}, index=idx)
        monkeypatch.setattr(
            "core.data.updater.fetch_funding_history",
            lambda **kwargs: fake)

        update_funding_data("BTCUSDT")

        # 写入侧：缺省落 setenv 目录（真实写 tmp，不触工程根）
        assert (tmp_path / "BTCUSDT_funding.parquet").exists()
        # cwd 相对写副作用消除：漂移 cwd 下无 data/ 生成
        assert not (tmp_path / "data").exists()
        # 回环：读取侧缺省（b 组已证引擎装载实参=单点解析）与写入同点
        assert default_data_dir() == tmp_path
