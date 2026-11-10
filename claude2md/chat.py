from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import cached_property
from typing import Any, Iterator


def oneline(text: str) -> str:
    return " ".join(text.split())


class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(slots=True, frozen=True)
class Block:
    type: str
    content: str
    at: datetime | None = None


@dataclass(slots=True, frozen=True, eq=False)
class ToolUse:
    id: str | None
    name: str
    input: dict[str, Any]
    at: datetime | None = None


@dataclass(slots=True, frozen=True)
class File:
    name: str
    kind: str | None
    id: str
    pages: int | None = None


@dataclass(slots=True, frozen=True)
class SearchResult:
    title: str
    url: str
    body: str
    age: str | None = None
    missing: bool = False

    @property
    def text(self) -> str:
        lines = [self.title, self.url]
        lines += [self.age] if self.age else []
        return "\n".join(lines) + (f"\n\n{self.body}" if self.body else "")


@dataclass(slots=True, frozen=True)
class Gallery:
    images: tuple[tuple[str, str, str], ...]
    expired: bool = False

    @property
    def text(self) -> str:
        images = ["\n".join(image) for image in self.images]
        images += ["[image results expired]"] if self.expired else []
        return "\n\n".join(images)


@dataclass(slots=True, frozen=True, eq=False)
class ToolResult:
    tool_use: ToolUse
    content: list[str | File | SearchResult | Gallery]
    is_error: bool
    at: datetime | None = None

    @property
    def text(self) -> str:
        def text(part: str | File | SearchResult | Gallery) -> str:
            if isinstance(part, File):
                return f"[image {part.id}]"
            return part if isinstance(part, str) else part.text

        return "\n\n".join(text(p) for p in self.content)


@dataclass(slots=True, frozen=True)
class Attachment:
    name: str | None
    media_type: str | None
    id: str


@dataclass(slots=True, frozen=True)
class Fallback:
    from_model: str
    to_model: str
    categories: tuple[str, ...]
    notice: str


@dataclass(slots=True, frozen=True, eq=False)
class Event:
    parent: "Event | None"

    def ancestors(self) -> Iterator["Event"]:
        event: Event | None = self
        while event:
            yield event
            event = event.parent

    def branch(self) -> Iterator["Message"]:
        return reversed([e for e in self.ancestors() if isinstance(e, Message)])

    @property
    def message(self) -> "Message | None":
        return next((e for e in self.ancestors() if isinstance(e, Message)), None)

    def preview(self, limit: int | None = None) -> str:
        texts = (
            block.content
            for msg in self.ancestors()
            if isinstance(msg, Message) and msg.role == Role.USER
            for block in msg.blocks
            if isinstance(block, Block) and block.type == "text"
        )
        text = oneline(next(texts, ""))
        if limit and len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        return text


@dataclass(slots=True, frozen=True, eq=False)
class Message(Event):
    role: Role
    blocks: list[Block | ToolUse | ToolResult]
    created_at: datetime
    attachments: list[Attachment] = field(default_factory=list)
    files: list[File] = field(default_factory=list)
    fallback: Fallback | None = None
    stop_reason: str | None = None
    cowork: bool = False
    id: str = field(kw_only=True)


@dataclass(frozen=True)
class Chat:
    messages: list[Message]
    name: str | None = None
    leaf: Message | None = None

    @cached_property
    def branches(self) -> dict[Message, int]:
        parents = {e.message for m in self.messages if (e := m.parent)}
        return {m: i for i, m in enumerate(self.messages) if m not in parents}

    @property
    def branch(self) -> Message | None:
        if self.leaf:
            return self.leaf
        return next(iter(self.branches)) if len(self.branches) == 1 else None

    def select(self, branch: str | None) -> Message:
        if branch:
            try:
                return self.messages[int(branch, 16)]
            except (ValueError, IndexError):
                raise LookupError(f'"{branch}" is not a branch ID') from None
        if self.branch:
            return self.branch
        if not self.branches:
            raise LookupError("No messages")
        raise LookupError("Multiple branches; specify with --branch <id>")
