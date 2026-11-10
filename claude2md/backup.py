from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import BinaryIO, Callable, Protocol, cast

import base64
import json
import os
import re
import sys

from claude_backup import (
    Account,
    ChatsEntry,
    Client,
    Nameable,
    Organization,
    SomeOrganization,
    Store,
)
from platformdirs import user_data_dir

from claude2md.chat import File


def saved_chats() -> list[ChatsEntry]:
    store = Store(Path(user_data_dir("claude-backup")))
    chats: dict[str, ChatsEntry] = {}

    def standing(entry: ChatsEntry) -> tuple[bool, datetime]:
        return isinstance(entry.chat_list.organization, Organization), entry.updated_at

    chat_lists = [organization.chat_list() for organization in organizations(store)]
    for chat_list in (*chat_lists, SomeOrganization(store).chat_list()):
        for entry in chat_list.cached_entries():
            if not chat_file(entry).is_file():
                continue
            held = chats.get(entry.uuid)
            if held is None or standing(held) < standing(entry):
                chats[entry.uuid] = entry
    return sorted(chats.values(), key=lambda entry: entry.updated_at, reverse=True)


def organizations(store: Store) -> list[Organization]:
    return [
        membership.organization()
        for account in Account.load(cast(Client, None), store)
        for membership in account.memberships()
    ]


def store_of(export: Path) -> Store | None:
    directory = export.absolute().parent
    return Store(directory.parent) if directory.name == Store.COMPAT_VERSION else None


def chat_file(entry: ChatsEntry) -> Path:
    path = entry.store.store_dir / entry.chat_store_path()
    return path.with_name(f"{path.name}.json")


CHAT_URL = re.compile(r"(?:https?://)?claude\.ai/chat/([0-9a-f-]{36})")


def find_chats(query: str) -> list[ChatsEntry]:
    chats = saved_chats()
    query = query.casefold()
    if url := CHAT_URL.match(query):
        query = url[1]
    return (
        [entry for entry in chats if entry.uuid == query]
        or [entry for entry in chats if entry.name and entry.name.casefold() == query]
        or [
            entry
            for entry in chats
            if entry.uuid.startswith(query)
            or (entry.name or "").casefold().startswith(query)
        ]
    )


def uploads_dirs(path: str) -> tuple[Path, ...]:
    if path.startswith("<"):
        return (Path("files"),)
    chats = Path(path).absolute().parent
    found = (chats.parent / "files",)
    if chats.parent.name == "export" and (store := store_of(chats.parent)):
        found = (
            tuple(
                org / "files"
                for org in sorted(
                    store.store_dir / o.store_path() for o in organizations(store)
                )
                if (org / "files.json").is_file()
            )
            or found
        )
    if Path(path).is_absolute():
        return found
    return tuple(Path(os.path.relpath(files)) for files in found)


def media_type(file: BinaryIO) -> str | None:
    head = file.read(12)
    file.seek(0)
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    return None


# https://mimesniff.spec.whatwg.org/
WHATWG = [
    (b"\x00\x00\x01\x00", b"\xff\xff\xff\xff", "image/x-icon"),
    (b"\x00\x00\x02\x00", b"\xff\xff\xff\xff", "image/x-icon"),
    (b"BM", b"\xff\xff", "image/bmp"),
    (b"GIF87a", b"\xff\xff\xff\xff\xff\xff", "image/gif"),
    (b"GIF89a", b"\xff\xff\xff\xff\xff\xff", "image/gif"),
    (
        b"RIFF\x00\x00\x00\x00WEBPVP",
        b"\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff\xff\xff",
        "image/webp",
    ),
    (b"\x89PNG\r\n\x1a\n", b"\xff\xff\xff\xff\xff\xff\xff\xff", "image/png"),
    (b"\xff\xd8\xff", b"\xff\xff\xff", "image/jpeg"),
    (b"%PDF-", b"\xff\xff\xff\xff\xff", "application/pdf"),
]


def whatwg_type(file: BinaryIO) -> str | None:
    head = file.read(max(len(pattern) for pattern, _, _ in WHATWG))
    file.seek(0)
    for pattern, mask, kind in WHATWG:
        if len(head) >= len(pattern) and all(
            byte & m == p for byte, m, p in zip(head, mask, pattern)
        ):
            return kind
    return None


class TextSink(Protocol):
    def write(self, s: str, /) -> object: ...


def base64_stream(file: BinaryIO, out: TextSink) -> None:
    while block := file.read(3 * 16384):
        out.write(base64.b64encode(block).decode("ascii"))


@dataclass(frozen=True)
class Backup:
    files: tuple[Path, ...]
    lossy: bool = False

    @cached_property
    def index(self) -> dict[str, tuple[Path, str]]:
        index: dict[str, tuple[Path, str]] = {}
        for files in self.files:
            listing = files.parent / "files.json"
            try:
                text = listing.read_text(encoding="utf-8")
            except OSError as e:
                if not self.lossy:
                    raise
                print(f"lossy: {listing}: {e.strerror}", file=sys.stderr)
                continue
            for id, entry in json.loads(text).items():
                index.setdefault(id, (files, entry.get("file_name") or ""))
        return index

    def path(self, file: File) -> Path:
        try:
            files, name = self.index[file.id]
        except KeyError:
            if not self.lossy:
                listings = [str(files.parent / "files.json") for files in self.files]
                raise FileNotFoundError(
                    f"{file.id} is not in {' or '.join(listings)}"
                ) from None
            files, name = self.files[0], file.name
        slug = (
            f"{name.translate(Nameable.FILENAME_XLAT)}-{file.id}" if name else file.id
        )
        return files / slug

    def lost(self, what: str) -> None:
        if not self.lossy:
            raise FileNotFoundError(what)
        print(f"lossy: {what}", file=sys.stderr)

    def probe(
        self, file: File, sniff: Callable[[BinaryIO], str | None] = media_type
    ) -> tuple[str | None, int] | None:
        path = self.path(file)
        try:
            with path.open("rb") as f:
                return sniff(f), os.fstat(f.fileno()).st_size
        except OSError as e:
            if not self.lossy:
                raise
            print(f"lossy: {path}: {e.strerror}", file=sys.stderr)
            return None
