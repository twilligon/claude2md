from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from itertools import groupby
from pathlib import Path
from typing import IO, Callable

import html
import io
import mimetypes
import re
import sys

from claude2md.backup import Backup, base64_stream, whatwg_type
from claude2md.chat import Block, Chat, File, Message, Role, stamp


class Writer:
    def __init__(self, file: IO[str], first: bool = True):
        self.file = file
        self.first = first
        self.prefix = ""
        self.pending = ""

    def section(self, prefix: str = "", quote: str = "", note: str = "") -> None:
        self.prefix = prefix + quote
        self.pending = ("" if self.first else prefix + "\n") + self.prefix
        if note:
            self.pending += f"{note}\n{self.prefix}"

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
    timestamps: bool = False


def target(path: Path) -> str:
    if path.is_absolute() and path.is_relative_to(Path.home()):
        path = "~" / path.relative_to(Path.home())
    text = str(path)
    return f"<{text}>" if any(c.isspace() or c in "()" for c in text) else text


def lines(text: Callable[[], IO[str]]) -> int | None:
    count, last = 0, "\n"
    with text() as f:
        try:
            while chunk := f.read(1 << 16):
                count += chunk.count("\n")
                last = chunk[-1]
        except UnicodeDecodeError:
            return None
    return count + (last != "\n")


def count(n: int, what: str) -> str:
    return f"{n} {what}{'s' * (n != 1)}"


def escape(text: str) -> str:
    # only what could parse as Markdown: snake_case and lone ampersands are fine as is
    special = r"[\\`*\[\]<~]|&(?=#?\w+;)|(?<![^\W_])_|_(?![^\W_])"
    return re.sub(special, lambda m: "\\" + m[0], text)


def code(text: str) -> str:
    fence = "`" * (max(map(len, re.findall("`+", text)), default=0) + 1)
    pad = " " if text[:1] == "`" or text[-1:] == "`" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def size(n: int) -> str:
    if n < 1000:
        return f"{n} B"
    scaled, unit = n / 1000, "kB"
    for bigger in ("MB", "GB"):
        if round(scaled, 2) < 1000:
            break
        scaled, unit = scaled / 1000, bigger
    return f"{scaled:.2f} {unit}"


def facts(
    file: File, backup: Backup, path: Path, probed: tuple[str | None, int] | None
) -> list[str]:
    about = [count(file.pages, "page")] if file.pages else []
    if file.preview:
        about.append(f"{file.preview[0]}x{file.preview[1]}")
    if probed is not None and probed[0] is None:
        lined = lines(partial(path.open, encoding="utf-8", newline=""))
        if lined is not None:
            about.append(count(lined, "line"))
    if (n := backup.size(file)) is not None:
        about.append(size(n))
    return about


def link(
    writer: Writer,
    file: File,
    backup: Backup,
    path: Path,
    probed: tuple[str | None, int] | None,
    embed: bool | None,
    untitled: str = "",
) -> None:
    if probed is None:
        kind = file.kind or (mimetypes.guess_type(file.name)[0] or "").split("/")[0]
        image = kind == "image"
    else:
        image = (probed[0] or "").startswith("image/")
    # alt text is plain text, where code spans would vanish rather than render
    label = (escape if image else code)(file.name) if file.name else untitled
    if about := facts(file, backup, path, probed):
        label = f"{label} ({', '.join(about)})".lstrip()
    # embedded output should survive being moved, which links to local files won't
    linked = image and embed is not True
    writer.write(f"{'[' * linked}{'!' * image}[{label}](")
    if probed is None or embed is not True:
        writer.write(target(path))
    else:
        kind = probed[0] or mimetypes.guess_type(file.name)[0]
        writer.write(f"data:{kind or 'application/octet-stream'};base64,")
        with path.open("rb") as f:
            base64_stream(f, writer)
    writer.write(f")]({target(path)})\n" if linked else ")\n")


def render(
    msg: Message,
    writer: Writer,
    backup: Backup,
    options: Options,
    tags: str | None = None,
) -> None:
    def section(at: datetime | None = None, quote: str = "") -> None:
        when = stamp(at or msg.created_at)
        if tags is None:
            writer.section("", quote, f"<!-- {when} -->" * options.timestamps)
        else:
            writer.section(f"{when}\t" * options.timestamps + f"{tags}\t", quote)

    match msg.role:
        case Role.USER if options.user:
            section(quote="> ")

            for a in msg.attachments:
                file = File(a.name or "", "document", a.id)
                path = backup.path(file)
                probed = backup.probe(file, whatwg_type)
                nbytes = backup.size(file)
                text: Callable[[], IO[str]] | None = None
                if probed is None and a.extracted is not None:
                    text = partial(io.StringIO, a.extracted)
                    if nbytes is None:
                        nbytes = len(a.extracted.encode())
                elif probed is not None and probed[0] is None:
                    text = partial(path.open, encoding="utf-8", newline="")
                n = lines(text) if text and options.embed is not False else None
                if not text or n is None:
                    link(
                        writer,
                        file,
                        backup,
                        path,
                        probed,
                        options.embed,
                        "Pasted content",
                    )
                    continue
                summary = (
                    f"<code>{html.escape(a.name, quote=False)}</code>"
                    if a.name
                    else "Pasted content"
                )
                about = [count(n, "line")] + ([] if nbytes is None else [size(nbytes)])
                summary += f" ({', '.join(about)})"
                writer.write(f"<details>\n<summary>{summary}</summary>\n\n")
                end = ""
                with text() as f:
                    while chunk := f.read(1 << 16):
                        writer.write(chunk)
                        end = chunk[-1]
                writer.write(("" if end == "\n" else "\n") + "\n</details>\n")

            for file in msg.files:
                path, probed = backup.path(file), backup.probe(file, whatwg_type)
                link(writer, file, backup, path, probed, options.embed)

            for block in msg.blocks:
                if isinstance(block, Block):
                    writer.write(block.content)
                    writer.write("\n")
        case Role.ASSISTANT:
            for block_type, group in groupby(
                msg.blocks, key=lambda b: b.type if isinstance(b, Block) else None
            ):
                if block_type == "thinking" and options.thinking:
                    thoughts = list(group)
                    section(thoughts[0].at)
                    writer.write("<thinking>")

                    for b in thoughts:
                        writer.write("\n")
                        writer.write(b.content)
                        writer.write("\n")

                    writer.write("</thinking>\n")
                elif block_type == "text" and options.assistant:
                    for b in group:
                        section(b.at)
                        writer.write(b.content)
                        writer.write("\n")

            if msg.files and options.assistant:
                section()
                for file in msg.files:
                    path, probed = backup.path(file), backup.probe(file, whatwg_type)
                    link(writer, file, backup, path, probed, options.embed)


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
        out.write("\t" * options.timestamps + f"{tags}\t{heading}")
    writer = Writer(out, first=not heading)
    for msg in chat.messages:
        render(msg, writer, backup, options, ",".join(ids[msg]))
