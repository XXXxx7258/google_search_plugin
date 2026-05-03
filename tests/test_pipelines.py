"""核心纯逻辑函数的回归测试。

覆盖范围:
- ``peel_envelope`` —— SDK 2.4 双层 envelope 防御
- ``_format_messages_to_readable`` —— host 序列化 dict → 文本
- ``is_url`` —— URL 直访路径分支判定
- ``parse_rewrite_output`` —— LLM rewrite 输出解析(纯字符串 / JSON / 围栏 JSON)
"""

from __future__ import annotations

import pytest

from google_search_plugin.pipelines._envelope import peel_envelope
from google_search_plugin.pipelines.search_pipeline import _format_messages_to_readable
from google_search_plugin.pipelines.url_pipeline import is_url
from google_search_plugin.tools.rewrite_output import parse_rewrite_output


# ============================================================================
# peel_envelope
# ============================================================================


class TestPeelEnvelope:
    def test_double_envelope_messages(self) -> None:
        """SDK 2.4 真实观察到的双层结构:外 {success, result} 包内 {success, messages}。"""
        wrapped = {
            "success": True,
            "result": {"success": True, "messages": [{"id": "m1"}]},
        }
        out = peel_envelope(wrapped)
        assert out == {"success": True, "messages": [{"id": "m1"}]}

    def test_double_envelope_llm(self) -> None:
        wrapped = {
            "success": True,
            "result": {"success": True, "response": "hello", "model": "utils"},
        }
        out = peel_envelope(wrapped)
        assert out == {"success": True, "response": "hello", "model": "utils"}

    def test_single_envelope(self) -> None:
        """只包一层也能正确剥到 inner。"""
        wrapped = {"success": True, "result": [1, 2, 3]}
        assert peel_envelope(wrapped) == [1, 2, 3]

    def test_bare_list_passthrough(self) -> None:
        """已经是裸数据(SDK 自动 unwrap 过的),不应再剥。"""
        bare = [{"a": 1}, {"b": 2}]
        assert peel_envelope(bare) is bare

    def test_error_envelope_no_result_key(self) -> None:
        """{success: False, error: ...} 没 result 键,不剥皮。"""
        err = {"success": False, "error": "boom"}
        assert peel_envelope(err) is err

    def test_dict_without_success_passthrough(self) -> None:
        """普通 dict 没有 success 键,不剥。"""
        normal = {"foo": "bar"}
        assert peel_envelope(normal) is normal

    def test_max_depth_clamp(self) -> None:
        """递归层数被 max_depth 限制,避免病态数据死循环。"""
        deep: dict = {"success": True, "result": "leaf"}
        # 包 6 层
        for _ in range(6):
            deep = {"success": True, "result": deep}
        out = peel_envelope(deep, max_depth=2)
        # 只剥 2 层,剩下还是 envelope dict
        assert isinstance(out, dict)
        assert "success" in out and "result" in out

    def test_inner_none_not_unwrapped(self) -> None:
        """inner 是 None 时,认为这是 success=True 但无 payload 的特殊响应,不剥。"""
        wrapped = {"success": True, "result": None}
        assert peel_envelope(wrapped) is wrapped

    def test_non_dict_input(self) -> None:
        assert peel_envelope("plain string") == "plain string"
        assert peel_envelope(42) == 42
        assert peel_envelope(None) is None


# ============================================================================
# _format_messages_to_readable
# ============================================================================


class TestFormatMessagesToReadable:
    def test_full_dict_with_cardname(self) -> None:
        msgs = [
            {
                "timestamp": "1777747340.0",
                "message_info": {"user_info": {"user_id": "u1", "user_nickname": "晴空", "user_cardname": "管理员"}},
                "processed_plain_text": "你好",
            },
        ]
        out = _format_messages_to_readable(msgs)
        # cardname 优先
        assert "管理员" in out
        assert "晴空" not in out
        assert "你好" in out
        assert ":" in out

    def test_falls_to_nickname_when_no_cardname(self) -> None:
        msgs = [
            {
                "message_info": {"user_info": {"user_id": "u1", "user_nickname": "晴空"}},
                "processed_plain_text": "在干嘛",
            },
        ]
        out = _format_messages_to_readable(msgs)
        assert "晴空" in out
        assert "在干嘛" in out

    def test_falls_to_user_id_when_no_name_fields(self) -> None:
        msgs = [
            {
                "message_info": {"user_info": {"user_id": "12345"}},
                "processed_plain_text": "test",
            },
        ]
        out = _format_messages_to_readable(msgs)
        assert "12345" in out

    def test_uses_display_message_when_no_processed(self) -> None:
        """processed_plain_text 缺失,fall back 到 display_message。"""
        msgs = [
            {
                "message_info": {"user_info": {"user_id": "u1", "user_nickname": "x"}},
                "display_message": "from display",
            },
        ]
        out = _format_messages_to_readable(msgs)
        assert "from display" in out

    def test_skips_invalid_entries(self) -> None:
        msgs = [
            {"processed_plain_text": "valid", "message_info": {"user_info": {"user_id": "u1"}}},
            "a string",  # 非 dict,跳过
            {"random": "no user/text"},  # 缺关键字段,跳过
            {
                "message_info": {"user_info": {"user_id": "u2"}},
                "processed_plain_text": "",  # 空文本,跳过
            },
        ]
        out = _format_messages_to_readable(msgs)
        assert "valid" in out
        # 只有一条有效消息
        assert out.count("\n") == 0

    def test_empty_input(self) -> None:
        assert _format_messages_to_readable([]) == ""

    def test_invalid_timestamp_no_prefix(self) -> None:
        """timestamp 解析失败时,不应该报错也不该打 ts_prefix。"""
        msgs = [
            {
                "timestamp": "not a number",
                "message_info": {"user_info": {"user_id": "u1"}},
                "processed_plain_text": "msg",
            },
        ]
        out = _format_messages_to_readable(msgs)
        assert "msg" in out
        assert not out.startswith("[")  # 没成功的 ts prefix


# ============================================================================
# is_url
# ============================================================================


class TestIsUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "https://example.com",
            "https://example.com/path?q=1",
            "https://www.zhihu.com/question/12345/answer/67890",
            "  https://example.com  ",  # 前后空格 strip 后仍是 URL
        ],
    )
    def test_valid_urls(self, url: str) -> None:
        assert is_url(url) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "hello",
            "看看 https://example.com 吧",  # 包含空格的句子
            "ftp://example.com",  # 非 http/https
            "javascript:alert(1)",  # 非 http/https
            "//cdn.example.com",  # 没 scheme
            "example.com",  # 没 scheme
        ],
    )
    def test_invalid_urls(self, text: str) -> None:
        assert is_url(text) is False


# ============================================================================
# parse_rewrite_output
# ============================================================================


class TestParseRewriteOutput:
    def test_plain_string(self) -> None:
        query, topic = parse_rewrite_output("简单的查询词")
        assert query == "简单的查询词"
        assert topic is None

    def test_json_with_query_and_topic(self) -> None:
        query, topic = parse_rewrite_output('{"query": "test", "tavily_topic": "news"}')
        assert query == "test"
        assert topic == "news"

    def test_json_with_general_topic(self) -> None:
        query, topic = parse_rewrite_output('{"query": "概念", "tavily_topic": "general"}')
        assert query == "概念"
        assert topic == "general"

    def test_json_with_empty_topic(self) -> None:
        query, topic = parse_rewrite_output('{"query": "test", "tavily_topic": ""}')
        assert query == "test"
        assert topic is None

    def test_fenced_json(self) -> None:
        raw = '```json\n{"query": "test", "tavily_topic": "news"}\n```'
        query, topic = parse_rewrite_output(raw)
        assert query == "test"
        assert topic == "news"

    def test_invalid_topic_normalized_to_none(self) -> None:
        """tavily_topic 不在白名单(general/news)时归 None。"""
        _, topic = parse_rewrite_output('{"query": "x", "tavily_topic": "sports"}')
        assert topic is None

    def test_empty_input(self) -> None:
        query, topic = parse_rewrite_output("")
        assert query == ""
        assert topic is None

    def test_none_input(self) -> None:
        query, topic = parse_rewrite_output(None)  # type: ignore[arg-type]
        assert query == ""
        assert topic is None
