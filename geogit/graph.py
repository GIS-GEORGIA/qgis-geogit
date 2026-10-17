"""Lane layout for drawing the commit graph row by row (no Qt here).

Commits come newest first, children before parents (git log --date-order).
Each row gets the column of its node plus the line segments to draw in the
upper half (top edge -> middle) and lower half (middle -> bottom edge).
"""

import hashlib
from dataclasses import dataclass, field


@dataclass
class GraphRow:
    col: int
    up: list = field(default_factory=list)     # [(col at top, col at middle)]
    down: list = field(default_factory=list)   # [(col at middle, col at bottom)]
    width: int = 1                             # columns used in this row
    is_merge: bool = False


def _free_slot(lanes):
    for i, sha in enumerate(lanes):
        if sha is None:
            return i
    lanes.append(None)
    return len(lanes) - 1


def layout(commits):
    """[GraphRow] for commits (objects with .sha and .parents), same order."""
    lanes = []
    known = {c.sha for c in commits}
    rows = []
    for c in commits:
        has_child = c.sha in lanes   # a commit above already points down to this one
        if has_child:
            col = lanes.index(c.sha)
        else:
            col = _free_slot(lanes)
            lanes[col] = c.sha
        before = list(lanes)

        row = GraphRow(col, is_merge=len(c.parents) > 1)
        for j, sha in enumerate(before):
            if sha is None:
                continue
            if sha != c.sha:
                row.up.append((j, j))
            elif j != col or has_child:
                row.up.append((j, col))
        # drop lanes that merged into this node
        for j, sha in enumerate(lanes):
            if sha == c.sha:
                lanes[j] = None

        parents = [p for p in c.parents if p in known]
        for n, p in enumerate(parents):
            if p in lanes:
                k = lanes.index(p)
            elif n == 0:
                k = col
                lanes[k] = p
            else:
                k = _free_slot(lanes)
                lanes[k] = p
            row.down.append((col, k))
        for j, sha in enumerate(lanes):
            if sha is not None and j < len(before) and before[j] == sha and sha != c.sha:
                row.down.append((j, j))

        while lanes and lanes[-1] is None:
            lanes.pop()
        used = [col] + [a for a, _ in row.up] + [b for _, b in row.up] + [b for _, b in row.down]
        row.width = max(used) + 1
        rows.append(row)
    return rows


# Distinct, readable on light and dark backgrounds.
PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf",
           "#e377c2", "#8c564b", "#bcbd22", "#3949ab", "#00897b", "#c2185b"]


def author_color(key):
    """Stable colour for an author (by e-mail), the same on every computer."""
    digest = hashlib.md5(key.strip().lower().encode("utf-8")).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def lane_color(col):
    return PALETTE[(col * 5) % len(PALETTE)]
