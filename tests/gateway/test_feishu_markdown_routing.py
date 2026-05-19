"""Routing tests for the Feishu outbound payload builder.

The agent emits a variety of reply shapes; the adapter has to decide
between Feishu's plain ``text`` and ``post (md)`` envelopes. Plain
text is fine for short conversational replies; structured replies
(data analysis, tool output dumps) must render via ``md`` or the user
sees a wall of unparsed text.

These tests pin the routing decisions in
:func:`gateway.platforms.feishu._build_outbound_payload` plus the
``_looks_structured`` heuristic that catches Chinese-colon multi-line
replies the markdown-hint regex would otherwise miss.
"""

from __future__ import annotations

import json
import pytest


@pytest.fixture()
def feishu_module():
    try:
        from gateway.platforms import feishu  # type: ignore
    except Exception as exc:  # pragma: no cover — surface import errors
        pytest.skip(f"feishu module unavailable: {exc}")
    return feishu


@pytest.fixture()
def builder(feishu_module):
    """A minimal adapter shim that exposes ``_build_outbound_payload``.

    The real adapter requires a Lark client + asyncio event loop to
    construct; for routing-only tests we don't need any of that.
    Bind the unbound method onto a SimpleNamespace.
    """
    from types import SimpleNamespace

    shim = SimpleNamespace()
    shim._build_outbound_payload = feishu_module.FeishuAdapter._build_outbound_payload.__get__(
        shim, type(shim)
    )
    return shim


class TestLooksStructured:
    def test_single_line_is_not_structured(self, feishu_module):
        assert feishu_module._looks_structured("你好，世界") is False

    def test_empty_is_not_structured(self, feishu_module):
        assert feishu_module._looks_structured("") is False

    def test_chinese_label_value_pair_is_structured(self, feishu_module):
        text = "\n".join(
            [
                "数据形状：1000 行 × 5 列",
                "列：销售额、日期、产品",
                "缺失率最高的列是「地区」（15%）",
            ]
        )
        assert feishu_module._looks_structured(text) is True

    def test_ascii_label_value_pair_is_structured(self, feishu_module):
        assert (
            feishu_module._looks_structured("Path: /opt/data/x.csv\nStatus: OK")
            is True
        )

    def test_multiline_prose_without_colons_is_not_structured(self, feishu_module):
        prose = "Hello\nworld\nthird line"
        assert feishu_module._looks_structured(prose) is False

    def test_one_colon_line_below_threshold(self, feishu_module):
        # Threshold requires 2+ colon lines so a single "Re: …" doesn't
        # accidentally route everything through post.
        text = "Re: ticket #123\nThanks for the update."
        assert feishu_module._looks_structured(text) is False


class TestOutboundRouting:
    def _payload_for(self, builder, text):
        msg_type, raw = builder._build_outbound_payload(text)
        return msg_type, json.loads(raw)

    def test_short_prose_stays_text(self, builder):
        msg_type, parsed = self._payload_for(builder, "你好")
        assert msg_type == "text"
        assert parsed == {"text": "你好"}

    def test_explicit_markdown_routes_to_post(self, builder):
        msg_type, parsed = self._payload_for(
            builder, "**Hello** _world_\n- item"
        )
        assert msg_type == "post"
        assert "zh_cn" in parsed and parsed["zh_cn"]["content"]

    def test_markdown_table_routes_to_post(self, builder):
        text = "| col1 | col2 |\n| ---- | ---- |\n| a    | b    |"
        msg_type, _parsed = self._payload_for(builder, text)
        assert msg_type == "post"

    def test_structured_chinese_response_routes_to_post(self, builder):
        # This is the regression that motivated the heuristic: an Excel
        # analysis reply from M2 with reasoning_effort=none.
        text = (
            "数据形状：1000 行 × 5 列\n"
            "列：销售额、日期、产品、地区、客户ID\n"
            "缺失率最高的列是「地区」（15%）"
        )
        msg_type, _parsed = self._payload_for(builder, text)
        assert msg_type == "post"

    def test_two_colon_label_value_lines_route_to_post(self, builder):
        text = "Status: ok\nReason: warmed up"
        msg_type, _parsed = self._payload_for(builder, text)
        assert msg_type == "post"

    def test_single_colon_line_stays_text(self, builder):
        text = "Re: ticket #123\nThanks for the update."
        msg_type, _parsed = self._payload_for(builder, text)
        assert msg_type == "text"

    def test_empty_content_stays_text(self, builder):
        msg_type, parsed = self._payload_for(builder, "")
        assert msg_type == "text"
        assert parsed == {"text": ""}
