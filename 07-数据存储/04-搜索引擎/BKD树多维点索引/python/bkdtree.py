"""BKD 树的构建、索引前缀编码与范围查询遍历。"""

from bkd import (get_num_left_leaf_nodes, needs_exact_bounds,
                 choose_split_dim, common_prefix_length, num_leaves_of)


class Leaf(object):
    def __init__(self, points, common_prefix_lengths, sorted_dim,
                 leaf_cardinality):
        self.kind = "leaf"
        self.points = points
        self.common_prefix_lengths = common_prefix_lengths
        self.sorted_dim = sorted_dim
        self.leaf_cardinality = leaf_cardinality


class Inner(object):
    def __init__(self, split_dim, split_value, left, right):
        self.kind = "inner"
        self.split_dim = split_dim
        self.split_value = split_value
        self.left = left
        self.right = right


def _bounds(points, num_dims):
    mins = [min(p[d] for p in points) for d in range(num_dims)]
    maxs = [max(p[d] for p in points) for d in range(num_dims)]
    return mins, maxs


def leaves_of(node):
    """先序收集所有叶子节点。"""
    if node.kind == "leaf":
        return [node]
    return leaves_of(node.left) + leaves_of(node.right)


def _leaf_stats(points, cfg):
    """叶子节点：算公共前缀、挑 sortedDim、数基数。

    points 为 [(doc_id, (v0, ...)), ...]。两处**照抄源码的「偏心」细节**：
      * usedBytes 只统计 **i >= 1** 的点（源码 `for (int i = from + 1; ...)`），
        第一个点不参与基数统计；
      * leafCardinality 从 1 起算，按**整点**（不含 docID）去重。
    """
    n = cfg.num_dims
    bpd = cfg.bytes_per_dim
    vals = [p[1] for p in points]
    cpl = [bpd] * n
    for i in range(1, len(vals)):
        for d in range(n):
            c = common_prefix_length(vals[0][d], vals[i][d], bpd)
            if c < cpl[d]:
                cpl[d] = c
    used = {}
    for d in range(n):
        if cpl[d] < bpd:
            seen = set()
            for v in vals[1:]:          # 源码从 from+1 起
                seen.add(_to_bytes_at(v[d], bpd, cpl[d]))
            used[d] = seen
    sorted_dim = 0
    best = None
    for d in range(n):
        if d in used:
            if best is None or len(used[d]) < best:
                best = len(used[d])
                sorted_dim = d
    ordered = sorted(points, key=lambda p: p[1][sorted_dim])
    card = 1
    for i in range(1, len(ordered)):
        if ordered[i][1] != ordered[i - 1][1]:
            card += 1
    return Leaf(list(ordered), cpl, sorted_dim, card)


def _to_bytes_at(value, bytes_per_dim, index):
    return (value >> (8 * (bytes_per_dim - 1 - index))) & 0xFF


def build_tree(points, cfg):
    """BKDWriter.build：points 为 [(doc_id, (v0, v1, ...)), ...]。"""
    pts = [p[1] for p in points]
    docs = [p[0] for p in points]
    total = num_leaves_of(len(pts), cfg.max_points_in_leaf_node)
    mins, maxs = _bounds(pts, cfg.num_dims)
    parent_splits = [0] * cfg.num_index_dims
    root = _build(pts, docs, 0, len(pts), total, total, mins, maxs,
                  parent_splits, cfg)
    return root


def _build(pts, docs, frm, to, num_leaves, total_leaves, mins, maxs,
           parent_splits, cfg):
    if num_leaves == 1:
        return _leaf_stats(list(zip(docs[frm:to], pts[frm:to])), cfg)

    if cfg.num_index_dims == 1:
        split_dim = 0
    else:
        if needs_exact_bounds(num_leaves, total_leaves, cfg.num_index_dims,
                              parent_splits):
            mins, maxs = _bounds(pts[frm:to], cfg.num_dims)
        split_dim = choose_split_dim(mins, maxs, parent_splits,
                                     cfg.num_index_dims)

    num_left = get_num_left_leaf_nodes(num_leaves)
    mid = frm + num_left * cfg.max_points_in_leaf_node
    # numLeft < numLeaves 且 numLeaves = ceil(N/max) => mid 落在 (frm, to) 内
    assert frm < mid < to

    # MutablePointTreeReaderUtils.partition：按 splitDim 把中位数放到 mid
    seg = sorted(zip(pts[frm:to], docs[frm:to]), key=lambda t: t[0][split_dim])
    for i, (pv, dv) in enumerate(seg):
        pts[frm + i] = pv
        docs[frm + i] = dv
    split_value = pts[mid][split_dim]

    left_maxs = list(maxs)
    left_maxs[split_dim] = split_value
    right_mins = list(mins)
    right_mins[split_dim] = split_value

    parent_splits[split_dim] += 1
    left = _build(pts, docs, frm, mid, num_left, total_leaves, mins,
                  left_maxs, parent_splits, cfg)
    right = _build(pts, docs, mid, to, num_leaves - num_left, total_leaves,
                   right_mins, maxs, parent_splits, cfg)
    parent_splits[split_dim] -= 1
    return Inner(split_dim, split_value, left, right)


# --------------------------------------------------------------------------
# recursePackIndex：split 值按维度做前缀编码
# --------------------------------------------------------------------------

def pack_index(root, cfg):
    """BKDWriter.recursePackIndex：把 split 值按维度做前缀编码。

    每个内节点写三样东西：
      1. 一个 vInt `code` = (firstDiffByteDelta * (1 + bytesPerDim) + prefix) * numIndexDims + splitDim
         —— 「差多少」「公共前缀多长」「切哪个维度」塞进同一个整数；
      2. split 值在公共前缀之后的**后缀字节**（第 prefix 个字节本身靠 delta 还原，所以只写
         bytesPerDim - prefix - 1 个）；
      3. 左子树字节数（用于只需下右子树时快速 seek）。

    negativeDeltas[dim] 在**左**孩子无条件置 true、**右**孩子无条件置 false —— 因为沿
    根到叶的路径上同一维度的 split 值单调（左递减、右递增），取反后 delta 恒为正
    （源码 `assert firstDiffByteDelta > 0`）。
    """
    last = [0] * (cfg.num_index_dims * cfg.bytes_per_dim)
    out = []

    def walk(node, negative_deltas):
        if node.kind != "inner":
            return
        dim = node.split_dim
        bpd = cfg.bytes_per_dim
        base = dim * bpd
        cur = _to_bytes_list(node.split_value, bpd)
        prev = last[base:base + bpd]
        prefix = common_prefix_length(node.split_value, _from_bytes(prev), bpd)
        if prefix < bpd:
            delta = cur[prefix] - prev[prefix]
            if negative_deltas[dim]:
                delta = -delta
            assert delta > 0, "源码 assert firstDiffByteDelta > 0"
        else:
            delta = 0
        code = (delta * (1 + bpd) + prefix) * cfg.num_index_dims + dim
        out.append({
            "prefix": prefix,
            "delta": delta,
            "split_dim": dim,
            "code": code,
            "suffix": cur[prefix + 1:],     # 需落盘的 bytesPerDim-prefix-1 个字节
        })
        saved = list(prev)
        last[base:base + bpd] = cur

        neg_left = list(negative_deltas)
        neg_left[dim] = True                # 左孩子：无条件 true
        walk(node.left, neg_left)

        neg_right = list(negative_deltas)
        neg_right[dim] = False               # 右孩子：无条件 false
        walk(node.right, neg_right)

        # 两个孩子都看过之后才还原（源码在右递归之后才 System.arraycopy 还原）
        last[base:base + bpd] = saved

    walk(root, [False] * cfg.num_index_dims)
    return out


def _to_bytes_list(value, bytes_per_dim):
    return [(value >> (8 * (bytes_per_dim - 1 - i))) & 0xFF
            for i in range(bytes_per_dim)]


def _from_bytes(bs):
    v = 0
    for b in bs:
        v = (v << 8) | b
    return v


# --------------------------------------------------------------------------
# BKDReader：带包围盒下推的范围查询
# --------------------------------------------------------------------------

def intersect(root, cfg, mins, maxs, qmin, qmax):
    """按 BKDReader.pushBoundsLeft/Right 的语义下推包围盒并剪枝。

    pushBoundsLeft 把 **maxPackedValue[splitDim]** 换成 splitValue；
    pushBoundsRight 把 **minPackedValue[splitDim]** 换成 splitValue。
    返回 (命中文档号列表, 统计)。
    """
    hits = []
    stats = {"leaves": 0, "inner": 0, "pruned": 0, "scanned": 0}

    def rel(lo, hi):
        """PointValues.Relation：CELL_INSIDE_QUERY / OUTSIDE / CROSSES。"""
        all_in = True
        any_in = True
        for d in range(cfg.num_dims):
            if hi[d] < qmin[d] or lo[d] > qmax[d]:
                any_in = False
                break
            if not (lo[d] >= qmin[d] and hi[d] <= qmax[d]):
                all_in = False
        if not any_in:
            return "OUTSIDE"
        return "INSIDE" if all_in else "CROSSES"

    def walk(node, lo, hi):
        r = rel(lo, hi)
        if r == "OUTSIDE":
            stats["pruned"] += 1
            return
        if node.kind == "leaf":
            stats["leaves"] += 1
            if r == "INSIDE":
                hits.extend(d for d, _v in node.points)
                stats["scanned"] += len(node.points)
                return
            for d, v in node.points:
                stats["scanned"] += 1
                if all(qmin[k] <= v[k] <= qmax[k] for k in range(cfg.num_dims)):
                    hits.append(d)
            return
        stats["inner"] += 1
        if r == "INSIDE":
            # 整块落在查询内：不用再判包围盒，直接收子树（node 本身已计数）
            collect_all(node.left, stats, hits)
            collect_all(node.right, stats, hits)
            return
        # pushBoundsLeft：上界被 splitValue 取代
        left_hi = list(hi)
        left_hi[node.split_dim] = node.split_value
        walk(node.left, lo, left_hi)
        # pushBoundsRight：下界被 splitValue 取代
        right_lo = list(lo)
        right_lo[node.split_dim] = node.split_value
        walk(node.right, right_lo, hi)

    def collect_all(node, st, out):
        if node.kind == "leaf":
            st["leaves"] += 1
            st["scanned"] += len(node.points)
            out.extend(d for d, _v in node.points)
        else:
            st["inner"] += 1
            collect_all(node.left, st, out)
            collect_all(node.right, st, out)

    walk(root, mins, maxs)
    return sorted(hits), stats
