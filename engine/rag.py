"""Retrieval over the ASC 606 reference doc (data/asc606_reference_doc.md).

Deliberately simple, deterministic retrieval: the doc is split into sections
by markdown heading, and a query is matched by keyword overlap (with a boost
for heading matches). No embeddings, no external services — at ~9KB of
reference text, transparent keyword scoring is easier to audit than a vector
store and retrieves the right section reliably.

Used by two consumers:
  - engine/explain.py — grounds each obligation's classification rationale
  - the /api/chat endpoint — grounds every chat answer
"""
from __future__ import annotations

import os
import re

_DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "asc606_reference_doc.md")

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "be", "to", "of", "and", "or", "in",
    "on", "for", "it", "its", "this", "that", "with", "as", "by", "at", "from",
    "what", "how", "when", "which", "not", "but", "if", "can", "do", "does",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in _STOPWORDS]


class ReferenceDoc:
    def __init__(self, path: str = _DOC_PATH):
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.sections: list[dict] = []
        title, buf = "Preamble", []
        for line in raw.splitlines():
            if line.startswith("## "):
                if buf:
                    self._add(title, buf)
                title, buf = line[3:].strip(), []
            else:
                buf.append(line)
        self._add(title, buf)

    def _add(self, title: str, buf: list[str]):
        text = "\n".join(buf).strip()
        if text:
            self.sections.append({
                "title": title,
                "text": text,
                "_title_tokens": set(_tokens(title)),
                "_text_tokens": _tokens(text),
            })

    def retrieve(self, query: str, k: int = 3) -> list[dict]:
        """Top-k sections by keyword overlap; heading hits count 3x."""
        q = _tokens(query)
        scored = []
        for s in self.sections:
            score = sum(3 for t in set(q) if t in s["_title_tokens"])
            score += sum(1 for t in q if t in s["_text_tokens"])
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [{"title": s["title"], "text": s["text"]} for _, s in scored[:k]]

    def section(self, title_startswith: str) -> dict | None:
        for s in self.sections:
            if s["title"].lower().startswith(title_startswith.lower()):
                return {"title": s["title"], "text": s["text"]}
        return None

    def full_text(self) -> str:
        return "\n\n".join(f"## {s['title']}\n{s['text']}" for s in self.sections)


_doc: ReferenceDoc | None = None


def get_doc() -> ReferenceDoc:
    global _doc
    if _doc is None:
        _doc = ReferenceDoc()
    return _doc
