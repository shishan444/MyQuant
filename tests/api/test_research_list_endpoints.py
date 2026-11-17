"""研究域列表端点特征网（fix/mcp-research-upstream-review）。

修复期临时安全网：钉住 GET /hypotheses、/log、/feature-requests
三端点的分页契约（CARD-05 统一 clamp；CARD-07 起 desc 最新在前 +
cursor keyset 续页 + total=过滤口径真总数 + next_cursor）、过滤
组合、404 错误通道与 translate 无 body 分支。
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
        "strategy_params": {"stop_loss": 0.05}, "conditions": CONDITIONS, "statement": statement,
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

    def test_beyond_last_page_empty_with_true_total(self, api_client):
        """CARD-07 后：超尾页 items 空，total 为过滤口径真总数（非 0），
        next_cursor 空页为 None。"""
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses",
                              params={"page": 99}).json()
        assert body["items"] == [] and body["total"] == 1
        assert body["next_cursor"] is None

    def test_pagination_slices_items(self, api_client):
        """CARD-07 后：total=真总数（不再随页变）；缺省 desc 最新在前。"""
        ids_in_order = []
        for i in range(3):
            resp = _register(api_client, f"2024-01-0{i + 1}T00:00:00Z")
            assert resp.status_code == 200, resp.text
            ids_in_order.append(resp.json()["hypothesis_id"])

        page1 = api_client.get("/api/research/hypotheses",
                               params={"page_size": 2}).json()
        page2 = api_client.get("/api/research/hypotheses",
                               params={"page": 2, "page_size": 2}).json()
        assert len(page1["items"]) == 2 and page1["total"] == 3
        assert len(page2["items"]) == 1 and page2["total"] == 3
        ids1 = [h["hypothesis_id"] for h in page1["items"]]
        ids2 = {h["hypothesis_id"] for h in page2["items"]}
        assert not set(ids1) & ids2, "分页窗口不应重叠"
        assert ids1[0] == ids_in_order[-1], "缺省 desc：最后登记（最新）在前"

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

    @pytest.mark.parametrize("page_size", [0, 201])
    def test_page_size_out_of_range_clamped(self, api_client, page_size):
        """/log 的 page_size 越界钳 50（闸门②补强：此前只测 page 维度）。"""
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/log",
                              params={"page_size": page_size}).json()
        assert body["page_size"] == 50 and len(body["items"]) == 1

    def test_page_clamped_and_beyond_tail(self, api_client):
        """CARD-07 后：超尾页 items 空、total=过滤口径真总数、cursor None。"""
        _register(api_client, "2024-01-01T00:00:00Z")
        clamped = api_client.get("/api/research/log",
                                 params={"page": 0}).json()
        assert clamped["page"] == 1
        tail = api_client.get("/api/research/log",
                              params={"page": 99}).json()
        assert tail["items"] == [] and tail["total"] == 1
        assert tail["next_cursor"] is None

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


class TestKeysetCursorPagination:
    """CARD-07 修复资产：三端点 keyset 续页契约。

    分页双轨：page/offset 翻页照旧；cursor（上一响应 next_cursor 回传）
    keyset 续页深翻页无漂移，提供时 page 被忽略；total 恒为过滤口径
    真总数；缺省 desc 最新在前；坏 cursor 结构化 422 不静默回首页。
    """

    def _create_five(self, api_client):
        ids = []
        for i in range(5):
            resp = _register(api_client, f"2024-01-0{i + 1}T00:00:00Z")
            assert resp.status_code == 200, resp.text
            ids.append(resp.json()["hypothesis_id"])
        return ids

    def test_cursor_supplies_page_is_ignored(self, api_client):
        """cursor 提供时 page 被忽略（offset=0）——page=99 不产生空页。"""
        self._create_five(api_client)
        first = api_client.get("/api/research/hypotheses",
                               params={"page_size": 2}).json()
        cursor = first["next_cursor"]

        with_page = api_client.get("/api/research/hypotheses", params={
            "page_size": 2, "cursor": cursor, "page": 99}).json()
        without_page = api_client.get("/api/research/hypotheses", params={
            "page_size": 2, "cursor": cursor}).json()
        assert with_page["items"] == without_page["items"]
        assert len(with_page["items"]) == 2, "page=99 未被采用（否则空页）"

    def test_malformed_cursor_rejected_structured(self, api_client):
        for bad in ("no-pipe", "lead|", "|tail", ""):
            resp = api_client.get("/api/research/hypotheses",
                                  params={"cursor": bad})
            assert resp.status_code == 422, f"cursor={bad!r} 应 422"
            detail = resp.json()["detail"]
            assert detail["code"] == "RESEARCH_PARAM_INVALID"
            assert detail["detail"]["legal_form"] == "created_at|id"

    @pytest.mark.parametrize(("endpoint", "setup"), [
        pytest.param("/api/research/hypotheses",
                     lambda c: [_register(c, f"2024-01-0{i + 1}T00:00:00Z")
                                for i in range(5)], id="假设"),
        pytest.param("/api/research/log",
                     lambda c: [_register(c, f"2024-01-0{i + 1}T00:00:00Z")
                                for i in range(5)], id="日志"),
    ])
    def test_cursor_walk_property_all_endpoints(self, api_client, endpoint,
                                                setup):
        """闸门④属性补强：cursor 翻页无重无漏性质三端点同持
        （_apply_keyset 共用，端点层 encode/decode/组装各自实现——
        性质须逐端点独立成立，不能由共用推定）。"""
        setup(api_client)

        seen = []
        cursor = None
        for _ in range(7):
            params = {"page_size": 2}
            if cursor is not None:
                params["cursor"] = cursor
            body = api_client.get(endpoint, params=params).json()
            assert body["total"] == 5, "total 恒为真总数"
            seen.extend((r.get("request_id") or r.get("hypothesis_id")
                         or r.get("log_id")) for r in body["items"])
            cursor = body["next_cursor"]
            if not body["items"]:
                break
        assert body["items"] == [] and cursor is None
        assert len(seen) == len(set(seen)) == 5, "翻页无重无漏"

    def test_hypotheses_and_log_same_contract(self, api_client):
        """契约同款抽查：desc 首页 + cursor 续页 + total 真总数。"""
        for i in range(3):
            resp = _register(api_client, f"2024-01-0{i + 1}T00:00:00Z")
            assert resp.status_code == 200, resp.text

        hyp1 = api_client.get("/api/research/hypotheses",
                              params={"page_size": 1}).json()
        assert hyp1["total"] == 3 and len(hyp1["items"]) == 1
        hyp2 = api_client.get("/api/research/hypotheses", params={
            "page_size": 1, "cursor": hyp1["next_cursor"]}).json()
        assert len(hyp2["items"]) == 1 and hyp2["total"] == 3
        assert (hyp2["items"][0]["hypothesis_id"]
                != hyp1["items"][0]["hypothesis_id"]), "续页前进"

        log1 = api_client.get("/api/research/log",
                              params={"entry_type": "register",
                                      "page_size": 2}).json()
        assert log1["total"] == 3, "entry_type 过滤口径真总数"
        assert len(log1["items"]) == 2 and log1["next_cursor"]


class TestCorruptedJsonPassthrough:
    """CARD-12 修复资产：库存 JSON 列损坏时端点不 500。

    四端点兜底语义：hypotheses/log/plugins 损坏保留原字符串（审计
    可读）；validation report 的 params 损坏归 {}（range=None——原始
    串不泄入人话摘要，同文件 _parse 先例形制）。
    """

    def test_hypotheses_list_and_detail_keep_raw(self, api_client, db_path):
        rr.save_hypothesis_with_log(
            db_path, hypothesis_id="hyp_dirty", conditions="{corrupted",
            conditions_hash="cd", statement="脏数据",
            data_ranges="not-json", ranges_hash="rd")
        listed = api_client.get("/api/research/hypotheses").json()
        dirty = next(h for h in listed["items"]
                     if h["hypothesis_id"] == "hyp_dirty")
        assert dirty["conditions"] == "{corrupted", "列表损坏列保留原串"
        assert dirty["data_ranges"] == "not-json"

        detail = api_client.get("/api/research/hypotheses/hyp_dirty").json()
        assert detail["conditions"] == "{corrupted"
        assert detail["data_ranges"] == "not-json"


    def test_formula_output_columns_keeps_raw(self, api_client, db_path):
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_dirty", name="dirty",
            version=1, expression="zscore(close, 20)",
            output_columns="{corrupted", entry_id="e_dirty",
            column_name="dirty_col")
        listed = api_client.get("/api/research/plugins/formula").json()
        dirty = next(r for r in listed["items"] if r["plugin_id"] == "p_dirty")
        assert dirty["output_columns"] == "{corrupted"

    def test_formula_output_columns_empty_string_kept(self, api_client, db_path):
        """output_columns 空串（终验消解）：falsy 不进解析、原样保留。
        None 形态被 NOT NULL 约束排除（不可达），空串是唯一 falsy 形态。"""
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_nocol", name="nocol",
            version=1, expression="zscore(close, 20)",
            output_columns="", entry_id="e_nocol", column_name="nocol_col")
        listed = api_client.get("/api/research/plugins/formula").json()
        row = next(r for r in listed["items"]
                   if r["plugin_id"] == "p_nocol")
        assert row["output_columns"] == ""



class TestNotFoundChannels:
    """三处 404 错误通道的结构化 code。"""

    def test_hypothesis_not_found(self, api_client):
        resp = api_client.get("/api/research/hypotheses/hyp_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_NOT_FOUND"


    def test_analysis_task_not_found(self, api_client):
        resp = api_client.get("/api/research/analysis/tasks/ant_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_TASK_NOT_FOUND"


