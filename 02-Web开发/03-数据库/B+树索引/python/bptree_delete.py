"""B+ Tree delete path - borrow / merge rebalancing.

Moved verbatim out of bptree.py to respect the <=300 line hard limit
(_docs/OPTIMIZATION.md 1.1). Only the section header changed; every line
below is byte-identical to lines 200..325 of the original file.

Deleting a key from an M-way B+ Tree must keep every node at or above its
minimum occupancy. Two repair strategies, tried in this order:

  * borrow (redistribute) - some sibling holds MORE than the minimum, so one
    entry (leaf) or one child (inner) moves across the parent's separator,
    and the separator itself is rewritten in place;
  * merge - no sibling can spare anything, so two nodes are fused and the
    parent's separator is dropped. That leaves the PARENT one short, so
    _delete recurses upward until a level is stable, or the root collapses.

The two helpers are chosen by _rebalance_after_delete: it tries a borrow
first and falls back to a merge when the borrow raises RuntimeError (the
signal used by the two _redistribute_* functions when they find no donor).
"""

from __future__ import annotations

from bptree import (
    MIN_KEYS_INNER,
    MIN_KEYS_LEAF,
    Inner,
    Node,
    find_child_index,
    find_key_index,
    is_leaf,
)
# ---------- delete helpers ---------------------------------------------------

def _left_sibling(parent: Inner, idx: int):
    return parent.children[idx - 1] if idx > 0 else None


def _right_sibling(parent: Inner, idx: int):
    return parent.children[idx + 1] if idx + 1 < len(parent.children) else None


def _redistribute_leaf(parent: Inner, idx: int) -> None:
    """Borrow one entry from a sibling so both leaves stay >= half full."""
    L = parent.children[idx]  # type: ignore[assignment]
    rsib = _right_sibling(parent, idx)
    if rsib is not None and len(rsib.keys) > MIN_KEYS_LEAF:  # type: ignore[union-attr]
        L.keys.append(rsib.keys.pop(0))  # type: ignore[union-attr]
        L.vals.append(rsib.vals.pop(0))  # type: ignore[union-attr]
        parent.keys[idx] = rsib.keys[0]  # type: ignore[union-attr]
        return
    lsib = _left_sibling(parent, idx)
    if lsib is not None and len(lsib.keys) > MIN_KEYS_LEAF:  # type: ignore[union-attr]
        L.keys.insert(0, lsib.keys.pop())  # type: ignore[union-attr]
        L.vals.insert(0, lsib.vals.pop())  # type: ignore[union-attr]
        parent.keys[idx - 1] = L.keys[0]
        return
    raise RuntimeError("redistribute called but no sibling has spare entries")


def _merge_leaf(parent: Inner, idx: int) -> None:
    """Merge L with one of its siblings; pop the parent's separator."""
    L = parent.children[idx]  # type: ignore[assignment]
    if idx > 0:
        lsib = parent.children[idx - 1]  # type: ignore[assignment]
        lsib.keys.extend(L.keys)  # type: ignore[union-attr]
        lsib.vals.extend(L.vals)  # type: ignore[union-attr]
        lsib.next = L.next  # type: ignore[union-attr]
        delete_idx = idx
    else:
        rsib = parent.children[idx + 1]  # type: ignore[assignment]
        L.keys.extend(rsib.keys)  # type: ignore[union-attr]
        L.vals.extend(rsib.vals)  # type: ignore[union-attr]
        L.next = rsib.next  # type: ignore[union-attr]
        delete_idx = idx + 1
    parent.keys.pop(delete_idx - 1)
    parent.children.pop(delete_idx)


def _redistribute_inner(parent: Inner, idx: int) -> None:
    """Borrow one child from a sibling through the parent's separator key."""
    N = parent.children[idx]
    rsib = _right_sibling(parent, idx)
    if rsib is not None and len(rsib.keys) > MIN_KEYS_INNER:  # type: ignore[union-attr]
        N.keys.append(parent.keys[idx])  # type: ignore[union-attr]
        N.children.append(rsib.children.pop(0))  # type: ignore[union-attr]
        parent.keys[idx] = rsib.keys.pop(0)  # type: ignore[union-attr]
        return
    lsib = _left_sibling(parent, idx)
    if lsib is not None and len(lsib.keys) > MIN_KEYS_INNER:  # type: ignore[union-attr]
        N.keys.insert(0, parent.keys[idx - 1])  # type: ignore[union-attr]
        N.children.insert(0, lsib.children.pop())  # type: ignore[union-attr]
        parent.keys[idx - 1] = lsib.keys.pop()  # type: ignore[union-attr]
        return
    raise RuntimeError("redistribute inner: no sibling has spare keys")


def _merge_inner(parent: Inner, idx: int) -> None:
    """Merge N with a sibling, pulling down the parent's separator key."""
    N = parent.children[idx]
    if idx > 0:
        lsib = parent.children[idx - 1]
        lsib.keys.append(parent.keys.pop(idx - 1))
        lsib.keys.extend(N.keys)
        lsib.children.extend(N.children)
        delete_idx = idx
    else:
        rsib = parent.children[idx + 1]
        N.keys.append(parent.keys.pop(idx))
        N.keys.extend(rsib.keys)
        N.children.extend(rsib.children)
        delete_idx = idx + 1
    parent.children.pop(delete_idx)


def _rebalance_after_delete(parent: Inner, idx: int) -> None:
    """Rebalance parent.children[idx] (just lost a key) via borrow or merge."""
    child = parent.children[idx]
    try:
        if is_leaf(child):
            _redistribute_leaf(parent, idx)
        else:
            _redistribute_inner(parent, idx)
        return
    except RuntimeError:
        pass
    if is_leaf(child):
        _merge_leaf(parent, idx)
    else:
        _merge_inner(parent, idx)


def _delete(n: Node, k: int) -> Node:
    """Recursive delete; mutates the subtree rooted at n; returns it."""
    if is_leaf(n):
        i = find_key_index(n.keys, k)
        if i >= len(n.keys) or n.keys[i] != k:
            return n
        n.keys.pop(i)
        n.vals.pop(i)
        return n

    i = find_child_index(n, k)
    child = n.children[i]
    _delete(child, k)
    min_keys = MIN_KEYS_LEAF if is_leaf(child) else MIN_KEYS_INNER
    if len(child.keys) < min_keys:
        _rebalance_after_delete(n, i)
    return n


def delete(root: Node, k: int) -> Node:
    """Delete key k; collapse root if it becomes empty."""
    root = _delete(root, k)
    if not is_leaf(root) and len(root.keys) == 0:
        root = root.children[0]
    return root

