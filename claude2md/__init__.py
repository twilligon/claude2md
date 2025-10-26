#!/usr/bin/env python3

# SPDX-License-Identifier: CC0-1.0

"""Convert Claude.ai or Claude Code chats to Markdown"""

__version__ = "0.1"

from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from json import JSONDecodeError
from uuid import UUID

import argparse
import json
import os
import re
import sys

NULL_UUID = "00000000-0000-4000-8000-000000000000"


class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"

    @classmethod
    def from_json(cls, value):
        match value:
            case "user" | "human":
                return cls.USER
            case "assistant":
                return cls.ASSISTANT
            case _:
                raise ValueError(f'Unknown role: "{value}"')


@dataclass(slots=True, frozen=True)
class Block:
    type: str
    content: str

    @classmethod
    def from_json(cls, obj):
        match obj["type"]:
            case "text":
                return cls("text", obj["text"])
            case "thinking":
                return cls("thinking", obj["thinking"])
            case "token_budget" | "tool_result" | "tool_use" | "voice_note":
                return None
            case block_type:
                raise NotImplementedError(f'Unsupported block type "{block_type}"')


@dataclass(slots=True, frozen=True)
class Message:
    uuid: str
    parent: str | None
    role: Role
    blocks: list[Block]
    attachments: list

    @classmethod
    def from_json(cls, data):
        # Try to get content from different possible locations
        content = data.get("message", {}).get("content") or data.get("content")
        if not content:
            raise KeyError("No content field found in message data")

        # Handle content as either a string or an array of block objects
        if isinstance(content, str):
            # Simple string content - create a text block
            blocks = [Block("text", content)]
        elif isinstance(content, list):
            # Array of block objects
            blocks = [
                block for block in (Block.from_json(obj) for obj in content) if block
            ]
        else:
            blocks = []
        parent = data.get("parentUuid") or data.get("parent_message_uuid")
        # Treat null UUID as None (no parent)
        if parent == NULL_UUID:
            parent = None

        # Try to get role from different possible locations
        role_value = data.get("message", {}).get("role") or data.get("sender")
        if not role_value:
            raise KeyError("No role field found in message data")

        return cls(
            uuid=data["uuid"],
            parent=parent,
            role=Role.from_json(role_value),
            blocks=blocks,
            attachments=data.get("attachments", []),
        )

    def render(self, thinking=True):
        parts = []

        if self.role == Role.USER:
            if self.attachments:
                lines = ["<documents>"]
                for index, attachment in enumerate(self.attachments, 1):
                    file_name = attachment.get("file_name")
                    file_type = attachment.get("file_type")
                    extracted = attachment["extracted_content"]

                    doc = f'<document index="{index}"'
                    if file_type:
                        doc += f' media_type="{file_type}"'
                    doc += ">"
                    if file_name:
                        doc += f"<source>{file_name}</source>"
                    doc += (
                        f"<document_content>{extracted}</document_content></document>"
                    )
                    lines.append(doc)
                lines.append("</documents>")
                parts.append("\n".join(lines))

            for block in self.blocks:
                parts.append(block.content)

            text = "\n\n".join(parts)
            return re.sub(r"^", "> ", text, flags=re.MULTILINE)

        elif self.role == Role.ASSISTANT:
            for block in self.blocks:
                if block.type == "thinking" and not thinking:
                    continue
                if block.type == "thinking":
                    parts.append(f"<thinking>\n{block.content}\n</thinking>")
                else:
                    parts.append(block.content)

            return "\n\n".join(parts)

        return ""


@dataclass(slots=True)
class Chat:
    file: object

    messages: dict = field(init=False, default_factory=dict)
    metadata: dict = field(init=False, default_factory=dict)
    leaves: list = field(init=False, default_factory=list)

    def __post_init__(self):
        unparsed = self.file.read()

        try:
            parsed = json.loads(unparsed)
            for msg_data in parsed["chat_messages"]:
                msg = Message.from_json(msg_data)
                self.messages[msg.uuid] = msg
            self.metadata = parsed
        except (JSONDecodeError, KeyError, TypeError):
            for line in unparsed.split("\n"):
                with suppress(JSONDecodeError, KeyError, TypeError):
                    msg = Message.from_json(json.loads(line))
                    self.messages[msg.uuid] = msg

        all_parents = {msg.parent for msg in self.messages.values() if msg.parent}
        self.leaves = [uuid for uuid in self.messages.keys() if uuid not in all_parents]

    def print_branch(self, uuid, user=True, assistant=True, thinking=False, title=None):
        msg = self.messages[uuid]

        if msg.parent:
            self.print_branch(msg.parent, user, assistant, thinking, title)
        elif title:
            chat_name = self.metadata.get("name")
            if title is True:
                title_text = chat_name or "Untitled"
                print(f"# {title_text}\n")
            elif chat_name:
                print(f"# {chat_name}\n")

        if msg.role == Role.USER and not user:
            return
        if msg.role == Role.ASSISTANT and not assistant:
            return

        rendered = msg.render(thinking=thinking)
        if rendered:
            print(rendered)
            print()

    def print_leaves(self, uuids=None, file=None):
        if uuids is None:
            uuids = self.leaves

        width = 80
        if sys.stdout.isatty():
            with suppress(AttributeError, OSError):
                width = os.get_terminal_size().columns
        preview_len = max(20, width - 36 - 4)

        for uuid in uuids:
            current = uuid
            preview = ""

            while current:
                msg = self.messages[current]
                if msg.role == Role.USER:
                    for block in msg.blocks:
                        if block.type == "text":
                            preview = block.content
                            break
                    if preview:
                        break
                current = msg.parent

            if preview:
                preview = preview.replace("\n", " ").replace("\r", " ")
                preview = " ".join(preview.split())
                if len(preview) > preview_len:
                    preview = preview[: preview_len - 1] + "…"
            else:
                preview = ""

            print(f"{uuid}\t{preview}", file=file)

    def get_leaf(self, prefix=""):
        with suppress(ValueError):
            return str(UUID(prefix))

        matches = [uuid for uuid in self.leaves if uuid.startswith(prefix)]

        match matches:
            case []:
                print(f'Error: No leaf matches prefix "{prefix}"', file=sys.stderr)
                return None
            case [single]:
                return single
            case multiple:
                print(
                    f'Error: Multiple leaves match prefix "{prefix}":',
                    file=sys.stderr,
                )
                self.print_leaves(multiple, file=sys.stderr)
                return None


def main():
    sys.setrecursionlimit(100_000)  # lol

    parser = argparse.ArgumentParser(
        description="Convert Claude.ai or Claude Code chats to Markdown"
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="JSON file to convert (default: stdin)",
    )

    nav_group = parser.add_mutually_exclusive_group()
    nav_group.add_argument(
        "--branches",
        dest="leaves",
        action="store_true",
        help="List all branch message UUIDs",
    )
    nav_group.add_argument(
        "--branch", dest="leaf", metavar="UUID", help="Show chain to specific branch"
    )

    parser.add_argument(
        "--user",
        dest="user",
        action="store_true",
        default=True,
        help="Show user messages (default)",
    )
    parser.add_argument(
        "--no-user", dest="user", action="store_false", help="Hide user messages"
    )
    parser.add_argument(
        "--human", dest="user", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--no-human", dest="user", action="store_false", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--assistant",
        dest="assistant",
        action="store_true",
        default=True,
        help="Show assistant (default)",
    )
    parser.add_argument(
        "--no-assistant", dest="assistant", action="store_false", help="Hide assistant"
    )
    parser.add_argument(
        "--thinking",
        dest="thinking",
        action="store_true",
        default=False,
        help="Show thinking blocks",
    )
    parser.add_argument(
        "--no-thinking",
        dest="thinking",
        action="store_false",
        help="Hide thinking (default)",
    )
    parser.add_argument(
        "--title",
        dest="title",
        action="store_true",
        default=None,
        help='Always show title (or "Untitled" if none)',
    )
    parser.add_argument(
        "--no-title", dest="title", action="store_false", help="Never show title"
    )
    args = parser.parse_args()

    chat = Chat(args.file)

    if args.leaves:
        chat.print_leaves()
    elif uuid := (
        (args.leaf and chat.get_leaf(args.leaf))
        or chat.metadata.get("current_leaf_message_uuid")
        or chat.get_leaf()
    ):
        chat.print_branch(uuid, args.user, args.assistant, args.thinking, args.title)


if __name__ == "__main__":
    main()
