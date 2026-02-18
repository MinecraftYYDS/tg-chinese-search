from __future__ import annotations


def build_fts_query(tokens: list[str]) -> str:
    cleaned = [t.replace('"', "").strip() for t in tokens if t.strip()]
    if not cleaned:
        return ""
    return " AND ".join(f'"{token}"*' for token in cleaned)

