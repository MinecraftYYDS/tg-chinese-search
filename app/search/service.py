from __future__ import annotations

from dataclasses import dataclass

from app.search.query_builder import build_fts_query
from app.search.tokenizer import Tokenizer
from app.storage.repository import MessageRepository, SearchRow


@dataclass(slots=True)
class SearchService:
    repo: MessageRepository
    tokenizer: Tokenizer

    def _check_channel_allowed(self, chat_id: int | None) -> bool:
        """Check if a channel is allowed for search. Returns True if allowed."""
        if chat_id is None:
            return True
        return self.repo.is_channel_allowed(chat_id)

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
        
        # Check channel permission
        chat_id = self.repo.resolve_channel(channel_filter)
        if channel_filter is not None and chat_id is None:
            return []
        if chat_id is not None and not self._check_channel_allowed(chat_id):
            return []
        
        tokens = self.tokenizer.tokenize(query)
        fts_query = build_fts_query(tokens)
        if not fts_query:
            return []
        return self.repo.search(fts_query=fts_query, limit=limit, offset=offset, channel=channel_filter)

    def count(self, query: str, channel_filter: str | int | None = None) -> int:
        query = query.strip()
        if not query:
            return 0
        
        # Check channel permission
        chat_id = self.repo.resolve_channel(channel_filter)
        if channel_filter is not None and chat_id is None:
            return 0
        if chat_id is not None and not self._check_channel_allowed(chat_id):
            return 0
        
        tokens = self.tokenizer.tokenize(query)
        fts_query = build_fts_query(tokens)
        if not fts_query:
            return 0
        return self.repo.search_count(fts_query=fts_query, channel=channel_filter)

    def random(
        self,
        limit: int,
        channel_filter: str | int | None = None,
    ) -> list[SearchRow]:
        if limit <= 0:
            return []

        chat_id = self.repo.resolve_channel(channel_filter)
        if channel_filter is not None and chat_id is None:
            return []
        if chat_id is not None and not self._check_channel_allowed(chat_id):
            return []

        return self.repo.random_messages(limit=limit, channel=channel_filter)

    def random_count(self, channel_filter: str | int | None = None) -> int:
        chat_id = self.repo.resolve_channel(channel_filter)
        if channel_filter is not None and chat_id is None:
            return 0
        if chat_id is not None and not self._check_channel_allowed(chat_id):
            return 0
        return self.repo.random_count(channel=channel_filter)
