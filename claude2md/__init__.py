#!/usr/bin/env python3

# SPDX-License-Identifier: CC0-1.0

"""Convert Claude.ai or Claude Code chats to Markdown"""

__version__ = "0.5"

from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from itertools import groupby
from json import JSONDecodeError
from typing import IO, Any

import argparse
import io
import json
import os
import re
import sys


class PrefixWriter(IO[str]):
    def __init__(self, file: IO[str], prefix: str):
        self.file = file
        self.prefix = prefix
        self.empty = True
        self.needs_prefix = True

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        self.empty = False
        n = len(s)
        while s:
            if self.needs_prefix:
                self.file.write(self.prefix)
                self.needs_prefix = False
            i = s.find("\n")
            if i < 0:
                self.file.write(s)
                return n
            self.file.write(s[: i + 1])
            self.needs_prefix = True
            s = s[i + 1 :]
        return n


class NewlineWriter(IO[str]):
    def __init__(self, file: IO[str]):
        self.file = file
        self.needs_blank = True

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        if self.needs_blank:
            self.file.write("\n")
            self.needs_blank = False
        return self.file.write(s)


class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"

    @classmethod
    def from_json(cls, value: str) -> "Role":
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
    def from_json(cls, obj: dict[str, Any]) -> "Block | None":
        match obj["type"]:
            case "text":
                if content := obj["text"].strip():
                    return cls("text", content)
            case "thinking":
                if content := obj["thinking"].strip():
                    return cls("thinking", content)
            case "token_budget" | "tool_result" | "tool_use" | "voice_note":
                pass
            case block_type:
                raise NotImplementedError(f'Unsupported block type "{block_type}"')
        return None


@dataclass(slots=True, frozen=True, eq=False)
class Message:
    parent: "Message | None"
    role: Role
    blocks: list[Block]
    attachments: list[dict[str, Any]]

    @classmethod
    def from_json(cls, data: dict[str, Any], parent: "Message | None") -> "Message":
        if (content := data.get("message", {}).get("content", None)) is None and (
            content := data.get("content", None)
        ) is None:
            raise KeyError("No content field found in message data")

        if isinstance(content, str):
            blocks = [Block("text", content)]
        elif isinstance(content, list):
            blocks = [
                block for block in (Block.from_json(obj) for obj in content) if block
            ]
        else:
            blocks = []

        if (role_value := data.get("message", {}).get("role", None)) is None and (
            role_value := data.get("sender", None)
        ) is None:
            raise KeyError("No role field found in message data")

        return cls(
            parent=parent,
            role=Role.from_json(role_value),
            blocks=blocks,
            attachments=data.get("attachments", []),
        )

    def preview(self) -> str:
        if self.role == Role.USER:
            for block in self.blocks:
                if block.type == "text":
                    return block.content
        return self.parent.preview() if self.parent else ""

    def render(
        self,
        user: bool = True,
        assistant: bool = True,
        thinking: bool = True,
        file: IO[str] = sys.stdout,
        parents: bool = True,
        first: bool = True,
    ) -> bool:
        if parents and self.parent:
            first = self.parent.render(user, assistant, thinking, file, parents, first)

        match self.role:
            case Role.USER:
                if user:
                    out = PrefixWriter(file if first else NewlineWriter(file), "> ")
                    first = False

                    if self.attachments:
                        out.write("<documents>\n")

                        for index, attachment in enumerate(self.attachments, 1):
                            file_name = attachment.get("file_name")
                            file_type = attachment.get("file_type")

                            out.write(f'<document index="{index}"')
                            if file_type:
                                out.write(f' media_type="{file_type}"')
                            out.write(">")

                            if file_name:
                                out.write(f"<source>{file_name}</source>")

                            out.write("<document_content>")
                            out.write(attachment["extracted_content"])
                            out.write("</document_content></document>\n")

                        out.write("</documents>\n")

                    for block in self.blocks:
                        out.write(block.content)
                        out.write("\n")
            case Role.ASSISTANT:
                for block_type, group in groupby(self.blocks, key=lambda b: b.type):
                    out = file if first else NewlineWriter(file)
                    if thinking and block_type == "thinking":
                        out.write("<thinking>")

                        for b in group:
                            out.write("\n")
                            out.write(b.content)
                            out.write("\n")

                        out.write("</thinking>")
                    elif assistant and block_type == "text":
                        for b in group:
                            out.write(b.content)
                    else:
                        continue

                    out.write("\n")
                    first = False

        return first


@dataclass(slots=True)
class Chat:
    file: IO[str]

    name: str | None = field(init=False, default=None)
    messages: list[Message] = field(init=False, default_factory=list)
    branches: dict[Message, int] = field(init=False, default_factory=dict)
    branch: Message | None = field(init=False, default=None)

    def __post_init__(self):
        by_uuid = {}

        def add_message(data: dict[str, Any]) -> None:
            parent_uuid = data.get("parentUuid") or data.get("parent_message_uuid")
            msg = by_uuid[data["uuid"]] = Message.from_json(
                data,
                by_uuid.get(parent_uuid),
            )
            self.messages.append(msg)

            self.branches[msg] = len(self.messages) - 1
            if msg.parent is not None:
                self.branches.pop(msg.parent, None)

        unparsed = self.file.read()

        try:
            parsed = json.loads(unparsed)
            self.name = parsed.get("name")
            for raw in parsed["chat_messages"]:
                add_message(raw)
            if branch := parsed.get("current_leaf_message_uuid"):
                self.branch = by_uuid[branch]
        except (JSONDecodeError, KeyError, TypeError):
            for line in unparsed.split("\n"):
                with suppress(JSONDecodeError, KeyError, TypeError):
                    d = json.loads(line)
                    if isinstance(d, dict):
                        add_message(d)

        if not self.branch and len(self.branches) == 1:
            self.branch = next(iter(self.branches))

    def print_branches(self, file: IO[str] = sys.stdout) -> None:
        width = 72
        if file.isatty():
            with suppress(AttributeError, OSError):
                width = max(20, os.get_terminal_size(file.fileno()).columns - 8)

        for msg in self.branches:
            preview = re.sub(r"\s+", " ", msg.preview()).strip()
            if len(preview) > width:
                preview = preview[: width - 1] + "…"

            print(f"{self.branches[msg]:x}\t{preview}", file=file)

    def print_branch(
        self,
        msg: Message,
        user: bool = True,
        assistant: bool = True,
        thinking: bool = True,
        name: bool | None = None,
    ) -> None:
        title = name or (name is None and self.name)
        if title:
            print(f"# {self.name or 'Untitled'}")
        msg.render(user, assistant, thinking, first=not title)

    def print_all(
        self,
        user: bool = True,
        assistant: bool = True,
        thinking: bool = False,
        name: bool | None = None,
    ) -> None:
        title = name or (name is None and self.name)
        if title:
            prefix = ",".join(f"{i:x}" for i in self.branches.values()) + "\t"
            print(f"{prefix}# {self.name or 'Untitled'}")

        descendants = defaultdict(list)

        def walk(msg: Message, branch: Message) -> None:
            if msg.parent:
                walk(msg.parent, branch)
            descendants[msg].append(branch)

        for branch in self.branches:
            walk(branch, branch)

        first = not title
        for msg in self.messages:
            prefix = ",".join(f"{self.branches[b]:x}" for b in descendants[msg]) + "\t"
            first = msg.render(
                user,
                assistant,
                thinking,
                PrefixWriter(sys.stdout, prefix),
                False,
                first,
            )


def main() -> None:
    sys.setrecursionlimit(100_000)  # lol

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Convert Claude.ai or Claude Code chats to Markdown",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r", encoding="utf-8"),
        default=sys.stdin,
        help="JSON file to convert (default: stdin)",
    )

    branches_group = parser.add_argument_group("branches")
    nav_group = branches_group.add_mutually_exclusive_group()
    nav_group.add_argument(
        "-B",
        "--branches",
        action="store_true",
        help="list all branches",
    )
    nav_group.add_argument(
        "-b",
        "--branch",
        metavar="ID",
        help="show messages from a specific non-default branch",
    )
    nav_group.add_argument(
        "-A",
        "--all-branches",
        action="store_true",
        help="show messages from all branches tagged with branch IDs",
    )

    filters_group = parser.add_argument_group("filters")
    filters_group.add_argument(
        "-n",
        "--name",
        action="store_true",
        default=None,
        help='always show chat name (or "Untitled" if none)',
    )
    filters_group.add_argument(
        "-N",
        "--no-name",
        dest="name",
        action="store_false",
        help="never show chat name",
    )
    filters_group.add_argument(
        "-u",
        "--user",
        action="store_true",
        default=None,
        help="show user messages",
    )
    filters_group.add_argument(
        "-a",
        "--assistant",
        action="store_true",
        default=None,
        help="show assistant messages",
    )
    filters_group.add_argument(
        "-t",
        "--thinking",
        action="store_true",
        default=None,
        help="show thinking blocks",
    )
    args = parser.parse_args()

    if args.user is args.assistant is args.thinking is None:
        args.user, args.assistant, args.thinking = True, True, False

    chat = Chat(args.file)

    if args.branches:
        chat.print_branches()
    elif args.all_branches:
        chat.print_all(args.user, args.assistant, args.thinking, args.name)
    else:
        if args.branch:
            try:
                branch = chat.messages[int(args.branch, 16)]
            except (ValueError, IndexError):
                print(f'Error: "{args.branch}" is not a branch ID', file=sys.stderr)
                chat.print_branches(file=sys.stderr)
                sys.exit(1)
        elif chat.branch:
            branch = chat.branch
        else:
            print(
                "Error: Multiple branches; specify with --branch <id>", file=sys.stderr
            )
            chat.print_branches(file=sys.stderr)
            sys.exit(1)

        chat.print_branch(branch, args.user, args.assistant, args.thinking, args.name)


if __name__ == "__main__":
    main()
