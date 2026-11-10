from json import JSONDecodeError
from typing import Any

import json

from claude2md.chat import Block, Chat, Message, Role, ToolResult, ToolUse, oneline

ROLES = {"human": Role.USER, "assistant": Role.ASSISTANT}


def read(text: str) -> Chat | None:
    try:
        export = json.loads(text)
    except JSONDecodeError:
        return None
    if not isinstance(export, dict) or "chat_messages" not in export:
        return None
    return parse(export)


def parse(export: dict[str, Any]) -> Chat:
    by_uuid: dict[str, Message] = {}
    for data in export["chat_messages"]:
        parent = by_uuid.get(data["parent_message_uuid"])
        by_uuid[data["uuid"]] = Message(
            parent=parent,
            role=ROLES[data["sender"]],
            blocks=parse_content(data["content"]),
            attachments=data.get("attachments", []),
        )
    return Chat(
        list(by_uuid.values()),
        name=oneline(export.get("name") or "") or None,
        leaf=by_uuid.get(export.get("current_leaf_message_uuid")),
    )


def parse_block(obj: dict[str, Any]) -> Block | ToolUse | None:
    match obj["type"]:
        case "text":
            if content := obj["text"].strip():
                return Block("text", content)
        case "thinking":
            summaries = (s.get("summary") or "" for s in obj.get("summaries") or [])
            summary = "\n\n".join(s.strip() for s in summaries if s.strip())
            if content := (obj.get("thinking") or "").strip() or summary:
                return Block("thinking", content)
        case "tool_use":
            return ToolUse(obj.get("id"), obj["name"], obj["input"])
        case (
            "image"
            | "fallback"
            | "flag"
            | "token_budget"
            | "voice_note"
            | "document"
            | "injected_prompt_block"
        ):
            pass
        case block_type:
            raise NotImplementedError(f'Unsupported block type "{block_type}"')
    return None


def parse_content(content: list[dict[str, Any]]) -> list[Block | ToolUse | ToolResult]:
    blocks: list[Block | ToolUse | ToolResult] = []
    unanswered: list[ToolUse] = []
    for obj in content:
        if obj["type"] == "tool_result":
            wanted = obj.get("tool_use_id")
            if use := next((u for u in unanswered if wanted in (None, u.id)), None):
                unanswered.remove(use)
                blocks.append(ToolResult(use))
        elif block := parse_block(obj):
            blocks.append(block)
            if isinstance(block, ToolUse):
                unanswered.append(block)
    return [b for b in blocks if b not in unanswered]
