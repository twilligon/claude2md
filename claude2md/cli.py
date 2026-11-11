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
from claude2md.backup import Backup, chat_file, copies, find_chats, uploads_dirs
from claude2md.chat import Chat, Event, Message

DESCRIPTIONS = {
    "markdown": "Convert Claude.ai or Claude Code chats to Markdown",
    "claude-code": "Import a Claude.ai chat as a Claude Code session",
}


def read(file: IO[str], exported: str | None = None) -> Chat:
    text = file.read()
    return claude_ai.read(text, exported) or claude_code.read(text)


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


def print_chats(chats: list[ChatsEntry], file: IO[str] = sys.stdout) -> None:
    def name(saved: Nameable) -> str:
        return saved.name or saved.uuid

    def saved_under(entry: ChatsEntry) -> list[str]:
        organization = entry.chat_list.organization
        if not isinstance(organization, Organization):
            return ["(export)", "(export)"]
        return [name(organization.account), name(organization)]

    def branch(entry: ChatsEntry) -> Event | None:
        chat = claude_ai.read(chat_file(entry).read_text(encoding="utf-8"))
        return chat.branch if chat else None

    columns = [[entry.uuid for entry in chats], *zip(*map(saved_under, chats))]
    shown = columns[:1] + [column for column in columns[1:] if len(set(column)) > 1]
    print_rows(zip(map(list, zip(*shown)), map(branch, chats)), file)


def select_chat(query: str) -> str:
    chats = find_chats(query)
    if len(chats) == 1:
        return str(chat_file(chats[0]))
    if chats:
        print(
            f'Error: Multiple saved chats match "{query}"; specify one by its ID',
            file=sys.stderr,
        )
        print_chats(chats, sys.stderr)
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
        default="-",
        help="chat JSON, UUID, name, or URL (default: stdin)",
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
        "--lossy",
        action="store_true",
        help="make do without what the backup lacks",
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
    return parser


def main(to: str) -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    parser = build_parser(to)
    args = parser.parse_args()

    path = args.chat
    if path != "-" and not os.path.exists(path):
        path = select_chat(path)
    path, exported = (path, None) if path == "-" else copies(path)
    try:
        with sys.stdin if path == "-" else open(path, encoding="utf-8") as file:
            chat = read(file, exported and Path(exported).read_text(encoding="utf-8"))
    except OSError as e:
        sys.exit(f"Error: {e}")
    except ValueError as e:
        parser.error(str(e))
    backup = Backup(uploads_dirs(file.name), args.lossy)

    try:
        if args.branches:
            print_branches(chat)
        elif to == "markdown":
            if args.user is args.assistant is args.thinking is None:
                args.user, args.assistant, args.thinking = True, True, False
            options = markdown.Options(
                args.user, args.assistant, args.thinking, args.embed
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
