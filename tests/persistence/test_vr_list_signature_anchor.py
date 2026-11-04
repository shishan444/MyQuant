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
    """四处调用点形制锚（B1.6 勘误清单——tester-2 修正：原版仅断言
    模块可导入与仓储 def 在场，调用面零适配未被证明，属断言虚标）。

    每处以源文本锚定调用形制：调用被改写（换函数/改直调为 lambda/
    传入新参改写调用面）即红，与上两签名锚构成「形参只增不改、
    调用方零适配」的完整实证。"""
    import api.routes.research.records_list as rl
    import api.routes.research.validation as val
    import core.analysis.validation_service as vs
    import core.analysis.hypothesis_service as hs
    # 端点接线调用（records_list.py:126——全 keyword 形制）
    assert "rows = rr.list_validation_records(" in inspect.getsource(rl)
    # 档案端点调用（validation.py:167——keyword+limit=200 单页全量）
    assert ("records = rr.list_validation_records(" in
            inspect.getsource(val))
    # iter_all_pages 消费两处（validation_service.py:1090/
    # hypothesis_service.py:1283——lambda off, lim 形态）
    assert ("lambda off, lim: rr.list_validation_records(" in
            inspect.getsource(vs))
    assert ("lambda off, lim: rr.list_validation_records(" in
            inspect.getsource(hs))
