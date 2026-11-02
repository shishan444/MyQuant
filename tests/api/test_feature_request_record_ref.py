"""A1 反馈引用键约定测试件（add-hypothesis-cards 任务 6——零生产代码）。

证 payload 结构化键 record_id 经既有提交-读回链路自然可见：
POST /feature-requests 携键 → 200 四键响应 → GET 列表读回 payload
原样含键；MCP 面（list_feature_requests=HTTP 透传 mcpserver/tools.py:245）
同 URL 读回同条（A2-3 经 api client 层承载）。不校验存在性——异常
record_id 仍 200（慢通道「只保证持久化与可查」语义不变）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from core.analysis.executor import ensure_research_tables
    from api.app import create_app
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    app = create_app(db_path=db, data_dir=tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestFeedbackRecordRef:
    """A2-1/A2-2/A2-3。"""

    def test_post_with_record_id_four_key_response(self, client):
        """A2-1：携 record_id 提交 → 200 四键体（request_id/title/
        status/created_at）——端点零改动纯约定。"""
        r = client.post("/api/research/feature-requests", json={
            "title": "验证记录 rec_xx1 结论存疑",
            "payload": {"record_id": "rec_xx1",
                        "comment": "第 12 笔交易的止损价与 K 线不符"}})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"request_id", "title", "status", "created_at"}
        assert body["status"] == "open"

    def test_get_reads_back_record_id_key(self, client):
        """A2-2：GET 列表读回 payload 原样含 record_id 键。"""
        post = client.post("/api/research/feature-requests", json={
            "title": "t",
            "payload": {"record_id": "rec_ref02", "comment": "意见"}}).json()
        got = client.get("/api/research/feature-requests").json()
        items = got["items"] if isinstance(got, dict) else got
        row = next(i for i in items if i["request_id"] == post["request_id"])
        assert row["payload"]["record_id"] == "rec_ref02"
        assert row["payload"]["comment"] == "意见"

    def test_mcp_face_same_url_reads_back(self, client):
        """A2-3：MCP 面 list_feature_requests=HTTP 透传（同 URL
        /api/research/feature-requests）——同条经同一通道读回。"""
        post = client.post("/api/research/feature-requests", json={
            "title": "t2",
            "payload": {"record_id": "rec_ref03"}}).json()
        # MCP 透传消费面：同一 GET 端点即 AI 下轮研究读到的数据
        got = client.get("/api/research/feature-requests").json()
        items = got["items"] if isinstance(got, dict) else got
        row = next(i for i in items if i["request_id"] == post["request_id"])
        assert row["payload"]["record_id"] == "rec_ref03"

    def test_nonexistent_record_id_still_accepted(self, client):
        """约定边界：不引入存在性校验——异常 record_id 仍 200（慢通道
        只保证持久化与可查）。"""
        r = client.post("/api/research/feature-requests", json={
            "title": "t3", "payload": {"record_id": "rec_ghost_404"}})
        assert r.status_code == 200

    def test_malformed_record_id_value_still_accepted(self, client):
        """Scenario「引用不校验存在性」WHEN 面完整覆盖：格式异常值
        （非 rec_ 前缀字符串/非字符串类型）同样提交成功——payload 为
        自由 JSON，消费侧按键自行解读，端点不做类型校验。"""
        for bad in ("not-a-ref", 12345, {"nested": True}):
            r = client.post("/api/research/feature-requests", json={
                "title": "t4", "payload": {"record_id": bad}})
            assert r.status_code == 200, f"record_id={bad!r} 不得 422"
        # 格式异常值原样持久化可查（读回不捏造不清洗）
        got = client.get("/api/research/feature-requests").json()
        items = got["items"] if isinstance(got, dict) else got
        # dict 值不可哈希——仅收集 str/int 形态的 record_id 值
        vals = {row["payload"].get("record_id") for row in items
                if isinstance(row["payload"].get("record_id"), (str, int))}
        assert {"not-a-ref", 12345} <= vals
