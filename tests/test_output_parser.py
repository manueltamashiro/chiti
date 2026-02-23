"""
Tests for backend/llm/output_parser.py
"""

import pytest
from backend.llm.output_parser import parse_blocks, StreamingOutputParser


class TestParseBlocks:
    def test_plain_text(self):
        blocks = list(parse_blocks("Hello, world!"))
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert "Hello" in blocks[0].content

    def test_text_block_tag(self):
        blocks = list(parse_blocks("<text>This is a paragraph</text>"))
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].content == "This is a paragraph"

    def test_code_block(self):
        blocks = list(parse_blocks('<code lang="python">print("hi")</code>'))
        assert len(blocks) == 1
        assert blocks[0].type == "code"
        assert 'print("hi")' in blocks[0].content
        assert blocks[0].metadata.get("lang") == "python"

    def test_chart_block_json(self):
        stream = '<chart type="line">{"labels":["a","b"],"datasets":[]}</chart>'
        blocks = list(parse_blocks(stream))
        assert len(blocks) == 1
        assert blocks[0].type == "chart"
        assert isinstance(blocks[0].content, dict)
        assert blocks[0].metadata.get("type") == "line"

    def test_mixed_text_and_block(self):
        stream = "Before <code lang='js'>const x = 1;</code> after"
        blocks = list(parse_blocks(stream))
        types = [b.type for b in blocks]
        assert "text" in types
        assert "code" in types

    def test_multiple_blocks(self):
        stream = (
            "<text>Introduction</text>"
            '<chart type="bar">{"data":[]}</chart>'
            "<text>Conclusion</text>"
        )
        blocks = list(parse_blocks(stream))
        assert len(blocks) == 3
        assert blocks[0].type == "text"
        assert blocks[1].type == "chart"
        assert blocks[2].type == "text"

    def test_empty_string(self):
        blocks = list(parse_blocks(""))
        assert blocks == []

    def test_whitespace_only(self):
        blocks = list(parse_blocks("   \n  "))
        assert blocks == []

    def test_unclosed_tag_treated_as_text(self):
        blocks = list(parse_blocks("<text>unclosed"))
        # Unclosed tags become text
        assert len(blocks) >= 1

    def test_metric_block(self):
        stream = '<metric>{"label":"CPU","value":"42%"}</metric>'
        blocks = list(parse_blocks(stream))
        assert blocks[0].type == "metric"
        assert isinstance(blocks[0].content, dict)


class TestStreamingOutputParser:
    def test_feed_simple(self):
        parser = StreamingOutputParser()
        blocks = []
        for token in ["Hello", ", ", "world"]:
            blocks.extend(parser.feed(token))
        blocks.extend(parser.flush())
        assert len(blocks) == 1
        assert "Hello" in blocks[0].content

    def test_feed_across_tag_boundary(self):
        parser = StreamingOutputParser()
        blocks = []
        tokens = ["<tex", "t>", "Hello", "</t", "ext>"]
        for tok in tokens:
            blocks.extend(parser.feed(tok))
        blocks.extend(parser.flush())
        assert any(b.type == "text" for b in blocks)

    def test_feed_code_block(self):
        parser = StreamingOutputParser()
        blocks = []
        stream = '<code lang="python">x = 1\ny = 2\n</code>'
        for char in stream:
            blocks.extend(parser.feed(char))
        blocks.extend(parser.flush())
        code_blocks = [b for b in blocks if b.type == "code"]
        assert len(code_blocks) == 1

    def test_flush_emits_remaining_text(self):
        parser = StreamingOutputParser()
        list(parser.feed("Some trailing text"))
        flushed = list(parser.flush())
        assert any("trailing text" in str(b.content) for b in flushed)
