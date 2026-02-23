"""
llm/claude.py — Claude API integration with streaming and tool support.

Streams tokens from the Anthropic API, routes them through the output block
parser, and feeds them to the WebSocket hub as typed OutputBlock events.

Action confirmation flow:
    - If a tool classified as Tier 2/3 is called, we pause execution,
      emit an action_confirm OutputBlock, and wait for user approval
      via a separate POST endpoint before resuming.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import anthropic

from backend.config.loader import cfg
from backend.config.logging import get_logger
from backend.llm.output_parser import StreamingOutputParser
from backend.pipeline.models import ActionTier, OutputBlock

logger = get_logger(__name__)

# System prompt template — context is injected at request time
_SYSTEM_PROMPT = """You are a personal AI assistant running locally on the user's machine.
You have access to tools for reading/writing files, running git and docker commands,
monitoring system resources, and more.

IMPORTANT RULES:
1. Always respond with structured output blocks using XML tags:
   - <text>markdown text here</text> for explanations
   - <code lang="python">code here</code> for code blocks
   - <chart type="line">{"labels":[...],"datasets":[...]}</chart> for charts
   - <table>{"columns":[...],"rows":[...]}</table> for tabular data
   - <metric>{"label":"CPU","value":"45%","delta":"+2%"}</metric> for KPIs
2. Never wrap everything in a single text block — use the most appropriate block type
3. For tool calls that modify the system, always explain what you're about to do first

Current date/time: {datetime}
{memory_context}
"""


class ClaudeClient:
    """
    Wraps the Anthropic Python SDK for streaming chat completions.

    Handles:
    - Context injection (datetime + memories)
    - Streaming token → OutputBlock conversion
    - Tool call → pipeline → action confirmation flow
    """

    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = cfg.llm.model

    async def stream_response(
        self,
        messages: List[Dict[str, Any]],
        system_context: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[OutputBlock]:
        """
        Stream a chat completion from Claude.

        Args:
            messages:       OpenAI-style message list (role/content)
            system_context: Extra context to append to system prompt
            tools:          Tool definitions from CapabilityRegistry

        Yields:
            OutputBlock objects as they are parsed from the stream
        """
        import datetime

        system = _SYSTEM_PROMPT.format(
            datetime=datetime.datetime.now().isoformat(timespec="minutes"),
            memory_context=system_context or "",
        )

        kwargs: Dict[str, Any] = dict(
            model=self._model,
            max_tokens=cfg.llm.max_tokens,
            system=system,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools

        parser = StreamingOutputParser()

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text_delta in stream.text_stream:
                    for block in parser.feed(text_delta):
                        yield block

                # Flush any remaining buffer
                for block in parser.flush():
                    yield block

                # Handle tool use blocks from the final message
                final_msg = await stream.get_final_message()
                for content_block in final_msg.content:
                    if content_block.type == "tool_use":
                        yield OutputBlock(
                            type="tool_call",
                            content={
                                "id": content_block.id,
                                "name": content_block.name,
                                "input": content_block.input,
                            },
                        )

        except anthropic.AuthenticationError:
            logger.error("Claude API authentication failed — check ANTHROPIC_API_KEY")
            yield OutputBlock(
                type="text",
                content="⚠️ Claude API key missing or invalid. Set `ANTHROPIC_API_KEY` env var.",
            )
        except anthropic.APIError as exc:
            logger.error("Claude API error", extra={"error": str(exc)})
            yield OutputBlock(
                type="text",
                content=f"⚠️ Claude API error: {exc}",
            )

    def messages_from_history(
        self, history: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert conversation DB records to Anthropic message format.

        Each record has: role ("user"|"assistant"), content (str).
        """
        return [
            {"role": rec["role"], "content": rec["content"]}
            for rec in history
        ]


def build_action_confirm_block(
    tool_name: str,
    params: Dict[str, Any],
    tier: ActionTier,
    justification: str,
    tool_call_id: str,
) -> OutputBlock:
    """
    Build an action_confirm OutputBlock to send to the frontend.
    The frontend renders this as a modal with Approve/Deny buttons.
    """
    return OutputBlock(
        type="action_confirm",
        content={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "params": params,
            "tier": tier.value,
            "justification": justification,
            "confirmation_type": "soft" if tier == ActionTier.TIER_2 else "explicit",
        },
    )
