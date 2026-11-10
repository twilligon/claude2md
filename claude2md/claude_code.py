from contextlib import suppress
from json import JSONDecodeError
from typing import Any, Iterable

import json

from claude2md.chat import Block, Chat, Event, Message, Role, SearchResult
from claude2md.claude_ai import parse_content


def read(text: str) -> Chat:
    records = []
    for line in text.split("\n"):
        with suppress(JSONDecodeError):
            if isinstance(record := json.loads(line), dict):
                records.append(record)
    return parse(records)


def parse(records: Iterable[dict[str, Any]]) -> Chat:
    by_uuid: dict[str, Event] = {}
    messages: list[Message] = []
    turn: list[dict[str, Any]] = []
    explicit = None

    def flush() -> None:
        nonlocal turn
        if turn:
            message = assistant(turn, by_uuid.get(turn[0].get("parentUuid")))
            messages.append(message)
            for data in turn:
                by_uuid[data["uuid"]] = message
        turn = []

    for data in records:
        if data.get("type") == "last-prompt":
            explicit = data.get("leafUuid") if data.get("explicit") else None
        if "uuid" not in data or data.get("isSidechain"):
            continue
        if data.get("type") == "assistant" or is_results(data):
            turn.append(data)
        elif turn and data.get("type") != "user":
            turn.append(data)
        else:
            flush()
            parent = by_uuid.get(
                data.get("parentUuid") or data.get("logicalParentUuid")
            )
            event = user(data, parent) or Event(parent)
            by_uuid[data["uuid"]] = event
            if isinstance(event, Message):
                messages.append(event)
    flush()
    leaf = by_uuid[explicit].message if explicit in by_uuid else None
    return Chat(messages, leaf=leaf or next(reversed(messages), None))


def is_results(data: dict[str, Any]) -> bool:
    if data.get("type") != "user":
        return False
    content = (data.get("message") or {}).get("content")
    return (
        isinstance(content, list)
        and bool(content)
        and all(b.get("type") == "tool_result" for b in content)
    )


def user(data: dict[str, Any], parent: Event | None) -> Message | None:
    if data.get("type") != "user" or data.get("isMeta") or data.get("isCompactSummary"):
        return None
    api = data.get("message")
    if not isinstance(api, dict) or (content := api.get("content")) is None:
        return None
    if isinstance(content, str):
        blocks = [Block("text", content)]
    else:
        blocks = parse_content(content, results)
    return Message(parent, Role.USER, blocks)


def assistant(turn: list[dict[str, Any]], parent: Event | None) -> Message:
    content: list[dict[str, Any]] = []
    for data in turn:
        api = data.get("message")
        if not isinstance(api, dict) or not isinstance(api.get("content"), list):
            continue
        content += api["content"]
    blocks = parse_content(content, results)
    return Message(parent, Role.ASSISTANT, blocks)


def results(content: str | list[dict[str, Any]]) -> list[str | SearchResult]:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    parts: list[str | SearchResult] = []
    for b in content:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "search_result":
            body = "\n\n".join(c["text"] for c in b["content"])
            parts.append(SearchResult(b["title"], b["source"], body))
    return [p for p in parts if (p if isinstance(p, str) else p.text).strip()]
