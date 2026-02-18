from __future__ import annotations

from dataclasses import dataclass

from app.admin.auth import AdminAuthService
from app.admin.config_store import ConfigStore
from app.search.service import SearchService
from app.search.tokenizer import Tokenizer
from app.storage.repository import MessageRepository


@dataclass(slots=True)
class RuntimeContext:
    repo: MessageRepository
    tokenizer: Tokenizer
    search_service: SearchService
    admin_auth: AdminAuthService
    config_store: ConfigStore
    default_search_limit: int
    private_page_size: int
    proxy_fail_open: bool

