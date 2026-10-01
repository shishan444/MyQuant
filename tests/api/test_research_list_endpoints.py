"""研究域列表端点特征网（fix/mcp-research-upstream-review）。

修复期临时安全网：钉住 GET /hypotheses、/log、/feature-requests
三端点的分页钳制、total=当前页长度语义、过滤组合、404 错误通道与
translate 无 body 分支。/feature-requests 原无钳制的现状用例已在
CARD-05（分页统一 clamp）后改写为钳制行为。
"""

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository import research as rr

CONDITIONS = {
    "when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
    "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
}


def _register(client, start: str, statement: str = "特征网假设"):
    return client.post("/api/research/hypotheses", json={
        "conditions": CONDITIONS, "statement": statement,
        "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": start, "end": "2024-03-01T00:00:00Z"}]})


class TestHypothesesPagination:
    """钳制语义：page<1→1；page_size 越界（<1 或 >200）→50；total=当前页长度。"""

    def test_page_zero_clamped_to_one(self, api_client):
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses",
                              params={"page": 0}).json()
        assert body["page"] == 1 and body["page_size"] == 50
        assert len(body["items"]) == 1 and body["total"] == 1

    @pytest.mark.parametrize("page_size", [0, -1, 201])
    def test_pagesize_out_of_range_clamped_to_50(self, api_client, page_size):
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses",
                              params={"page_size": page_size}).json()
        assert body["page_size"] == 50, f"page_size={page_size} 应钳制为 50"

    def test_pagesize_cap_boundary_kept(self, api_client):
        body = api_client.get("/api/research/hypotheses",
                              params={"page_size": 200}).json()
        assert body["page_size"] == 200, "上限 200 本身合法，不应被改写"

    def test_beyond_last_page_empty_with_zero_total(self, api_client):
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses",
                              params={"page": 99}).json()
        assert body["items"] == [] and body["total"] == 0

    def test_pagination_slices_items(self, api_client):
        for i in range(3):
            resp = _register(api_client, f"2024-01-0{i + 1}T00:00:00Z")
            assert resp.status_code == 200, resp.text

        page1 = api_client.get("/api/research/hypotheses",
                               params={"page_size": 2}).json()
        page2 = api_client.get("/api/research/hypotheses",
                               params={"page": 2, "page_size": 2}).json()
        assert len(page1["items"]) == 2 and page1["total"] == 2
        assert len(page2["items"]) == 1 and page2["total"] == 1
        ids1 = {h["hypothesis_id"] for h in page1["items"]}
        ids2 = {h["hypothesis_id"] for h in page2["items"]}
        assert not ids1 & ids2, "分页窗口不应重叠"

    def test_status_filter(self, api_client):
        r = _register(api_client, "2024-01-01T00:00:00Z").json()
        active = api_client.get("/api/research/hypotheses",
                                params={"status": "active"}).json()
        assert any(h["hypothesis_id"] == r["hypothesis_id"]
                   for h in active["items"])
        refuted = api_client.get("/api/research/hypotheses",
                                 params={"status": "refuted"}).json()
        assert refuted["items"] == []

    def test_items_decode_structured_json_fields(self, api_client):
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses").json()
        item = body["items"][0]
        assert isinstance(item["conditions"], dict), "conditions 库存 JSON 串应在响应解码"
        assert isinstance(item["data_ranges"], list)


class TestResearchLogEndpoint:
    """entry_type / hypothesis_id 过滤组合与脏 data_ranges 容错。"""

    def test_entry_type_filter_hit_and_miss(self, api_client):
        r = _register(api_client, "2024-01-01T00:00:00Z").json()
        hit = api_client.get("/api/research/log",
                             params={"entry_type": "register"}).json()
        assert any(e["hypothesis_id"] == r["hypothesis_id"]
                   for e in hit["items"])
        miss = api_client.get("/api/research/log",
                              params={"entry_type": "nope"}).json()
        assert miss["items"] == [] and miss["total"] == 0

    def test_combined_filter(self, api_client):
        r = _register(api_client, "2024-01-01T00:00:00Z").json()
        body = api_client.get("/api/research/log", params={
            "hypothesis_id": r["hypothesis_id"],
            "entry_type": "register"}).json()
        assert len(body["items"]) == 1
        empty = api_client.get("/api/research/log", params={
            "hypothesis_id": "hyp_none", "entry_type": "register"}).json()
        assert empty["items"] == []

    def test_page_clamped_and_beyond_tail(self, api_client):
        _register(api_client, "2024-01-01T00:00:00Z")
        clamped = api_client.get("/api/research/log",
                                 params={"page": 0}).json()
        assert clamped["page"] == 1
        tail = api_client.get("/api/research/log",
                              params={"page": 99}).json()
        assert tail["items"] == [] and tail["total"] == 0

    def test_malformed_data_ranges_passed_through_verbatim(self, api_client, db_path):
        """库内 data_ranges 非法 JSON：端点不炸，原样字符串回传（容错分支）。"""
        rr.append_research_log(db_path, hypothesis_id=None,
                               entry_type="analysis",
                               data_ranges="not-json-{broken")
        body = api_client.get("/api/research/log",
                              params={"entry_type": "analysis"}).json()
        dirty = [e for e in body["items"]
                 if e["data_ranges"] == "not-json-{broken"]
        assert dirty, "脏 data_ranges 应原样回传且不影响端点可用性"


class TestFeatureRequestsEndpoint:
    """分页钳制与 /hypotheses、/log 同款（CARD-05 后统一）：
    page<1→1；page_size 出 [1,200]→50；total=当前页长度。"""

    def test_create_and_list_roundtrip(self, api_client):
        for i in range(2):
            resp = api_client.post("/api/research/feature-requests", json={
                "title": f"需求{i}", "payload": {"k": i}})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["request_id"].startswith("fr_")
            assert body["status"] == "open"

        listed = api_client.get("/api/research/feature-requests").json()
        assert len(listed["items"]) == 2 and listed["total"] == 2
        payloads = {r["payload"]["k"] for r in listed["items"]}
        assert payloads == {0, 1}, "payload 库存 JSON 串应在响应解码"

    def test_status_filter(self, api_client):
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        open_fr = api_client.get("/api/research/feature-requests",
                                 params={"status": "open"}).json()
        assert len(open_fr["items"]) == 1
        closed = api_client.get("/api/research/feature-requests",
                                params={"status": "closed"}).json()
        assert closed["items"] == []

    @pytest.mark.parametrize("page", [0, -1])
    def test_non_positive_page_clamped_to_one(self, api_client, page):
        """page<=0 钳制为 1：回第一页数据且 echo page=1（CARD-05 前为
        负 OFFSET 回首页 + echo 原值的不一致形制）。"""
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page": page}).json()
        assert len(body["items"]) == 1 and body["total"] == 1
        assert body["page"] == 1

    def test_page_size_zero_clamped_to_default(self, api_client):
        """page_size=0 钳制为 50 缺省（CARD-05 前为 LIMIT 0 假空页）。"""
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page_size": 0}).json()
        assert body["page_size"] == 50 and len(body["items"]) == 1

    def test_page_size_over_cap_clamped_to_default(self, api_client):
        """page_size 超上限 200 钳制为 50 缺省（CARD-05 前原值透传）。"""
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page_size": 201}).json()
        assert body["page_size"] == 50 and len(body["items"]) == 1

    def test_beyond_last_page_empty(self, api_client):
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page": 99}).json()
        assert body["items"] == [] and body["total"] == 0


class TestNotFoundChannels:
    """三处 404 错误通道的结构化 code。"""

    def test_hypothesis_not_found(self, api_client):
        resp = api_client.get("/api/research/hypotheses/hyp_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_NOT_FOUND"

    def test_validation_report_not_found(self, api_client):
        resp = api_client.get("/api/research/validation/st_nope/report")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_REPORT_NOT_FOUND"

    def test_analysis_task_not_found(self, api_client):
        resp = api_client.get("/api/research/analysis/tasks/ant_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_TASK_NOT_FOUND"


class TestTranslateBodyOmitted:
    def test_translate_without_body_reaches_service_layer(self, api_client):
        """payload 可整体省略（None 缺省）：路由层不炸，404 由服务层给出。"""
        resp = api_client.post("/api/research/translate/hyp_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_NOT_FOUND"
