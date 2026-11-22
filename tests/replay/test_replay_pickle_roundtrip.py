"""T2-g：跨进程异常 pickle 往返零漂移单元（fix-replay-gap-alignment 2.2）。

回放进程池 worker 内抛出的自定义结构化异常经 pickle 回传父进程
（concurrent.futures _ExceptionWithTraceback 打包），重建所需全部
构造信息须完整携带——现状 args 单元素（super 只收 message）使
unpickle 调 ReplayError(message) 缺参 TypeError 崩池管理线程（E3
实测 BrokenProcessPool+整池连坐）。本单元直证 __reduce__ 两类
形制的重建等价（零依赖可独立先行——落位 tester v0.8 §二）。

进程内语义零漂移约束：str(exc)/args/全局 handler 消费面不经
__reduce__（仅 pickle 路径），往返后对象与原对象逐项等价。
"""
from __future__ import annotations

import pickle

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError
from core.replay.runner import ReplayError


def _factories():
    return [
        ReplayError("REPLAY_LOAD_FAILED", "回放装载失败: 数据不足",
                    "task_id=t_probe"),
        ReplayError("REPLAY_TASK_PREEMPTED", "回放任务行启动前已被改写"),
        ReplayError("REPLAY_PREPARE_FAILED", "执行器装载校验意外未过",
                    None),
        ResearchError("RESEARCH_EXECUTION_FAILED", "回放执行段异常: X",
                      status=500, detail={"task_id": "t_probe2"}),
        ResearchError("RESEARCH_DATA_INSUFFICIENT", "基因级周期轴缺失",
                      status=422),
    ]


class TestT2gPickleRoundtrip:
    """序列化往返零漂移：重建对象 str/args/构造参数/结构化属性族与
    原对象逐项等价（__reduce__ 单点判别力锚——修复前 unpickle 即
    TypeError）。"""

    @pytest.mark.parametrize("exc", _factories(), ids=lambda e: e.code)
    def test_roundtrip_attributes_equivalent(self, exc):
        restored = pickle.loads(pickle.dumps(exc))
        assert type(restored) is type(exc)
        assert str(restored) == str(exc), "str(exc) 跨往返零漂移"
        assert restored.args == exc.args, (
            "args 形制不变（super 单元素——进程内消费面零漂移前提）")
        assert restored.code == exc.code
        assert restored.message == exc.message
        assert restored.detail == exc.detail
        if isinstance(exc, ResearchError):
            assert restored.status == exc.status, (
                "keyword-only status 经 3-元组 state 形制恢复")

    def test_roundtrip_replay_error_positional_rebuild(self, tmp_path):
        """ReplayError 三元位置参全量重建（detail=None 边界也显式传）。"""
        exc = ReplayError("REPLAY_TASK_PREEMPTED",
                          "回放任务行启动前已被外部改写，放弃驱动",
                          detail="task_id=x")
        restored = pickle.loads(pickle.dumps(exc))
        assert isinstance(restored, ReplayError)
        # 重建对象保持可再序列化（嵌套往返稳定）
        again = pickle.loads(pickle.dumps(restored))
        assert str(again) == str(exc)

    def test_roundtrip_research_error_dict_state(self):
        """ResearchError 3-元组 state 形式（__dict__.update 恢复）——
        kwargs 参不占位置参的形制正确性。"""
        exc = ResearchError("RESEARCH_EXECUTION_FAILED",
                            "回放执行段异常: RuntimeError: boom",
                            status=500, detail={"task_id": "z"})
        blob = pickle.dumps(exc)
        restored = pickle.loads(blob)
        # reduce 载荷形制断言（构造信息完整携带的直接证据）
        reduce_cls, reduce_args, state = exc.__reduce__()
        assert reduce_cls is ResearchError
        assert reduce_args == ("RESEARCH_EXECUTION_FAILED",
                               "回放执行段异常: RuntimeError: boom")
        assert state == {"status": 500, "detail": {"task_id": "z"}}
        assert restored.detail == {"task_id": "z"}
