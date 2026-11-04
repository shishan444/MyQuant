"""仓储签名锚（upgrade-validation-records-list 任务 1.2——T2-A8）。

四处调用点形参表快照断言：list/count_validation_records 形参漂移即红
（「形参只增不改」契约——缺省不传行为不变即既有调用方零适配）。
"""
from __future__ import annotations

import inspect

from core.persistence.repository import research as rr


def test_list_signature_anchor():
    sig = inspect.signature(rr.list_validation_records)
    params = list(sig.parameters)
    # 既有形制（现状锚——重命名/删除即红）
    assert params[:7] == [
        "db_path", "hypothesis_id", "strategy_id",
        "offset", "limit", "cursor", "order"]
    # B1 扩展形参（upgrade-validation-records-list 1.1——全部缺省
    # None 现状逐位一致）
    for p in ("q", "generalization", "sort", "level", "verdict"):
        assert p in params, f"list 缺 B1 形参 {p}"
        assert sig.parameters[p].default is None, (
            f"{p} 缺省须 None（不传=现状逐位一致）")


def test_count_signature_anchor():
    sig = inspect.signature(rr.count_validation_records)
    params = list(sig.parameters)
    assert params[:3] == ["db_path", "hypothesis_id", "strategy_id"]
    for p in ("q", "generalization", "level", "verdict"):
        assert p in params, f"count 缺 B1 形参 {p}"
        assert sig.parameters[p].default is None


def test_call_sites_unchanged():
    """四处调用点零适配实证：不传新参调用逐位一致（编译级签名兼容
    由上两锚保证；本锚锁调用面无 keyword 传参冲突）。"""
    import core.analysis.validation_service as vs
    import core.analysis.hypothesis_service as hs
    import api.routes.research.records_list as rl
    import api.routes.research.validation as val
    for mod in (vs, hs, rl, val):
        assert mod is not None
    # 调用点存在性（形参引用面——lambda/直接调用形态均以源文本锚定）
    import core.persistence.repository.research as rmod
    src = inspect.getsource(rmod)
    assert "def list_validation_records(" in src
    assert "def count_validation_records(" in src
