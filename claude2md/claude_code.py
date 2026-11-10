from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import groupby, pairwise
from json import JSONDecodeError
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Any, BinaryIO, Callable, Iterable, Iterator

import base64
import difflib
import io
import json
import math
import os
import re
import shutil
import uuid

from PIL import Image, ImageOps

from claude2md.backup import Backup
from claude2md.chat import (
    Block,
    Chat,
    Event,
    File,
    Gallery,
    Message,
    Role,
    SearchResult,
    ToolResult,
    ToolUse,
)
from claude2md.claude_ai import parse_content


def read(text: str) -> Chat:
    records = []
    for line in text.split("\n"):
        with suppress(JSONDecodeError):
            if isinstance(record := json.loads(line), dict):
                records.append(record)
    return parse(records)


def parse(records: Iterable[dict[str, Any]]) -> Chat:
    by_uuid: dict[str, Event] = {}
    messages: list[Message] = []
    turn: list[dict[str, Any]] = []
    explicit = None

    def flush() -> None:
        nonlocal turn
        if turn:
            message = assistant(turn, by_uuid.get(turn[0].get("parentUuid")))
            messages.append(message)
            for data in turn:
                by_uuid[data["uuid"]] = message
        turn = []

    for data in records:
        if data.get("type") == "last-prompt":
            explicit = data.get("leafUuid") if data.get("explicit") else None
        if "uuid" not in data or data.get("isSidechain"):
            continue
        if data.get("type") == "assistant" or is_results(data):
            turn.append(data)
        elif turn and data.get("type") != "user":
            turn.append(data)
        else:
            flush()
            parent = by_uuid.get(
                data.get("parentUuid") or data.get("logicalParentUuid")
            )
            event = user(data, parent) or Event(parent)
            by_uuid[data["uuid"]] = event
            if isinstance(event, Message):
                messages.append(event)
    flush()
    leaf = by_uuid[explicit].message if explicit in by_uuid else None
    return Chat(messages, leaf=leaf or next(reversed(messages), None))


def is_results(data: dict[str, Any]) -> bool:
    if data.get("type") != "user":
        return False
    content = (data.get("message") or {}).get("content")
    return (
        isinstance(content, list)
        and bool(content)
        and all(b.get("type") == "tool_result" for b in content)
    )


def user(data: dict[str, Any], parent: Event | None) -> Message | None:
    if data.get("type") != "user" or data.get("isMeta") or data.get("isCompactSummary"):
        return None
    api = data.get("message")
    if not isinstance(api, dict) or (content := api.get("content")) is None:
        return None
    if isinstance(content, str):
        blocks = [Block("text", content)]
    else:
        blocks = parse_content(content, results)
    return Message(
        parent,
        Role.USER,
        blocks,
        datetime.fromisoformat(data["timestamp"]),
        id=data["uuid"],
    )


def assistant(turn: list[dict[str, Any]], parent: Event | None) -> Message:
    content: list[dict[str, Any]] = []
    times: list[datetime] = []
    for data in turn:
        api = data.get("message")
        if not isinstance(api, dict) or not isinstance(api.get("content"), list):
            continue
        content += api["content"]
        times += [datetime.fromisoformat(data["timestamp"])] * len(api["content"])
    blocks = parse_content(content, results, times)
    return Message(
        parent,
        Role.ASSISTANT,
        blocks,
        datetime.fromisoformat(turn[0]["timestamp"]),
        id=turn[-1]["uuid"],
    )


def results(content: str | list[dict[str, Any]]) -> list[str | SearchResult]:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    parts: list[str | SearchResult] = []
    for b in content:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "search_result":
            body = "\n\n".join(c["text"] for c in b["content"])
            parts.append(SearchResult(b["title"], b["source"], body))
    return [p for p in parts if (p if isinstance(p, str) else p.text).strip()]


CLEARED = "[Old tool result content cleared]"
PAGE_TEXT_REMOVED = "[Page text removed]"
IMAGE_REMOVED = "[Image removed]"

IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
LOSSLESS = {"image/png", "image/gif"}
MAX_IMAGE = 5 * 1024 * 1024
MAX_SIDE = 2000
MAX_LONG_EDGE = 2576
MAX_VISUAL_TOKENS = 4784
MAX_REENCODED = 512000
SIZE_BY_QUALITY = {0: -3.17, 50: -1.76, 75: -1.47, 90: -0.81, 99: -0.02, 100: 0.0}
FIRST_QUALITY = 90

UNSIGNED = "none"

ENDED = {"end_turn": "end_turn", "stop_sequence": "end_turn", "refusal": "refusal"}

OWN = {"propose_skills"}

RENAMED = {"web_search": "WebSearch", "web_fetch": "WebFetch"}

ASKED = "The questions were shown to the user, who replies in their next message."


CWD = "/home/claude"
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/twilligon/claude2md")


def derive(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(map(str, parts))))


NAME_XLAT = {c: ord("_") for c in [*range(32), *map(ord, '<>:"/\\|?*#')]}
JS_SPACE = set(" \f\n\r\t\v\u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff") | {
    chr(c) for c in range(0x2000, 0x200B)
}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def mention(path: str) -> str:
    end = path[-1:]
    bare = end.isascii() and (end.isalnum() or end == "_") and not JS_SPACE & set(path)
    return f"@{path}" if bare else f'@"{path}"'


def stamp(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="milliseconds")[:-6] + "Z"


def hunks(old: str, new: str) -> list[dict[str, Any]]:
    patch: list[dict[str, Any]] = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""):
        if m := HUNK.match(line):
            old_start, old_len, new_start, new_len = (int(x or 1) for x in m.groups())
            patch.append(
                {
                    "oldStart": old_start + (old_len == 0),
                    "oldLines": old_len,
                    "newStart": new_start + (new_len == 0),
                    "newLines": new_len,
                    "lines": [],
                }
            )
        elif patch and line[:1] in ("+", "-", " "):
            patch[-1]["lines"].append(line)
    return patch


def claude_code_call(use: ToolUse) -> ToolUse:
    name, input = use.name, use.input
    if name == "ask_user_input_v0":
        if asked := questions(input):
            name, input = "AskUserQuestion", asked
    elif name in RENAMED:
        name = RENAMED[name]
    return ToolUse(use.id, name, input, use.at)


def questions(input: dict[str, Any]) -> dict[str, Any] | None:
    asked = input.get("questions")
    if set(input) != {"questions"} or not isinstance(asked, list):
        return None
    if not 1 <= len(asked) <= 4:
        return None
    out = []
    for q in asked:
        if not isinstance(q, dict) or not {"question", "options"} <= set(q):
            return None
        if set(q) - {"question", "options", "type"}:
            return None
        if q.get("type") not in (None, "single_select", "multi_select"):
            return None
        options = q["options"]
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            return None
        out.append(
            {
                "question": q["question"],
                "header": "",
                "options": [{"label": o, "description": ""} for o in options],
                "multiSelect": q.get("type") == "multi_select",
            }
        )
    return {"questions": out}


def kept(result: ToolResult, call: ToolUse) -> Any:
    text = result.text
    if result.is_error:
        return f"Error: {text}"
    match call.name:
        case "Bash":
            return {
                "stdout": text,
                "stderr": "",
                "interrupted": False,
                "isImage": False,
            }
        case "Edit":
            old, new = call.input["old_string"], call.input["new_string"]
            return {
                "filePath": call.input["file_path"],
                "oldString": old,
                "newString": new,
                "originalFile": None,
                "structuredPatch": hunks(old, new),
                "userModified": False,
                "replaceAll": call.input.get("replace_all") in (True, "true"),
            }
        case "ListAgents":
            return {"listing": text}
    return text


def base64_of(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def fit(file: BinaryIO, kind: str, size: int) -> tuple[str, str, dict[str, int]]:
    def as_is() -> str:
        file.seek(0)
        return base64.b64encode(file.read()).decode("ascii")

    with Image.open(file) as image:
        fits = base64_size(size) <= MAX_IMAGE and max(image.size) <= MAX_SIDE
        if fits and kind not in LOSSLESS:
            return as_is(), kind, dimensions(image.size, image.size)
        data, original, shown = recode(image, kind in LOSSLESS, fits)
        if fits and len(data) >= size:
            return as_is(), kind, dimensions(original, original)
        return (
            base64.b64encode(data).decode("ascii"),
            "image/webp",
            dimensions(original, shown),
        )


def recode(
    image: Image.Image, lossless: bool, fits: bool
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    icc = image.info.get("icc_profile")
    image = ImageOps.exif_transpose(image)
    original = image.size
    if image.mode.startswith("I"):
        image = image.convert("I").point(lambda v: v / 257).convert("L")
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if image.has_transparency_data else "RGB")
    if not fits:
        image = image.resize(seen(*original), Image.Resampling.LANCZOS)

    def webp(**options: Any) -> bytes:
        out = io.BytesIO()
        image.save(out, "WEBP", icc_profile=icc, **options)
        return out.getvalue()

    data = webp(lossless=True, exact=True) if lossless else None
    if not fits and (data is None or len(data) > MAX_REENCODED):
        data = lossy(webp)
    return data, original, image.size


def lossy(webp: Callable[..., bytes]) -> bytes:
    fit, unfit = -1, 101
    quality, data = FIRST_QUALITY, b""
    while True:
        attempt = webp(quality=quality)
        if len(attempt) <= MAX_REENCODED:
            fit, data = quality, attempt
        else:
            unfit = quality
        if unfit - fit == 1:
            return data if fit >= 0 else attempt
        room = typical_size(quality) + math.log(MAX_REENCODED / len(attempt))
        quality = max(
            (q for q in range(fit + 1, unfit) if typical_size(q) <= room),
            default=fit + 1,
        )


def typical_size(quality: int) -> float:
    (q0, s0), (q1, s1) = next(
        (a, b) for a, b in pairwise(SIZE_BY_QUALITY.items()) if quality <= b[0]
    )
    return s0 + (s1 - s0) * (quality - q0) / (q1 - q0)


def seen(width: int, height: int) -> tuple[int, int]:
    resized = resized_size(width, height, MAX_LONG_EDGE, MAX_VISUAL_TOKENS)
    if max(resized) <= MAX_SIDE:
        return resized
    short = max(round(MAX_SIDE / (max(width, height) / min(width, height))), 1)
    return (MAX_SIDE, short) if width >= height else (short, MAX_SIDE)


# https://platform.claude.com/docs/en/build-with-claude/vision-coordinates#resize-your-image-before-uploading


def count_image_tokens(width: int, height: int) -> int:
    """Visual tokens consumed by an image: one token per 28x28 pixel patch."""
    return math.ceil(width / 28) * math.ceil(height / 28)


def resized_size(
    width: int,
    height: int,
    max_edge: int = 1568,
    max_tokens: int = 1568,
) -> tuple[int, int]:
    """
    The size Claude resizes an image to before padding.

    Defaults are for the standard resolution tier. For high-resolution-tier
    models, use max_edge=2576 and max_tokens=4784. Returns (width, height).
    Images that already fit within the limits are returned unchanged.
    """

    def fits(w: int, h: int) -> bool:
        return (
            math.ceil(w / 28) * 28 <= max_edge
            and math.ceil(h / 28) * 28 <= max_edge
            and count_image_tokens(w, h) <= max_tokens
        )

    if fits(width, height):
        return (width, height)
    if height > width:
        resized_h, resized_w = resized_size(height, width, max_edge, max_tokens)
        return (resized_w, resized_h)

    aspect_ratio = width / height
    lo, hi = 1, width
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect_ratio), 1)):
            lo = mid
        else:
            hi = mid
    return (lo, max(round(lo / aspect_ratio), 1))


def base64_size(size: int) -> int:
    return (size + 2) // 3 * 4


def dimensions(original: tuple[int, int], shown: tuple[int, int]) -> dict[str, int]:
    return {
        "originalWidth": original[0],
        "originalHeight": original[1],
        "displayWidth": shown[0],
        "displayHeight": shown[1],
    }


def dump_json(rec: dict[str, Any], out: IO[str]) -> None:
    out.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def private_dir(path: Path) -> None:
    try:
        path.mkdir(0o700, exist_ok=True)
    except FileNotFoundError:
        private_dir(path.parent)
        path.mkdir(0o700, exist_ok=True)


@contextmanager
def save(path: Path, mode: str = "wb", **options: Any) -> Iterator[IO[Any]]:
    file = NamedTemporaryFile(
        mode, prefix=f".{path.name}-", dir=path.parent, delete=False, **options
    )
    try:
        with file:
            yield file
        Path(file.name).replace(path)
    except BaseException:
        Path(file.name).unlink(missing_ok=True)
        raise


@dataclass
class Session:
    chat: Chat
    backup: Backup
    claude: Path
    id: str
    named: str
    last: str | None = field(default=None, init=False)
    uploaded: set[str] = field(default_factory=set, init=False)

    def chain(self, rec: dict[str, Any]) -> dict[str, Any]:
        self.last = rec["uuid"]
        return rec

    def upload(self, name: str, id: str, source: Path | None) -> str:
        name = f"{id}-{name.translate(NAME_XLAT)}" if name else id
        folder = self.claude / "uploads" / self.id
        if source is not None and name not in self.uploaded:
            private_dir(folder)
            path = folder / name
            with source.open("rb") as file, save(path) as out:
                shutil.copyfileobj(file, out)
            st = source.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.uploaded.add(name)
        return f"{self.named}/uploads/{self.id}/{name}"

    def user(
        self, msg: Message, prompt_id: str, system_prompt: str | None = None
    ) -> Iterator[dict[str, Any]]:
        when = msg.created_at
        paths: list[str] = []
        attachments: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []

        def attach(path: str, kind: str, file: dict[str, Any]) -> None:
            attachments.append(
                {
                    "type": "file",
                    "filename": path,
                    "content": {"type": kind, "file": file},
                    "displayPath": path,
                }
            )

        def include(f: File, probed: tuple[str | None, int] | None) -> None:
            if probed is None:
                paths.append(self.upload(f.name, f.id, None))
                return
            kind, size = probed
            src = self.backup.path(f)
            path = self.upload(f.name, f.id, src)
            paths.append(path)
            if kind in IMAGES:
                with src.open("rb") as image:
                    data, kind, shown = fit(image, kind, size)
                file = {
                    "base64": data,
                    "type": kind,
                    "originalSize": size,
                    "dimensions": shown,
                }
                attach(path, "image", file)
            elif kind == "application/pdf":
                data = base64_of(src)
                file = {"filePath": path, "base64": data, "originalSize": size}
                attach(path, "pdf", file)
                source = {"type": "base64", "media_type": kind, "data": data}
                documents.append({"type": "document", "source": source, "title": path})

        for a in msg.attachments:
            file = File(a.name or "", "document", a.id)
            probed = self.backup.probe(file)
            if probed is None or probed[0] == "application/pdf":
                include(file, probed)
                continue
            src = self.backup.path(file)
            text = src.read_bytes().decode()
            lines = text.count("\n") + 1
            path = self.upload(file.name, file.id, src)
            paths.append(path)
            attach(
                path,
                "text",
                {
                    "filePath": path,
                    "content": text,
                    "numLines": lines,
                    "startLine": 1,
                    "totalLines": lines,
                },
            )
        for f in msg.files:
            include(f, self.backup.probe(f))
        if system_prompt is not None:
            parts = [system_prompt] if system_prompt else []
            attachments.append({"type": "prompt_snapshot", "systemPrompt": parts})

        texts = [
            b.content for b in msg.blocks if isinstance(b, Block) and b.type == "text"
        ]
        content: str | list[dict[str, Any]]
        content = " ".join([mention(p) for p in paths] + texts[:1])
        if documents:
            content = [*documents, {"type": "text", "text": content}]
        yield self.chain(
            {
                "parentUuid": self.last,
                "isSidechain": False,
                "promptId": prompt_id,
                "type": "user",
                "message": {"role": "user", "content": content},
                "uuid": derive(msg.id, "prompt"),
                "timestamp": stamp(when),
                "origin": {"kind": "human"},
                "promptSource": "typed",
                "turnOrigin": "human",
                "cwd": CWD,
                "sessionId": self.id,
            }
        )
        for n, attachment in enumerate(attachments):
            yield self.chain(
                {
                    "parentUuid": self.last,
                    "isSidechain": False,
                    "attachment": attachment,
                    "type": "attachment",
                    "uuid": derive(msg.id, "attachment", n),
                    "timestamp": stamp(when),
                    "cwd": CWD,
                    "sessionId": self.id,
                }
            )

    def result(
        self, result: ToolResult, call: ToolUse
    ) -> tuple[str | list[dict[str, Any]], Any]:
        result = ToolResult(
            result.tool_use,
            [p for p in result.content if not isinstance(p, Gallery)],
            result.is_error,
            result.at,
        )
        for p in result.content:
            if isinstance(p, SearchResult) and not (p.body.strip() or p.missing):
                self.backup.lost(f"{p.url}: no page text")
        asked = result.tool_use.name != call.name == "AskUserQuestion"
        if asked and not result.is_error:
            result = ToolResult(result.tool_use, [ASKED], result.is_error, result.at)
        if len(result.content) == 1 and isinstance(file := result.content[0], File):
            kind, size = self.backup.probe(file) or (None, 0)
            if kind in IMAGES:
                with self.backup.path(file).open("rb") as image:
                    data, kind, shown = fit(image, kind, size)
                source = {"type": "base64", "data": data, "media_type": kind}
                stored = {
                    "base64": "",
                    "type": kind,
                    "originalSize": size,
                    "dimensions": shown,
                }
                block = {"type": "image", "source": source}
                return [block], {"type": "image", "file": stored}
        stored = kept(result, call)
        match result.content:
            case [SearchResult(body=body)] if call.name == "WebFetch" and body:
                return body, stored
        alone = len(result.content) == 1
        blocks = [self.part(p, alone) for p in result.content]
        if pages := [p for p in result.content if isinstance(p, SearchResult)]:
            texts = [b["text"] for b in blocks if b["type"] == "text"]
            if any(not p.body and not p.missing for p in pages):
                texts.append(PAGE_TEXT_REMOVED)
            images = [b for b in blocks if b["type"] == "image"]
            blocks = [{"type": "text", "text": "\n\n".join(texts)}, *images]
        if not (call.name[:1].isupper() or call.name in OWN):
            return blocks, stored if result.is_error else blocks
        if len(blocks) == 1 and blocks[0]["type"] == "text":
            return blocks[0]["text"], stored
        return blocks, stored

    def part(self, part: str | File | SearchResult, alone: bool) -> dict[str, Any]:
        if isinstance(part, File):
            kind, size = self.backup.probe(part) or (None, 0)
            if kind not in IMAGES:
                return {"type": "text", "text": CLEARED if alone else IMAGE_REMOVED}
            with self.backup.path(part).open("rb") as image:
                data, kind, _ = fit(image, kind, size)
            source = {"type": "base64", "media_type": kind, "data": data}
            return {"type": "image", "source": source}
        return {"type": "text", "text": part if isinstance(part, str) else part.text}

    @staticmethod
    def message(
        id: str, block: dict[str, Any], stop_reason: str | None
    ) -> dict[str, Any]:
        return {
            "model": None,
            "id": id,
            "type": "message",
            "role": "assistant",
            "content": [block],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": None,
            "diagnostics": None,
        }

    def assistant(self, msg: Message, prompt_id: str) -> Iterator[dict[str, Any]]:
        blocks = msg.blocks

        place = {id(b): n for n, b in enumerate(blocks)}
        ids = {
            b: b.id or derive(msg.id, "tool_use", place[id(b)])
            for b in blocks
            if isinstance(b, ToolUse)
        }
        calls = {b: claude_code_call(b) for b in ids}

        turns = [
            (is_result, list(group))
            for is_result, group in groupby(
                blocks, key=lambda b: isinstance(b, ToolResult)
            )
        ]
        use_records: dict[ToolUse, str] = {}
        for n, (is_result, group) in enumerate(turns):
            if is_result:
                for b in group:
                    use = b.tool_use
                    content, stored = self.result(b, calls[use])
                    yield self.chain(
                        {
                            "parentUuid": self.last,
                            "isSidechain": False,
                            "promptId": prompt_id,
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "tool_use_id": ids[use],
                                        "type": "tool_result",
                                        **({"content": content} if content else {}),
                                        "is_error": b.is_error,
                                    }
                                ],
                            },
                            "uuid": derive(msg.id, "result", place[id(b)]),
                            "timestamp": stamp(b.at or msg.created_at),
                            "toolUseResult": stored,
                            "sourceToolAssistantUUID": use_records[use],
                            "cwd": CWD,
                            "sessionId": self.id,
                        }
                    )
                continue
            message_id = derive(msg.id, "message", n)
            for index, b in enumerate(group):
                if isinstance(b, ToolUse):
                    block = {
                        "type": "tool_use",
                        "id": ids[b],
                        "name": calls[b].name,
                        "input": calls[b].input,
                    }
                elif b.type == "thinking":
                    block = {
                        "type": "thinking",
                        "thinking": b.content,
                        "signature": UNSIGNED,
                    }
                else:
                    block = {"type": "text", "text": b.content}
                rec = self.chain(
                    {
                        "parentUuid": self.last,
                        "isSidechain": False,
                        "message": self.message(
                            message_id,
                            block,
                            (
                                "tool_use"
                                if n < len(turns) - 1
                                else ENDED.get(msg.stop_reason or "")
                            ),
                        ),
                        "apiBlockIndex": index,
                        "type": "assistant",
                        "uuid": derive(msg.id, "block", place[id(b)]),
                        "timestamp": stamp(b.at or msg.created_at),
                        "cwd": CWD,
                        "sessionId": self.id,
                    }
                )
                if isinstance(b, ToolUse):
                    use_records[b] = rec["uuid"]
                yield rec

    def records(
        self, leaf: Message, system_prompt: str | None = None
    ) -> Iterator[dict[str, Any]]:
        if self.chat.name:
            yield {"type": "ai-title", "aiTitle": self.chat.name, "sessionId": self.id}
        prompt_id = derive(self.id, "promptId")
        for msg in leaf.branch():
            if msg.role == Role.USER:
                prompt_id = derive(msg.id, "promptId")
                yield from self.user(msg, prompt_id, system_prompt)
                system_prompt = None
                continue
            yield from self.assistant(msg, prompt_id)
        last_prompt = leaf.preview(201)
        yield {
            "type": "last-prompt",
            **({"lastPrompt": last_prompt} if last_prompt else {}),
            "leafUuid": self.last,
            "sessionId": self.id,
        }


def claude_dir(cwd: Path) -> tuple[Path, str]:
    if directory := os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(directory), directory
    for directory in (cwd, *cwd.parents):
        if (directory / ".claude" / "projects").is_dir():
            return directory / ".claude", "~/.claude"
    return Path.home() / ".claude", "~/.claude"


def write(chat: Chat, backup: Backup, leaf: Message, system_prompt: str | None) -> str:
    session_id = str(uuid.uuid5(NAMESPACE, json.dumps([leaf.id, system_prompt])))
    home, named = claude_dir(Path.cwd())
    directory = home / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", CWD)
    path = directory / f"{session_id}.jsonl"
    if path.exists():
        return session_id
    session = Session(chat, backup, home, session_id, named)
    private_dir(directory)
    with save(path, "w", encoding="utf-8") as file:
        for rec in session.records(leaf, system_prompt):
            dump_json(rec, file)
    return session.id
