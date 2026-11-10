from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby
from typing import IO

import sys

from claude2md.chat import Block, Chat, Message, Role


class Writer:
    def __init__(self, file: IO[str], first: bool = True):
        self.file = file
        self.first = first
        self.prefix = ""
        self.pending = ""

    def section(self, prefix: str = "", quote: str = "") -> None:
        self.pending = ("" if self.first else prefix + "\n") + prefix + quote
        self.prefix = prefix + quote

    def write(self, s: str) -> None:
        if s:
            self.first = False
        *lines, last = s.split("\n")
        for line in lines:
            prefix = self.pending if line else self.pending.rstrip(" ")
            self.file.write(prefix + line + "\n")
            self.pending = self.prefix
        if last:
            self.file.write(self.pending + last)
            self.pending = ""


@dataclass(frozen=True)
class Options:
    user: bool = True
    assistant: bool = True
    thinking: bool = True


def render(msg: Message, writer: Writer, options: Options, prefix: str = "") -> None:
    match msg.role:
        case Role.USER if options.user:
            writer.section(prefix, "> ")

            if msg.attachments:
                writer.write("<documents>\n")

                for index, attachment in enumerate(msg.attachments, 1):
                    file_name = attachment.get("file_name")
                    file_type = attachment.get("file_type")

                    writer.write(f'<document index="{index}"')
                    if file_type:
                        writer.write(f' media_type="{file_type}"')
                    writer.write(">")

                    if file_name:
                        writer.write(f"<source>{file_name}</source>")

                    writer.write("<document_content>")
                    writer.write(attachment["extracted_content"])
                    writer.write("</document_content></document>\n")

                writer.write("</documents>\n")

            for block in msg.blocks:
                if isinstance(block, Block):
                    writer.write(block.content)
                    writer.write("\n")
        case Role.ASSISTANT:
            for block_type, group in groupby(
                msg.blocks, key=lambda b: b.type if isinstance(b, Block) else None
            ):
                if block_type == "thinking" and options.thinking:
                    writer.section(prefix)
                    writer.write("<thinking>")

                    for b in group:
                        writer.write("\n")
                        writer.write(b.content)
                        writer.write("\n")

                    writer.write("</thinking>\n")
                elif block_type == "text" and options.assistant:
                    for b in group:
                        writer.section(prefix)
                        writer.write(b.content)
                        writer.write("\n")


def title(chat: Chat, name: bool | None) -> str | None:
    if name or (name is None and chat.name):
        return f"# {chat.name or 'Untitled'}\n"
    return None


def write_branch(
    chat: Chat,
    msg: Message,
    options: Options = Options(),
    name: bool | None = None,
    out: IO[str] = sys.stdout,
) -> None:
    if heading := title(chat, name):
        out.write(heading)
    writer = Writer(out, first=not heading)
    for ancestor in msg.branch():
        render(ancestor, writer, options)


def write_all(
    chat: Chat,
    options: Options = Options(),
    name: bool | None = None,
    out: IO[str] = sys.stdout,
) -> None:
    ids: dict[Message, list[str]] = defaultdict(list)
    for leaf, index in chat.branches.items():
        for msg in leaf.branch():
            ids[msg].append(f"{index:x}")

    if heading := title(chat, name):
        tags = ",".join(f"{i:x}" for i in chat.branches.values())
        out.write(f"{tags}\t{heading}")
    writer = Writer(out, first=not heading)
    for msg in chat.messages:
        render(msg, writer, options, ",".join(ids[msg]) + "\t")
