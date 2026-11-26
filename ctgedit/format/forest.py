"""Walks over the record forest (a page's records in depth-first order)."""

from __future__ import annotations

from typing import List, Optional, Sequence


def subtree_end(records: Sequence, index: int) -> int:
    """One past the last record of ``records[index]``'s subtree (a count running past the end
    stops at the end)."""
    end, pending = index, 1
    n = len(records)
    while pending and end < n:
        pending += records[end].class_flag - 1
        end += 1
    return end


def whole_forest(counts: Sequence[int]) -> bool:
    """The child counts are whole trees: every group's members follow it within the list (the
    forest check of integrity.py; a list that is not would make a container take the records
    after it)."""
    open_slots = 0
    for c in counts:
        open_slots = max(open_slots - 1, 0) + c
    return open_slots == 0


def parents_from_counts(counts: Sequence[int]) -> List[Optional[int]]:
    """Each record's parent index (None = a root) from the records' child counts (over-long
    counts are clamped to the records that follow)."""
    parents: List[Optional[int]] = [None] * len(counts)
    stack: List[List[int]] = []  # [container index, children still to come]
    for i, c in enumerate(counts):
        while stack and stack[-1][1] <= 0:
            stack.pop()
        if stack:
            parents[i] = stack[-1][0]
            stack[-1][1] -= 1
        if c > 0:
            stack.append([i, c])
    return parents


def parents(records: Sequence) -> List[Optional[int]]:
    """Each record's parent index within ``records`` (None for a root, or for a record whose
    parent is not in the list)."""
    return parents_from_counts([r.class_flag for r in records])
