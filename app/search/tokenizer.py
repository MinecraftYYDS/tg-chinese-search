from __future__ import annotations

import re
from dataclasses import dataclass

import jieba


TOKEN_SPLIT_RE = re.compile(r"\s+")
NON_TOKEN_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(slots=True)
class Tokenizer:
    stopwords: set[str]

    def normalize_text(self, text: str) -> str:
        lowered = text.lower().strip()
        return NON_TOKEN_RE.sub(" ", lowered).strip()

    def tokenize(self, text: str) -> list[str]:
        normalized = self.normalize_text(text)
        if not normalized:
            return []
        base_tokens = [t.strip() for t in jieba.cut(normalized) if t.strip()]
        filtered = [t for t in base_tokens if t not in self.stopwords]
        ngrams: list[str] = []
        compact = TOKEN_SPLIT_RE.sub("", normalized)
        if len(compact) >= 2:
            ngrams.extend(compact[i : i + 2] for i in range(len(compact) - 1))
        return list(dict.fromkeys(filtered + ngrams))


def default_tokenizer() -> Tokenizer:
    return Tokenizer(stopwords={"的", "了", "和", "是", "在", "就", "都", "而", "及", "与"})
