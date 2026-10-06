"""Okapi BM25 over a small candidate set (BE-09, #27): no vector store, no reranker model.

The corpus is only the candidates retrieved for one claim, so document frequencies come
from that set. Scores are relative; callers compare them within one ranking only.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Final

K1: Final = 1.5
B: Final = 0.75
_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS: Final = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to "
    "was were will with which who what when where how than then there these those not no "
    "but if into about over more most less per".split()
)


def tokens(text: str) -> list[str]:
    return [token for token in _TOKEN.findall(text.lower()) if token not in STOPWORDS]


def scores(query: str, documents: Sequence[str]) -> list[float]:
    """BM25 score of every document for ``query``, in input order."""
    terms = set(tokens(query))
    if not documents or not terms:
        return [0.0] * len(documents)
    bags = [Counter(tokens(document)) for document in documents]
    lengths = [sum(bag.values()) for bag in bags]
    average = sum(lengths) / len(lengths) or 1.0
    count = len(documents)
    idf = {
        term: math.log((count - df + 0.5) / (df + 0.5) + 1)
        for term in terms
        for df in [sum(1 for bag in bags if term in bag)]
    }
    result = []
    for bag, length in zip(bags, lengths, strict=True):
        total = 0.0
        for term in terms:
            frequency = bag.get(term, 0)
            if frequency:
                total += (
                    idf[term]
                    * frequency
                    * (K1 + 1)
                    / (frequency + K1 * (1 - B + B * length / average))
                )
        result.append(total)
    return result


def rank(query: str, documents: Sequence[str]) -> list[tuple[int, float]]:
    """Indices with their scores, best first; ties keep input order."""
    scored = list(enumerate(scores(query, documents)))
    return sorted(scored, key=lambda item: (-item[1], item[0]))
