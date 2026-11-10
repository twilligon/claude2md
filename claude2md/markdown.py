from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import IO

import html
import mimetypes
import sys

from claude2md.backup import Backup, base64_stream, whatwg_type
from claude2md.chat import Block, Chat, File, Message, Role


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
    embed: bool | None = None


def target(path: Path) -> str:
    if path.is_absolute() and path.is_relative_to(Path.home()):
        path = "~" / path.relative_to(Path.home())
    text = str(path)
    return f"<{text}>" if any(c.isspace() or c in "()" for c in text) else text


def lines(path: Path) -> int:
    count, last = 0, b"\n"
    with path.open("rb") as f:
        while chunk := f.read(1 << 16):
            count += chunk.count(b"\n")
            last = chunk[-1:]
    return count + (last != b"\n")


def size(n: int) -> str:
    if n < 1000:
        return f"{n} B"
    if n < 1_000_000:
        return f"{n / 1000:.2f} kB"
    return f"{n / 1_000_000:.2f} MB"


def link(writer: Writer, file: File, backup: Backup, embed: bool | None) -> None:
    path = backup.path(file)
    probed = backup.probe(file, whatwg_type)
    if probed is None:
        writer.write(
            "!"
            if (file.kind or (mimetypes.guess_type(file.name)[0] or "").split("/")[0])
            == "image"
            else ""
        )
    else:
        writer.write("!" if (probed[0] or "").startswith("image/") else "")
    writer.write(f"[{file.name}](")
    if probed is None or embed is not True:
        writer.write(target(path))
    else:
        kind = probed[0] or mimetypes.guess_type(file.name)[0]
        writer.write(f"data:{kind or 'application/octet-stream'};base64,")
        with path.open("rb") as f:
            base64_stream(f, writer)
    writer.write(")\n")


def render(
    msg: Message, writer: Writer, backup: Backup, options: Options, prefix: str = ""
) -> None:
    match msg.role:
        case Role.USER if options.user:
            writer.section(prefix, "> ")

            for a in msg.attachments:
                file = File(a.name or "", "document", a.id)
                path = backup.path(file)
                if options.embed is False:
                    writer.write(f"[{a.name or 'Pasted content'}]({target(path)})\n")
                    continue
                probed = backup.probe(file, whatwg_type)
                inline = probed is not None and probed[0] != "application/pdf"
                summary = (
                    f"<code>{html.escape(a.name, quote=False)}</code>"
                    if a.name
                    else "Pasted content"
                )
                if inline:
                    n = lines(path)
                    summary += f" ({n} line{'s' * (n != 1)}, {size(probed[1])})"
                writer.write(f"<details>\n<summary>{summary}</summary>\n\n")
                end = ""
                if inline:
                    with path.open(encoding="utf-8", newline="") as f:
                        while chunk := f.read(1 << 16):
                            writer.write(chunk)
                            end = chunk[-1]
                else:
                    writer.write(target(path))
                writer.write(("" if end == "\n" else "\n") + "\n</details>\n")

            for file in msg.files:
                link(writer, file, backup, options.embed)

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

            if msg.files and options.assistant:
                writer.section(prefix)
                for file in msg.files:
                    link(writer, file, backup, options.embed)


def title(chat: Chat, name: bool | None) -> str | None:
    if name or (name is None and chat.name):
        return f"# {chat.name or 'Untitled'}\n"
    return None


def write_branch(
    chat: Chat,
    msg: Message,
    backup: Backup,
    options: Options = Options(),
    name: bool | None = None,
    out: IO[str] = sys.stdout,
) -> None:
    if heading := title(chat, name):
        out.write(heading)
    writer = Writer(out, first=not heading)
    for ancestor in msg.branch():
        render(ancestor, writer, backup, options)


def write_all(
    chat: Chat,
    backup: Backup,
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
        render(msg, writer, backup, options, ",".join(ids[msg]) + "\t")
