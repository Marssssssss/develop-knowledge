"""B+ Tree demo - visualization and CLI examples.

Imports the B+ Tree implementation from bptree.py and runs five short
demos that exercise insert, point search, range scan, delete with
redistribute, and bulk-load.

Run: python3 demo.py
"""

from __future__ import annotations

from bptree import (
    Inner, Leaf, Node, bulk_load, count_leaves, delete, insert,
    is_leaf, range_query, search,
)


def visualize(root: Node, prefix: str = "", is_last: bool = True) -> None:
    """Print a sideways ASCII tree (└── / ├── branch glyphs)."""
    branch = prefix + ("└── " if is_last else "├── ")
    if is_leaf(root):
        leaf_str = ", ".join(f"{k}:{v}" for k, v in zip(root.keys, root.vals))
        print(branch + f"[leaf {leaf_str}]" + (" -> next" if root.next else ""))
        return
    print(branch + f"[inner keys={root.keys}]")
    next_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(root.children):
        visualize(child, next_prefix, i == len(root.children) - 1)


def demo_insert_sequence() -> Node:
    """Insert 1..20 in ascending order; triggers the right-most split chain."""
    print("=== demo 1: insert sequence, watch splits propagate ===")
    root: Node = Leaf()
    for k in range(1, 21):
        root = insert(root, k, f"v{k}")
    visualize(root)
    print()
    return root


def demo_point_search(root: Node) -> None:
    """Point lookups: hits, misses, edge cases."""
    print("=== demo 2: point search ===")
    for k in [3, 11, 20, 99]:
        print(f"  search({k}) -> {search(root, k)!r}")
    print()


def demo_range_query(root: Node) -> None:
    """Range scan uses the leaf sibling chain after descending to lo."""
    print("=== demo 3: range query (lo=7, hi=15) via sibling chain ===")
    print("  ", range_query(root, 7, 15))
    print()


def demo_delete_redistribute() -> None:
    """Bulk-load a small tree, then delete to force redistribute from sibling."""
    print("=== demo 4: delete with borrow from sibling ===")
    pairs = [(i, f"v{i}") for i in [5, 8, 1, 7, 3, 12, 9, 14, 6, 11]]
    root = bulk_load(sorted(pairs))
    print("before delete:")
    visualize(root)
    for k in [1, 3, 5]:
        root = delete(root, k)
    print("after deleting 1, 3, 5:")
    visualize(root)
    print()


def demo_bulk_load() -> None:
    """Bulk-load 1..30 shows O(N) build vs N times insert."""
    print("=== demo 5: bulk load (sorted input -> O(N) build) ===")
    pairs = [(i, f"val{i}") for i in range(1, 31)]
    root = bulk_load(pairs)
    print(f"  root keys = {root.keys}, total leaves = {count_leaves(root)}")
    print(f"  range_query(10, 25) -> {range_query(root, 10, 25)}")


def main() -> None:
    root = demo_insert_sequence()
    demo_point_search(root)
    demo_range_query(root)
    demo_delete_redistribute()
    demo_bulk_load()


if __name__ == "__main__":
    main()