"""Groq LLM client wrapper with retry/error handling and a generic tool-calling loop shared
by every agent. This is the one required LLM integration point; individual agents plug in
their own system prompt + tool schema/functions."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from groq import APIConnectionError, APIStatusError, Groq

from app.config import settings

logger = logging.getLogger("agentcare.llm")

_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=settings.GROQ_API_KEY)
    return _client


class LLMError(RuntimeError):
    pass


def chat_completion_with_retry(messages: list[dict], tools: list[dict] | None, max_retries: int = 3) -> Any:
    """Calls the Groq chat-completions endpoint with exponential-backoff retry — required
    error handling/recovery around the LLM boundary, which is the flakiest part of the
    pipeline."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            client = get_client()
            kwargs = {
                "model": settings.GROQ_MODEL,
                "messages": messages,
                "temperature": 0.2,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            return client.chat.completions.create(**kwargs)
        except (APIConnectionError, APIStatusError) as exc:
            last_error = exc
            logger.warning("LLM call failed (attempt %s/%s): %s", attempt, max_retries, exc)
            time.sleep(min(2 ** attempt, 8))
        except Exception as exc:  # noqa: BLE001 - surface any unexpected SDK error the same way
            last_error = exc
            logger.warning("LLM call raised unexpected error (attempt %s/%s): %s", attempt, max_retries, exc)
            time.sleep(min(2 ** attempt, 8))
    raise LLMError(f"LLM call failed after {max_retries} attempts: {last_error}")


@dataclass
class ToolRegistry:
    """Binds JSON tool schemas to real Python callables for one agent invocation."""
    schemas: list[dict] = field(default_factory=list)
    functions: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def add(self, schema: dict, fn: Callable[..., Any]) -> None:
        self.schemas.append(schema)
        self.functions[schema["function"]["name"]] = fn


def run_tool_loop(
    system_prompt: str,
    user_message: str,
    registry: ToolRegistry,
    max_iterations: int = 6,
) -> dict:
    """Generic ReAct-style tool-calling loop: ask the model, execute any tool calls it
    requests against real functions, feed results back, repeat until it returns a final
    JSON answer. Returns {'final_text': str, 'tool_calls': [...], 'messages': [...]}."""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    tool_call_log: list[dict] = []

    for _ in range(max_iterations):
        response = chat_completion_with_retry(messages, registry.schemas or None)
        choice = response.choices[0]
        msg = choice.message

        if getattr(msg, "tool_calls", None):
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                fn = registry.functions.get(fn_name)
                if fn is None:
                    result = {"error": f"Unknown tool '{fn_name}'"}
                else:
                    try:
                        result = fn(**args)
                    except Exception as exc:  # noqa: BLE001 - tool failure must not crash the agent
                        logger.exception("Tool '%s' raised an error", fn_name)
                        result = {"error": str(exc)}
                tool_call_log.append({"tool": fn_name, "args": args, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
            continue

        return {"final_text": msg.content or "", "tool_calls": tool_call_log, "messages": messages}

    return {
        "final_text": "Reached maximum reasoning steps without a final answer.",
        "tool_calls": tool_call_log,
        "messages": messages,
    }
