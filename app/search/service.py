from __future__ import annotations

from dataclasses import dataclass

from app.search.query_builder import build_fts_query
from app.search.tokenizer import Tokenizer
from app.storage.repository import MessageRepository, SearchRow


@dataclass(slots=True)
class SearchService:
    repo: MessageRepository
    tokenizer: Tokenizer

    def search(
        self,
        query: str,
        limit: int,
        offset: int = 0,
        channel_filter: str | int | None = None,
    ) -> list[SearchRow]:
        query = query.strip()
        if not query:
            return []
        tokens = self.tokenizer.tokenize(query)
        fts_query = build_fts_query(tokens)
        if not fts_query:
            return []
        return self.repo.search(fts_query=fts_query, limit=limit, offset=offset, channel=channel_filter)

