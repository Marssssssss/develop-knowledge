"""Lucene BKD 树：多维点（数值 / 地理）索引的构建与遍历。

忠实转写自 apache/lucene@main：
  core/src/java/org/apache/lucene/util/bkd/BKDConfig.java
  core/src/java/org/apache/lucene/util/bkd/BKDUtil.java
  core/src/java/org/apache/lucene/util/bkd/BKDWriter.java
  core/src/java/org/apache/lucene/util/bkd/BKDReader.java

口径：
  * 每个维度的值按 **bytesPerDim 字节无符号** 比较（NumericUtils 的编码顺序）；
    本 demo 直接用整数代替字节串，无符号比较即「值域内的普通整数比较」。
  * `subtract(bytesPerDim, dim, max, min)` 在这个口径下就是 `max - min`。
"""

MAX_DIMS = 16
MAX_INDEX_DIMS = 8
DEFAULT_MAX_POINTS_IN_LEAF_NODE = 512
SPLITS_BEFORE_EXACT_BOUNDS = 4


class BKDConfig(object):
    """BKDConfig：numDims / numIndexDims / bytesPerDim / maxPointsInLeafNode。"""

    DEFAULT_CONFIGS = [(1, 1, 2), (1, 1, 4), (1, 1, 8), (1, 1, 16),
                       (2, 2, 2), (2, 2, 4), (2, 2, 8), (2, 2, 16),
                       (7, 4, 4)]

    def __init__(self, num_dims, num_index_dims, bytes_per_dim,
                 max_points_in_leaf_node=DEFAULT_MAX_POINTS_IN_LEAF_NODE):
        if not 1 <= num_dims <= MAX_DIMS:
            raise ValueError("numDims must be 1 .. %d (got: %d)"
                             % (MAX_DIMS, num_dims))
        if not 1 <= num_index_dims <= MAX_INDEX_DIMS:
            raise ValueError("numIndexDims must be 1 .. %d (got: %d)"
                             % (MAX_INDEX_DIMS, num_index_dims))
        if num_index_dims > num_dims:
            raise ValueError("numIndexDims cannot exceed numDims")
        if bytes_per_dim <= 0:
            raise ValueError("bytesPerDim must be > 0")
        if max_points_in_leaf_node <= 0:
            raise ValueError("maxPointsInLeafNode must be > 0")
        self.num_dims = num_dims
        self.num_index_dims = num_index_dims
        self.bytes_per_dim = bytes_per_dim
        self.max_points_in_leaf_node = max_points_in_leaf_node

    def packed_bytes_length(self):
        return self.num_dims * self.bytes_per_dim

    def packed_index_bytes_length(self):
        return self.num_index_dims * self.bytes_per_dim

    def bytes_per_doc(self):
        return self.packed_bytes_length() + 4

    def is_default(self):
        return (self.num_dims, self.num_index_dims,
                self.bytes_per_dim) in self.DEFAULT_CONFIGS


# --------------------------------------------------------------------------
# BKDUtil：公共前缀
# --------------------------------------------------------------------------

def _to_bytes(value, bytes_per_dim):
    return [(value >> (8 * (bytes_per_dim - 1 - i))) & 0xFF
            for i in range(bytes_per_dim)]


def common_prefix_length(a, b, bytes_per_dim):
    """BKDUtil.commonPrefixLengthN：前面的公共字节数。

    numBytes 为 4/8 时源码走 `Integer/Long.numberOfLeadingZeros(reverseBytes(a^b)) >>> 3`
    的快路径，语义与逐字节比较一致。
    """
    ba = _to_bytes(a, bytes_per_dim)
    bb = _to_bytes(b, bytes_per_dim)
    i = 0
    while i < bytes_per_dim and ba[i] == bb[i]:
        i += 1
    return i


def common_prefix_length_fast(a, b, bytes_per_dim):
    """4/8 字节的位运算快路径，用来交叉验证 slow 版本。"""
    xor = a ^ b
    bits = bytes_per_dim * 8
    # reverseBytes 等价于把字节序倒过来；这里直接算「从最高字节起的公共字节数」
    lead = bits
    for i in range(bytes_per_dim):
        shift = 8 * (bytes_per_dim - 1 - i)
        if (xor >> shift) & 0xFF:
            lead = i
            break
    return lead if lead != bits else bytes_per_dim


# --------------------------------------------------------------------------
# BKDWriter：树的形状
# --------------------------------------------------------------------------

def num_leaves_of(point_count, max_points_in_leaf_node):
    """(pointCount + maxPointsInLeafNode - 1) / maxPointsInLeafNode。"""
    return (point_count + max_points_in_leaf_node - 1) // max_points_in_leaf_node


def get_num_left_leaf_nodes(num_leaves):
    """BKDWriter.getNumLeftLeafNodes：尽量把叶节点铺成满二叉树。

    lastFullLevel = 31 - nlz(numLeaves)；满层的叶子数 1<<lastFullLevel，
    其中一半归左子树；不满的部分（unbalanced）尽量也往左边塞。
    """
    assert num_leaves > 1
    last_full_level = 31 - _nlz(num_leaves)
    leaves_full_level = 1 << last_full_level
    num_left = leaves_full_level // 2
    unbalanced = num_leaves - leaves_full_level
    num_left += min(unbalanced, num_left)
    assert num_left >= num_leaves - num_left
    assert num_left <= 2 * (num_leaves - num_left)
    return num_left


def _nlz(x):
    """Integer.numberOfLeadingZeros。"""
    return 32 - x.bit_length() if x > 0 else 32


def choose_split_dim(mins, maxs, parent_splits, num_index_dims):
    """BKDWriter.split：先看「2 倍」规则，再挑跨度最大的维度。"""
    max_num_splits = 0
    for s in parent_splits:
        max_num_splits = max(max_num_splits, s)
    # 「某些维度被切的次数还不到最多的一半、且该维度上取值不全等」-> 优先切它，
    # 这样能保证每个维度都被索引到
    for dim in range(num_index_dims):
        if parent_splits[dim] < max_num_splits // 2 and mins[dim] != maxs[dim]:
            return dim
    split_dim = -1
    best_span = None
    for dim in range(num_index_dims):
        span = maxs[dim] - mins[dim]
        if split_dim == -1 or span > best_span:
            best_span = span
            split_dim = dim
    return split_dim


def needs_exact_bounds(num_leaves, total_leaves, num_index_dims, parent_splits):
    """SPLITS_BEFORE_EXACT_BOUNDS：只在特定条件下重算精确包围盒。

    判据是 `numLeaves != leafBlockFPs.length && numIndexDims > 2
    && sum(parentSplits) % SPLITS_BEFORE_EXACT_BOUNDS == 0` —— 即**非根节点**
    （叶子数与全树叶子数不同）且维度 > 2 时，每 4 次切分才重算一次。
    """
    if num_leaves == total_leaves:
        return False
    if num_index_dims <= 2:
        return False
    return sum(parent_splits) % SPLITS_BEFORE_EXACT_BOUNDS == 0
