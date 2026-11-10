from datetime import datetime
from pathlib import Path
from typing import cast

import re

from claude_backup import (
    Account,
    ChatsEntry,
    Client,
    Organization,
    SomeOrganization,
    Store,
)
from platformdirs import user_data_dir


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
