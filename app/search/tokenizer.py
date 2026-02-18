from __future__ import annotations

import re
from dataclasses import dataclass

import jieba


TOKEN_SPLIT_RE = re.compile(r"\s+")


@dataclass(slots=True)
class Tokenizer:
    stopwords: set[str]

    def tokenize(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        base_tokens = [t.strip().lower() for t in jieba.cut(text) if t.strip()]
        filtered = [t for t in base_tokens if t not in self.stopwords]
        ngrams: list[str] = []
        compact = TOKEN_SPLIT_RE.sub("", text)
        if len(compact) >= 2:
            ngrams.extend(compact[i : i + 2].lower() for i in range(len(compact) - 1))
        return list(dict.fromkeys(filtered + ngrams))


def default_tokenizer() -> Tokenizer:
    return Tokenizer(stopwords={"的", "了", "和", "是", "在", "就", "都", "而", "及", "与"})

