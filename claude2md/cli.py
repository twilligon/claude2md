from contextlib import suppress
from typing import IO, Iterable

import argparse
import io
import os
import sys

from claude2md import __version__, claude_ai, claude_code, markdown
from claude2md.chat import Chat, Event, Message


def read(file: IO[str]) -> Chat:
    text = file.read()
    return claude_ai.read(text) or claude_code.read(text)


def print_rows(rows: Iterable[tuple[list[str], Event | None]], file: IO[str]) -> None:
    columns = None
    if file.isatty():
        with suppress(AttributeError, OSError):
            columns = os.get_terminal_size(file.fileno()).columns
    for cells, branch in rows:
        line = "".join(f"{cell}\t" for cell in cells)
        width = max(20, columns - len(line.expandtabs())) if columns else None
        print(line + (branch.preview(width) if branch else ""), file=file)


def print_branches(chat: Chat, file: IO[str] = sys.stdout) -> None:
    rows = (([f"{index:x}"], msg) for msg, index in chat.branches.items())
    print_rows(rows, file)


def select_branch(chat: Chat, branch: str | None) -> Message:
    try:
        return chat.select(branch)
    except LookupError as e:
        print(f"Error: {e}", file=sys.stderr)
        print_branches(chat, sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
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
        help="show messages from a specific non-default branch",
    )
    branches.add_argument(
        "-A",
        "--all-branches",
        action="store_true",
        help="show messages from all branches tagged with branch IDs",
    )

    filters = parser.add_argument_group("filters")
    filters.add_argument(
        "-n",
        "--name",
        action="store_true",
        default=None,
        help='always show chat name (or "Untitled" if none)',
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
    return parser


def to_markdown() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    args = build_parser().parse_args()

    chat = read(args.file)

    if args.branches:
        print_branches(chat)
    else:
        if args.user is args.assistant is args.thinking is None:
            args.user, args.assistant, args.thinking = True, True, False
        options = markdown.Options(args.user, args.assistant, args.thinking)
        if args.all_branches:
            markdown.write_all(chat, options, args.name)
        else:
            leaf = select_branch(chat, args.branch)
            markdown.write_branch(chat, leaf, options, args.name)
