from __future__ import annotations

from dataclasses import dataclass
import re


KEYWORD_SPLIT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(slots=True)
class ParsedQuery:
    channel: str | None
    query: str


@dataclass(slots=True)
class ParsedRandomInput:
    channel: str | None
    limit: int


def parse_search_input(text: str, mode: str) -> ParsedQuery:
    raw = text.strip()
    if not raw:
        return ParsedQuery(channel=None, query="")
    if mode == "command" and raw.startswith("/search"):
        raw = raw[len("/search") :].strip()

    first, _, rest = raw.partition(" ")
    if mode in {"private", "command"} and first.startswith("@"):
        return ParsedQuery(channel=first, query=rest.strip())
    if mode == "inline" and first.startswith("#"):
        return ParsedQuery(channel=first[1:], query=rest.strip())
    return ParsedQuery(channel=None, query=raw)


def extract_keywords(query: str) -> list[str]:
    return [item for item in KEYWORD_SPLIT_RE.split(query.strip()) if item]


def parse_random_command_input(text: str, default_limit: int, max_limit: int) -> ParsedRandomInput:
    raw = text.strip()
    if not raw:
        return ParsedRandomInput(channel=None, limit=default_limit)

    first_token = raw.split(" ", 1)[0]
    if first_token.startswith("/sj"):
        raw = raw[len(first_token) :].strip()
    if not raw:
        return ParsedRandomInput(channel=None, limit=default_limit)

    tokens = [item for item in raw.split() if item]
    if len(tokens) > 2:
        raise ValueError("Usage: /sj [#channel|@channel|-100chat_id] [条数]")

    channel: str | None = None
    limit: int = default_limit
    seen_limit = False

    for token in tokens:
        if token.isdigit():
            if seen_limit:
                raise ValueError("条数参数重复。")
            parsed_limit = int(token)
            if parsed_limit <= 0:
                raise ValueError("条数必须大于 0。")
            if parsed_limit > max_limit:
                raise ValueError(f"条数上限为 {max_limit}。")
            limit = parsed_limit
            seen_limit = True
            continue

        looks_like_channel = token.startswith("@") or token.startswith("#") or (
            token.startswith("-") and token[1:].isdigit()
        )
        if not looks_like_channel:
            raise ValueError("随机模式不支持关键词，仅支持频道和条数。")
        if channel is not None:
            raise ValueError("频道参数重复。")
        channel = token

    return ParsedRandomInput(channel=channel, limit=limit)
