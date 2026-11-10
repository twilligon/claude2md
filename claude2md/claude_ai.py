from datetime import datetime
from json import JSONDecodeError
from typing import Any, Callable

import json

from claude2md.chat import (
    Attachment,
    Block,
    Chat,
    Fallback,
    File,
    Gallery,
    Message,
    Role,
    SearchResult,
    ToolResult,
    ToolUse,
    oneline,
)

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
    flagged, fallback = safeguard_fallback(export)
    call, ran = handoff(export)
    cowork = False
    for data in export["chat_messages"]:
        cowork = cowork or any(handed(b, call) for b in data["content"])
        content, times = blocks(data, None if ran else call)
        pasted = attachments(data)
        parent = by_uuid.get(data["parent_message_uuid"])
        by_uuid[data["uuid"]] = Message(
            parent=parent,
            role=ROLES[data["sender"]],
            blocks=parse_content(content, results, times),
            created_at=datetime.fromisoformat(data["created_at"]),
            attachments=pasted,
            files=files(data, {a.id for a in pasted}),
            fallback=fallback if data["uuid"] == flagged else None,
            stop_reason=data.get("stop_reason"),
            cowork=cowork,
            id=data["uuid"],
        )
    return Chat(
        list(by_uuid.values()),
        name=oneline(export.get("name") or "") or None,
        leaf=by_uuid.get(export.get("current_leaf_message_uuid")),
    )


def safeguard_fallback(export: dict[str, Any]) -> tuple[str | None, Fallback | None]:
    fallback = (export.get("settings") or {}).get("safeguard_fallback")
    if not fallback:
        return None, None
    hops = fallback["hops"]
    return fallback["turn_uuid"], Fallback(
        hops[0]["from_model"]["model"],
        hops[-1]["to_model"]["model"],
        tuple(hop["category"] for hop in hops),
        fallback["explanation"],
    )


def handoff(export: dict[str, Any]) -> tuple[str | None, bool]:
    content = [b for data in export["chat_messages"] for b in data["content"]]
    first = next((n for n, b in enumerate(content) if by_claude_code(b)), 0)
    calls = [b for b in content[:first] if b["type"] == "tool_use"]
    if not calls:
        return None, False
    id = calls[-1].get("id")
    for b in content:
        if b["type"] == "tool_result" and b.get("tool_use_id") == id:
            if by_claude_code(b):
                return id, True
            if b.get("content") or b.get("is_error"):
                return None, False
    return id, False


def by_claude_code(block: dict[str, Any]) -> bool:
    return block.get("integration_name") == "Claude Code"


def blocks(
    data: dict[str, Any], unrun: str | None
) -> tuple[list[dict[str, Any]], list[datetime | None]]:
    return (
        [b for b in data["content"] if not handed(b, unrun)],
        [t for b, t in zip(data["content"], done(data)) if not handed(b, unrun)],
    )


def handed(block: dict[str, Any], handoff: str | None) -> bool:
    return handoff is not None and handoff in (
        block.get("id"),
        block.get("tool_use_id"),
    )


def span(
    block: dict[str, Any], data: dict[str, Any]
) -> tuple[datetime | None, datetime | None]:
    saved = datetime.fromisoformat(data["created_at"])
    start, stop = (
        datetime.fromisoformat(t) if (t := block.get(key)) else None
        for key in ("start_timestamp", "stop_timestamp")
    )
    return (None, None) if start == stop == saved else (start, stop)


def done(data: dict[str, Any]) -> list[datetime | None]:
    spans = [span(b, data) for b in data["content"]]
    starts = [start for start, _ in spans[1:]] + [None]
    return [
        min((t for t in (stop, start) if t), default=None)
        for (_, stop), start in zip(spans, starts)
    ]


def attachments(data: dict[str, Any]) -> list[Attachment]:
    pasted = data.get("attachments", [])
    listed = data.get("files") or []
    return [
        Attachment(
            a.get("file_name"),
            a.get("file_type"),
            (
                a["id"]
                if "id" in a
                else listed[len(listed) - len(pasted) + n]["file_uuid"]
            ),
        )
        for n, a in enumerate(pasted)
    ]


def files(data: dict[str, Any], pasted: set[str]) -> list[File]:
    listed = (data.get("files_v2") or []) + (data.get("files") or [])
    listed += [
        {"file_kind": "image", "file_uuid": b["file_uuid"]}
        for b in data["content"]
        if b["type"] == "image"
    ]
    pages = {
        f["file_uuid"]: count
        for f in listed
        if (count := (f.get("document_asset") or {}).get("page_count")) is not None
    }
    files: dict[str, File] = {}
    for f in listed:
        id = f["file_uuid"]
        if id in pasted:
            continue
        file = File(f.get("file_name") or "", f.get("file_kind"), id, pages.get(id))
        files.setdefault(file.id, file)
    return list(files.values())


def results(
    content: list[dict[str, Any]],
) -> list[str | File | SearchResult | Gallery]:
    parts: list[str | File | SearchResult | Gallery] = []
    for item in content:
        match item["type"]:
            case "text":
                text = item["text"]
            case "image":
                parts.append(File("", "image", item["file_uuid"]))
                continue
            case "knowledge":
                parts.append(
                    SearchResult(
                        item["title"],
                        item["url"],
                        item.get("text") or "",
                        (item.get("prompt_context_metadata") or {}).get("age") or None,
                        item["is_missing"],
                    )
                )
                continue
            case "image_gallery":
                parts.append(
                    Gallery(
                        tuple(
                            (img["title"], img["url"], img["page_url"])
                            for img in item["images"]
                        ),
                        item["is_expired"],
                    )
                )
                continue
            case item_type:
                raise NotImplementedError(f'Unsupported item type "{item_type}"')
        if text.strip():
            parts.append(text)
    return parts


def parse_block(
    obj: dict[str, Any], at: datetime | None = None
) -> Block | ToolUse | None:
    match obj["type"]:
        case "text":
            if content := obj["text"].strip():
                return Block("text", content, at)
        case "thinking":
            summaries = (s.get("summary") or "" for s in obj.get("summaries") or [])
            summary = "\n\n".join(s.strip() for s in summaries if s.strip())
            if content := (obj.get("thinking") or "").strip() or summary:
                return Block("thinking", content, at)
        case "tool_use":
            return ToolUse(obj.get("id"), obj["name"], obj["input"], at)
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


def parse_content(
    content: list[dict[str, Any]],
    results: Callable[[Any], list[str | File | SearchResult | Gallery]],
    times: list[datetime | None] | None = None,
) -> list[Block | ToolUse | ToolResult]:
    blocks: list[Block | ToolUse | ToolResult] = []
    unanswered: list[ToolUse] = []
    for n, obj in enumerate(content):
        at = times[n] if times else None
        if obj["type"] == "tool_result":
            wanted = obj.get("tool_use_id")
            if use := next((u for u in unanswered if wanted in (None, u.id)), None):
                unanswered.remove(use)
                error = obj.get("is_error", False)
                blocks.append(
                    ToolResult(use, results(obj.get("content", [])), error, at)
                )
        elif block := parse_block(obj, at):
            blocks.append(block)
            if isinstance(block, ToolUse):
                unanswered.append(block)
    return [b for b in blocks if b not in unanswered]
