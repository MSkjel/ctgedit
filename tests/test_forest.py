"""Record forest walks."""

import random

from ctgedit.format import forest as F


class _R:
    def __init__(self, c):
        self.class_flag = c


def test_parents_and_subtree_ends_agree_on_random_forests():
    """A record's subtree is it and exactly the records whose parent chain reaches it; the
    counts may run past the end (malformed)."""
    random.seed(7)
    for _trial in range(2000):
        n = random.randint(0, 30)
        recs = [_R(random.choice([0, 0, 0, 1, 2, 3, 40])) for _ in range(n)]
        par = F.parents(recs)
        assert par == F.parents_from_counts([r.class_flag for r in recs])
        for i in range(n):
            end = F.subtree_end(recs, i)
            inside = set()
            for j in range(n):
                k = j
                while k is not None and k != i:
                    k = par[k]
                if k == i:
                    inside.add(j)
            assert inside == set(range(i, end)), (i, [r.class_flag for r in recs])
