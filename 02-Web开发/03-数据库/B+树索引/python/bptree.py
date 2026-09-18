"""B+ Tree index - minimal implementation.

In-memory B+ Tree that mirrors the structure used by RDBMS storage engines
(InnoDB clustered index, SQLite, PostgreSQL heap+btree index).

Reference properties for an M-way B+ Tree (per CMU 15-445 Lecture 08,
Andy Pavlo + Jignesh Patel, Fall 2023 / OpenDSA 7.2 "B-Trees", Clifford
Shaffer):

  * Perfectly balanced: every leaf node at the same depth.
  * Inner node other than root: between ceil(M/2)-1 and M-1 keys
    (equivalently between ceil(M/2) and M children).
  * Inner node with k keys has k+1 children.
  * Data lives ONLY in leaf nodes; inner nodes are pure routing.
  * Leaf nodes are linked left-to-right via sibling pointers for range scans.

We fix M = 4 (a.k.a. "order 4") so each node fits at most 3 keys; this is
small enough to print legibly yet exercises split/merge.
"""

from __future__ import annotations

from typing import List, Tuple


M = 4
MAX_KEYS = M - 1
MIN_KEYS_LEAF = (M + 1) // 2 - 1
MIN_KEYS_INNER = M // 2 - 1


class Leaf:
    __slots__ = ("keys", "vals", "next")

    def __init__(self) -> None:
        self.keys: List[int] = []
        self.vals: List[str] = []
        self.next: "Leaf | None" = None


class Inner:
    __slots__ = ("keys", "children")

    def __init__(self) -> None:
        self.keys: List[int] = []
        self.children: List["Node"] = []


Node = Leaf | Inner


# ---------- core utilities ---------------------------------------------------

def is_leaf(n: Node) -> bool:
    return isinstance(n, Leaf)


def find_child_index(n: Inner, k: int) -> int:
    """Return the smallest i s.t. k < n.keys[i]; else len(n.keys)."""
    lo, hi = 0, len(n.keys)
    while lo < hi:
        mid = (lo + hi) // 2
        if n.keys[mid] <= k:
            lo = mid + 1
        else:
            hi = mid
    return lo


def find_key_index(arr: List[int], k: int) -> int:
    """Binary search the smallest i such that arr[i] >= k; else len(arr)."""
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < k:
            lo = mid + 1
        else:
            hi = mid
    return lo


# ---------- search / range scan --------------------------------

def search(root: Node, k: int) -> str | None:
    """Point query; search MUST reach a leaf (OpenDSA 7.2.1.1)."""
    n = root
    while not is_leaf(n):
        n = n.children[find_child_index(n, k)]  # type: ignore[assignment]
    i = find_key_index(n.keys, k)
    if i < len(n.keys) and n.keys[i] == k:
        return n.vals[i]
    return None


def range_query(root: Node, lo: int, hi_inclusive: int) -> List[Tuple[int, str]]:
    """Return [(k, v), ...] for lo <= k <= hi_inclusive, in ascending order.

    Descend to the first leaf whose smallest key >= lo, then walk the
    sibling chain emitting keys in range until we pass hi_inclusive.
    """
    if root is None:
        return []
    n = root
    while not is_leaf(n):
        n = n.children[find_child_index(n, lo)]  # type: ignore[assignment]
    out: List[Tuple[int, str]] = []
    while n is not None:
        for k, v in zip(n.keys, n.vals):
            if k < lo:
                continue
            if k > hi_inclusive:
                return out
            out.append((k, v))
        n = n.next  # type: ignore[assignment]
    return out


# ---------- split primitives ------------------------------------------------

def split_leaf(L: Leaf) -> Tuple[int, Leaf]:
    """Split an over-full leaf in half; return (copy-up key, new right leaf)."""
    mid = len(L.keys) // 2
    new = Leaf()
    new.keys = L.keys[mid:]
    new.vals = L.vals[mid:]
    L.keys = L.keys[:mid]
    L.vals = L.vals[:mid]
    new.next = L.next
    L.next = new
    return new.keys[0], new


def split_inner(I: Inner) -> Tuple[int, Inner]:
    """Split an over-full inner node; return (push-up key, new right inner)."""
    mid = len(I.keys) // 2
    up = I.keys[mid]
    new = Inner()
    new.keys = I.keys[mid + 1:]
    new.children = I.children[mid + 1:]
    I.keys = I.keys[:mid]
    I.children = I.children[:mid + 1]
    return up, new


# ---------- insert -----------------------------------------------------------

def insert(root: Node, k: int, v: str) -> Node:
    """Insert (k, v); return (possibly new) root."""
    path: List[Tuple[Inner, int]] = []
    n = root
    while not is_leaf(n):
        i = find_child_index(n, k)  # type: ignore[arg-type]
        path.append((n, i))         # type: ignore[arg-type]
        n = n.children[i]           # type: ignore[assignment]

    L = n  # type: ignore[assignment]
    i = find_key_index(L.keys, k)
    if i < len(L.keys) and L.keys[i] == k:
        L.vals[i] = v
        return root
    L.keys.insert(i, k)
    L.vals.insert(i, v)

    pushed_key: int | None = None
    new_node: Node | None = None
    if len(L.keys) > MAX_KEYS:
        pushed_key, new_node = split_leaf(L)  # type: ignore[arg-type]

    # `parent` is the inner node we are about to splice into; `idx` is its
    # child slot for the just-split child. After the splice, if `parent`
    # itself overflows, we split it and continue propagating.
    parent: Inner | None = None
    while pushed_key is not None and new_node is not None:
        if not path:
            # Root itself split -> new root of height + 1. We splice `parent`
            # (the just-split inner node, now mutated to the left half) and
            # `new_node` (the right half) under a fresh single-key root.
            # Using `L` here would double-link L into two places.
            new_root = Inner()
            new_root.keys = [pushed_key]  # type: ignore[list-item]
            new_root.children = [parent if parent is not None else L, new_node]  # type: ignore[list-item]
            return new_root
        parent, idx = path.pop()
        # Splice: insert separator key at position idx and new_node at idx+1.
        # idx is the original child slot of the just-split child within
        # parent; pushing the separator there shifts the old separator at idx
        # to idx+1 and keeps the keys sorted (the new separator is >= all keys
        # in the just-split left half and < the next sibling's keys).
        parent.keys.insert(idx, pushed_key)  # type: ignore[arg-type]
        parent.children.insert(idx + 1, new_node)  # type: ignore[arg-type]
        if len(parent.keys) <= MAX_KEYS:
            break
        pushed_key, new_node = split_inner(parent)  # type: ignore[arg-type]

    return root


# ---------- delete (implementation lives in bptree_delete.py) ---------------

# Re-exported so `from bptree import delete` keeps working for existing callers
# (demo.py). bptree_delete imports from this module, so the import must stay
# below every definition it needs - which it does, by construction.
from bptree_delete import _delete, delete  # noqa: E402

# ---------- bulk-load --------------------------------------------------------

def bulk_load(pairs: List[Tuple[int, str]]) -> Node:
    """Build a B+ Tree from a SORTED list of (key, value) pairs in O(N).

    Per Wikipedia/CMU: build leaves sequentially, then build inner levels
    bottom-up. This is dramatically faster than N times insert() because
    no path-tracking or split-on-the-way-up is needed.
    """
    if not pairs:
        return Leaf()
    leaves: List[Leaf] = []
    cur = Leaf()
    for k, v in pairs:
        if len(cur.keys) == MAX_KEYS:
            leaves.append(cur)
            cur = Leaf()
        cur.keys.append(k)
        cur.vals.append(v)
    if cur.keys:
        leaves.append(cur)
    for i in range(len(leaves) - 1):
        leaves[i].next = leaves[i + 1]

    level = leaves  # type: ignore[assignment]
    while len(level) > 1:
        new_level: List[Inner] = []
        i = 0
        while i < len(level):
            cur = Inner()
            cur.children = [level[i]]
            j = i + 1
            while j < len(level) and len(cur.keys) < MAX_KEYS:
                cur.keys.append(level[j].keys[0])
                cur.children.append(level[j])
                j += 1
            new_level.append(cur)
            i = j
        level = new_level  # type: ignore[assignment]

    return level[0]  # type: ignore[return-value]


# ---------- tree-shape helpers -----------------------------------------------

def count_leaves(n: Node) -> int:
    if is_leaf(n):
        return 1
    return sum(count_leaves(c) for c in n.children)  # type: ignore[union-attr]