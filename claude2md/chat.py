from dataclasses import dataclass, field
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


@dataclass(slots=True, frozen=True, eq=False)
class ToolUse:
    id: str | None
    name: str
    input: dict[str, Any]


@dataclass(slots=True, frozen=True)
class File:
    name: str
    kind: str | None
    id: str


@dataclass(slots=True, frozen=True, eq=False)
class ToolResult:
    tool_use: ToolUse


@dataclass(slots=True, frozen=True)
class Attachment:
    name: str | None
    media_type: str | None
    id: str


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
    attachments: list[Attachment] = field(default_factory=list)
    files: list[File] = field(default_factory=list)


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
