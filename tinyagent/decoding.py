"""Optional source-span constraints for versioned inference candidates.

These rules use source syntax and catalog field types, never expected labels.
They preserve full numeric literals and balanced quoted file names when the
learned span overlaps them. Unconstrained checkpoints retain the old decoder.
"""
import re

QUOTED = re.compile(r'"([^"\n]+)"|\x27([^\x27\n]+)\x27|\u201c([^\u201d\n]+)\u201d|\u2018([^\u2019\n]+)\u2019')
NUMBER = re.compile(r'(?<![\w.])(?:0[xX][0-9a-fA-F]+|[+-]?\d+)(?![\w.])')
HOST = re.compile(r'(?<![\w.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z0-9-]+(?![\w.-])')
FILE_SLOTS = {"path", "src", "dst", "file", "directory"}


def constrained_span(text, key, expected_types, start, stop, start_scores, end_scores, *, numeric=False):
    candidates = []
    if numeric and "int" in expected_types:
        candidates = [(m.start(), m.end()-1) for m in NUMBER.finditer(text)
                      if m.start() <= stop and m.end() > start]
    elif key in FILE_SLOTS:
        for match in QUOTED.finditer(text):
            if match.start() <= stop and match.end() > start:
                index = next(i for i in range(1, 5) if match.group(i) is not None)
                candidates.append((match.start(index), match.end(index)-1))
        # A span crossing multiple quoted operands is ambiguous; don't guess.
        if len(candidates) > 1:
            candidates = []
    elif key == "host":
        candidates = [(m.start(), m.end()-1) for m in HOST.finditer(text)
                      if m.start() <= stop and m.end() > start]
    if not candidates:
        return start, stop
    return max(candidates, key=lambda pair: float(start_scores[pair[0]] + end_scores[pair[1]]))
