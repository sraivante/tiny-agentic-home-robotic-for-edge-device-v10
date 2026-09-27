"""Offline WordPiece encoding with exact original character offsets."""
from __future__ import annotations

import numpy as np


def encode_words(tokenizer, texts, char_length):
    encodings = tokenizer.encode_batch(texts)
    max_tokens = max(len(e.ids) for e in encodings)
    if max_tokens > 512:
        raise ValueError("A command exceeds the semantic encoder token limit")
    pieces = np.zeros((len(texts), max_tokens), dtype=np.int32)
    alignment = np.zeros((len(texts), char_length), dtype=np.int32)
    for row, encoding in enumerate(encodings):
        pieces[row, :len(encoding.ids)] = encoding.ids
        for token, (start, end) in enumerate(encoding.offsets):
            if end > start:
                alignment[row, start:min(end, char_length)] = token
    return pieces, alignment
