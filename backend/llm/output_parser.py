"""
llm/output_parser.py — Streaming output block XML parser.

The LLM emits typed blocks inside XML tags. This parser buffers the raw
token stream and yields complete OutputBlock objects as tags close.

Supported block types:
    <text>…</text>              → type="text"   (react-markdown)
    <chart type="line">…</chart>→ type="chart"  (Recharts JSON)
    <table>…</table>            → type="table"  (TanStack Table JSON)
    <code lang="python">…</code>→ type="code"   (Monaco Editor)
    <metric>…</metric>          → type="metric" (KPI card JSON)
    <form>…</form>              → type="form"   (react-hook-form JSON)
    <action_confirm>…</action_confirm> → type="action_confirm"

Content outside any tag is treated as plain text and emitted as type="text".
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Dict, Iterator, Optional

from backend.pipeline.models import OutputBlock


# ---------------------------------------------------------------------------
# Tag parsing helpers
# ---------------------------------------------------------------------------

# Matches opening tags with optional attributes: <chart type="bar"> or <code lang="py">
_OPEN_TAG = re.compile(r"<(text|chart|table|code|metric|form|action_confirm)(\s[^>]*)?>")
_CLOSE_TAG = re.compile(r"</(text|chart|table|code|metric|form|action_confirm)>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _parse_attrs(attrs_str: str) -> Dict[str, str]:
    return dict(_ATTR.findall(attrs_str or ""))


def _try_parse_json(text: str) -> Any:
    """Try to JSON-parse block content; fall back to raw string."""
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return stripped


# ---------------------------------------------------------------------------
# Synchronous generator (for unit tests with a pre-built string)
# ---------------------------------------------------------------------------

def parse_blocks(stream: str) -> Iterator[OutputBlock]:
    """
    Parse a complete LLM response string into OutputBlock objects.

    Yields one OutputBlock per typed section. Untagged text between blocks is
    yielded as type="text" blocks (skipped if empty / whitespace-only).
    """
    pos = 0
    text_buf = ""

    while pos < len(stream):
        open_match = _OPEN_TAG.search(stream, pos)

        if open_match is None:
            # Remaining text after last closing tag
            text_buf += stream[pos:]
            break

        # Text before this opening tag
        text_buf += stream[pos : open_match.start()]

        if text_buf.strip():
            yield OutputBlock(type="text", content=text_buf.strip())
        text_buf = ""

        tag_name = open_match.group(1)
        attrs = _parse_attrs(open_match.group(2) or "")
        content_start = open_match.end()

        # Find matching close tag
        close_pattern = re.compile(rf"</{tag_name}>")
        close_match = close_pattern.search(stream, content_start)

        if close_match is None:
            # Unclosed tag — treat rest as text
            text_buf = stream[open_match.start():]
            break

        raw_content = stream[content_start : close_match.start()]
        content = _try_parse_json(raw_content)

        yield OutputBlock(type=tag_name, content=content, metadata=attrs)
        pos = close_match.end()

    if text_buf.strip():
        yield OutputBlock(type="text", content=text_buf.strip())


# ---------------------------------------------------------------------------
# Async streaming parser (used in production with SSE token stream)
# ---------------------------------------------------------------------------

class StreamingOutputParser:
    """
    Stateful parser that accepts tokens one at a time and emits OutputBlocks.

    Usage:
        parser = StreamingOutputParser()
        async for token in llm_stream:
            for block in parser.feed(token):
                yield block
        for block in parser.flush():
            yield block
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_tag: Optional[str] = None
        self._tag_attrs: Dict[str, str] = {}
        self._tag_content = ""
        self._text_buf = ""

    def feed(self, token: str) -> Iterator[OutputBlock]:
        """Feed a token into the parser; yields any completed blocks."""
        self._buf += token
        yield from self._process()

    def flush(self) -> Iterator[OutputBlock]:
        """Flush any remaining buffered text as a final text block."""
        remaining = self._buf + self._text_buf
        if remaining.strip():
            yield OutputBlock(type="text", content=remaining.strip())
        self._buf = ""
        self._text_buf = ""

    def _process(self) -> Iterator[OutputBlock]:
        while True:
            if self._in_tag is None:
                # Looking for an opening tag
                open_match = _OPEN_TAG.search(self._buf)
                if open_match is None:
                    # No open tag found; safe text = everything except last 50 chars
                    # (keep a small buffer in case a tag starts at the very end)
                    safe_end = max(0, len(self._buf) - 50)
                    self._text_buf += self._buf[:safe_end]
                    self._buf = self._buf[safe_end:]
                    break

                # Emit any text before the opening tag
                self._text_buf += self._buf[: open_match.start()]
                if self._text_buf.strip():
                    yield OutputBlock(type="text", content=self._text_buf.strip())
                self._text_buf = ""

                self._in_tag = open_match.group(1)
                self._tag_attrs = _parse_attrs(open_match.group(2) or "")
                self._tag_content = ""
                self._buf = self._buf[open_match.end():]

            else:
                # Inside a tag — look for the closing tag
                close_pattern = re.compile(rf"</{self._in_tag}>")
                close_match = close_pattern.search(self._buf)
                if close_match is None:
                    # Not closed yet — keep a lookahead buffer in case the close
                    # tag is split across tokens (e.g. "</cod" + "e>").
                    # The lookahead length covers the longest possible close tag.
                    lookahead = len(self._in_tag) + 3  # len("</TAG>")
                    safe_end = max(0, len(self._buf) - lookahead)
                    self._tag_content += self._buf[:safe_end]
                    self._buf = self._buf[safe_end:]
                    break

                self._tag_content += self._buf[: close_match.start()]
                content = _try_parse_json(self._tag_content)
                yield OutputBlock(
                    type=self._in_tag,
                    content=content,
                    metadata=self._tag_attrs,
                )
                self._buf = self._buf[close_match.end():]
                self._in_tag = None
                self._tag_attrs = {}
                self._tag_content = ""


async def parse_stream(token_stream: AsyncIterator[str]) -> AsyncIterator[OutputBlock]:
    """
    Async generator: consume a raw token stream, yield OutputBlocks.

    Example:
        async for block in parse_stream(llm.stream(...)):
            await ws.send_json(block_to_dict(block))
    """
    parser = StreamingOutputParser()
    async for token in token_stream:
        for block in parser.feed(token):
            yield block
    for block in parser.flush():
        yield block
