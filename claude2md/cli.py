from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import IO, Iterable

import argparse
import io
import os
import sys

from claude_backup import ChatsEntry, Nameable, Organization

from claude2md import __version__, claude_ai, claude_code, markdown
from claude2md.backup import (
    Backup,
    chat_file,
    copies,
    find_chats,
    saved_chats,
    uploads_dirs,
)
from claude2md.chat import Chat, Event, Message, stamp

DESCRIPTIONS = {
    "markdown": "Convert Claude.ai or Claude Code chats to Markdown",
    "claude-code": "Import a Claude.ai chat as a Claude Code session",
}


def read(file: IO[str], exported: str | None = None, lossy: bool = False) -> Chat:
    text = file.read()
    return claude_ai.read(text, exported, lossy) or claude_code.read(text, lossy)


def print_rows(rows: Iterable[tuple[list[str], Event | None]], file: IO[str]) -> None:
    columns = None
    if file.isatty():
        with suppress(AttributeError, OSError):
            columns = os.get_terminal_size(file.fileno()).columns
    for cells, branch in rows:
        line = "".join(f"{cell}\t" for cell in cells)
        width = max(20, columns - len(line.expandtabs())) if columns else None
        print(line + (branch.preview(width) if branch else ""), file=file)


def print_branches(
    chat: Chat, file: IO[str] = sys.stdout, timestamps: bool = False
) -> None:
    rows = (
        ([stamp(msg.ended_at)] * timestamps + [f"{index:x}"], msg)
        for msg, index in chat.branches.items()
    )
    print_rows(rows, file)


Saved = ChatsEntry | claude_code.Transcript


def saved_file(entry: Saved) -> Path:
    if isinstance(entry, claude_code.Transcript):
        return entry.path
    return chat_file(entry)


def print_chats(
    chats: list[Saved], file: IO[str] = sys.stdout, lossy: bool = False
) -> None:
    def name(saved: Nameable) -> str:
        return saved.name or saved.uuid

    def saved_under(entry: Saved) -> list[str]:
        if isinstance(entry, claude_code.Transcript):
            return [str(entry.path.parents[2]), entry.path.parent.name]
        organization = entry.chat_list.organization
        if not isinstance(organization, Organization):
            return ["(export)", "(export)"]
        return [name(organization.account), name(organization)]

    def branch(entry: Saved) -> Event | None:
        with saved_file(entry).open(encoding="utf-8") as text:
            return read(text, lossy=lossy).branch

    columns = [[entry.uuid for entry in chats], *zip(*map(saved_under, chats))]
    shown = columns[:1] + [column for column in columns[1:] if len(set(column)) > 1]
    print_rows(zip(map(list, zip(*shown)), map(branch, chats)), file)


def select_chat(query: str, lossy: bool = False) -> str:
    chats = find_chats(query, [*saved_chats(), *claude_code.transcripts(Path.cwd())])
    if len(chats) == 1:
        return str(saved_file(chats[0]))
    if chats:
        print(
            f'Error: Multiple saved chats match "{query}"; specify one by its ID',
            file=sys.stderr,
        )
        print_chats(chats, sys.stderr, lossy)
    else:
        print(f'Error: "{query}" is neither a file nor a saved chat', file=sys.stderr)
    sys.exit(1)


def select_branch(chat: Chat, branch: str | None) -> Message:
    try:
        return chat.select(branch)
    except LookupError as e:
        print(f"Error: {e}", file=sys.stderr)
        print_branches(chat, sys.stderr)
        sys.exit(1)


def build_parser(to: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=DESCRIPTIONS[to])
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "chat",
        nargs="?",
        help="chat JSON, UUID, or URL (default: stdin)",
    )
    if to == "markdown":
        parser.add_argument(
            "-e",
            "--embed",
            action="store_true",
            default=None,
            help="embed media as data URLs",
        )
        parser.add_argument(
            "-E",
            "--no-embed",
            dest="embed",
            action="store_false",
            help="link text attachments like media",
        )
    else:
        parser.add_argument(
            "-s",
            "--system",
            metavar="PROMPT",
            help="use a custom system prompt",
        )
    parser.add_argument(
        "-L",
        "--lossy",
        action="store_true",
        help="make do without what's missing or unsupported",
    )

    branches = parser.add_argument_group("branches").add_mutually_exclusive_group()
    branches.add_argument(
        "-B",
        "--branches",
        action="store_true",
        help="list all branches",
    )
    branches.add_argument(
        "-b",
        "--branch",
        metavar="ID",
        help="use a specific non-default branch",
    )
    if to == "claude-code":
        return parser
    branches.add_argument(
        "-A",
        "--all-branches",
        action="store_true",
        help="show messages from all branches",
    )

    filters = parser.add_argument_group("filters")
    filters.add_argument(
        "-n",
        "--name",
        action="store_true",
        default=None,
        help='always show chat name (or "Untitled")',
    )
    filters.add_argument(
        "-N",
        "--no-name",
        dest="name",
        action="store_false",
        help="never show chat name",
    )
    filters.add_argument(
        "-u",
        "--user",
        action="store_true",
        default=None,
        help="show user messages",
    )
    filters.add_argument(
        "-a",
        "--assistant",
        action="store_true",
        default=None,
        help="show assistant messages",
    )
    filters.add_argument(
        "-t",
        "--thinking",
        action="store_true",
        default=None,
        help="show thinking blocks",
    )
    filters.add_argument(
        "-T",
        "--timestamps",
        action="store_true",
        help="show timestamps",
    )
    return parser


def main(to: str) -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    parser = build_parser(to)
    args = parser.parse_args()

    path = args.chat
    if path is None:
        if sys.stdin.isatty():
            parser.print_help(sys.stderr)
            parser.exit(2)
        path = "-"
    if path != "-" and not os.path.exists(path):
        path = select_chat(path, args.lossy)
    path, exported = (path, None) if path == "-" else copies(path)
    try:
        with sys.stdin if path == "-" else open(path, encoding="utf-8") as file:
            twin = exported and Path(exported).read_text(encoding="utf-8")
            chat = read(file, twin, args.lossy)
    except OSError as e:
        sys.exit(f"Error: {e}")
    except ValueError as e:
        parser.error(str(e))
    backup = Backup(uploads_dirs(file.name), args.lossy)

    try:
        if args.branches:
            print_branches(chat, timestamps=to == "markdown" and args.timestamps)
        elif to == "markdown":
            if args.user is args.assistant is args.thinking is None:
                args.user, args.assistant, args.thinking = True, True, False
            options = markdown.Options(
                args.user, args.assistant, args.thinking, args.embed, args.timestamps
            )
            if args.all_branches:
                markdown.write_all(chat, backup, options, args.name)
            else:
                leaf = select_branch(chat, args.branch)
                markdown.write_branch(chat, leaf, backup, options, args.name)
        else:
            leaf = select_branch(chat, args.branch)
            print(claude_code.write(chat, backup, leaf, args.system))
    except OSError as e:
        sys.exit(f"Error: {e}")


to_markdown = partial(main, "markdown")
to_claude_code = partial(main, "claude-code")
