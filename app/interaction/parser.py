from __future__ import annotations

from dataclasses import dataclass
import re


KEYWORD_SPLIT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(slots=True)
class ParsedQuery:
    channel: str | None
    query: str


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
