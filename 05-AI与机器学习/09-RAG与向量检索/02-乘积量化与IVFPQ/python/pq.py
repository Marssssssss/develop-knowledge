"""乘积量化（Product Quantization）—— 按 faiss 官方 ProductQuantizer 转写。

依据（facebookresearch/faiss @ main）：
  - `faiss/impl/ProductQuantizer.cpp::set_derived_values`
  - `faiss/impl/ProductQuantizer.cpp::compute_distance_table` /
    `compute_distance_tables` / `compute_inner_prod_table`
  - `faiss/impl/ProductQuantizer.h` 的成员与 layout 注释

关键事实：
  - `dsub = d / M`（要求 d % M == 0），`ksub = 1 << nbits`，
    `code_size = (nbits * M + 7) / 8`；
  - `nbits > 24` 官方直接抛「nbits larger than 24 is not practical.」；
  - `centroids` 布局是 **(M, ksub, dsub)**，另有 `transposed_centroids` 布局 (dsub, M, ksub)
    与 `centroids_sq_lengths` 布局 (M, ksub)；
  - L2 距离表 `dis_table(m, j) = ||x_m - c_(m,j)||²`；内积表 `dis_table(m, j) = <x_m, c_(m,j)>`；
  - 用距离表做 ADC 时**不需要还原向量**：`||x - y||² = Σ_m dis_table(m, code[m])`。
"""

def kmeans(points, k, niter=25, seed=42):
    """确定性 k-means（k-means++ 初始化 + Lloyd）。

    faiss 用 `ClusteringParameters{niter=25, min_points_per_centroid=39,
    max_points_per_centroid=256}`；这里用定种子 LCG 复刻可复现的初始化。
    """
    if not points:
        raise ValueError("empty points")
    d = len(points[0])
    x = seed & 0x7FFFFFFF
    rnd = []
    for _ in range(k * 4 + 16):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        rnd.append((x % 1000000) / 1000000.0)
    pos = 0

    def nxt():
        nonlocal pos
        v = rnd[pos % len(rnd)]
        pos += 1
        return v

    cent = [list(points[int(nxt() * len(points)) % len(points)])]
    while len(cent) < k:
        best, best_d = None, -1.0
        for p in points:
            dd = min(sum((a - b) * (a - b) for a, b in zip(p, c)) for c in cent)
            score = dd * (0.5 + nxt())
            if score > best_d:
                best_d, best = score, p
        cent.append(list(best))

    for _ in range(niter):
        buckets = [[] for _ in range(k)]
        for p in points:
            bi, bd = 0, None
            for ci, c in enumerate(cent):
                dd = sum((a - b) * (a - b) for a, b in zip(p, c))
                if bd is None or dd < bd:
                    bd, bi = dd, ci
            buckets[bi].append(p)
        moved = 0.0
        for ci, b in enumerate(buckets):
            if not b:
                continue          # 官方对空簇不更新质心
            new = [sum(v[j] for v in b) / len(b) for j in range(d)]
            moved += sum((a - b2) * (a - b2) for a, b2 in zip(new, cent[ci]))
            cent[ci] = new
        if moved < 1e-18:
            break
    return cent


class ProductQuantizer:
    """`faiss::ProductQuantizer` 的语义转写。"""

    def __init__(self, d, M, nbits):
        self.d = d
        self.M = M
        self.nbits = nbits
        self.centroids = []           # 布局 (M, ksub, dsub)
        self.centroids_sq_lengths = []  # 布局 (M, ksub)
        self.set_derived_values()

    def set_derived_values(self):
        if self.M <= 0:
            raise ValueError("M must be > 0")
        if self.d % self.M != 0:
            raise ValueError(
                "The dimension of the vector (d) should be a multiple of the "
                "number of subquantizers (M)")
        if self.d > 0 and self.nbits > 24:
            raise ValueError("nbits larger than 24 is not practical.")
        self.dsub = self.d // self.M
        self.code_size = (self.nbits * self.M + 7) // 8
        self.ksub = 1 << self.nbits

    # -- 训练 -----------------------------------------------------
    def train(self, xs, niter=25, seed=42):
        if len(xs) < self.ksub:
            raise ValueError("training set smaller than ksub")
        self.centroids = []
        self.centroids_sq_lengths = []
        for m in range(self.M):
            sub = [x[m * self.dsub:(m + 1) * self.dsub] for x in xs]
            cm = kmeans(sub, self.ksub, niter=niter, seed=seed + m)
            self.centroids.append(cm)
            self.centroids_sq_lengths.append(
                [sum(v * v for v in c) for c in cm])
        return self

    def get_centroids(self, m, i):
        return self.centroids[m][i]

    # -- 编解码 ---------------------------------------------------
    def compute_code(self, x):
        """`compute_code`：逐子空间取最近质心。返回长度 M 的索引列表。"""
        return [self._best_sub(x, m) for m in range(self.M)]

    def _best_sub(self, x, m):
        xm = x[m * self.dsub:(m + 1) * self.dsub]
        bi, bd = 0, None
        for i, c in enumerate(self.centroids[m]):
            dd = sum((a - b) * (a - b) for a, b in zip(xm, c))
            if bd is None or dd < bd:
                bd, bi = dd, i
        return bi

    def decode(self, code):
        """`decode`：把 code 还原成向量（即各子空间质心的拼接）。"""
        out = []
        for m, i in enumerate(code):
            out.extend(self.centroids[m][i])
        return out

    def pack_code(self, code):
        """把索引列表按 nbits 位打包成字节串（`code_size = ceil(nbits*M/8)`）。"""
        buf = bytearray(self.code_size)
        bitpos = 0
        for idx in code:
            for b in range(self.nbits):
                if idx >> b & 1:
                    byte = bitpos >> 3
                    buf[byte] |= 1 << (bitpos & 7)
                bitpos += 1
        return bytes(buf)

    def unpack_code(self, raw):
        out = []
        bitpos = 0
        for _ in range(self.M):
            v = 0
            for b in range(self.nbits):
                if raw[bitpos >> 3] >> (bitpos & 7) & 1:
                    v |= 1 << b
                bitpos += 1
            out.append(v)
        return out

    # -- 距离表 ---------------------------------------------------
    def compute_distance_table(self, x):
        """`dis_table(m, j) = ||x_m - c_(m,j)||²`，返回 M×ksub 的嵌套列表。"""
        table = []
        for m in range(self.M):
            xm = x[m * self.dsub:(m + 1) * self.dsub]
            row = []
            for c in self.centroids[m]:
                row.append(sum((a - b) * (a - b) for a, b in zip(xm, c)))
            table.append(row)
        return table

    def compute_inner_prod_table(self, x):
        """`compute_inner_prod_table`：`<x_m, c_(m,j)>`（不是 L2，符号相反）。"""
        table = []
        for m in range(self.M):
            xm = x[m * self.dsub:(m + 1) * self.dsub]
            row = []
            for c in self.centroids[m]:
                row.append(sum(a * b for a, b in zip(xm, c)))
            table.append(row)
        return table

    def adc_l2(self, table, code):
        """ADC（非对称距离计算）：直接对距离表求和，**不还原向量**。"""
        return sum(table[m][code[m]] for m in range(self.M))

    def adc_ip_asymmetric(self, x, ip_table, code):
        """内积的 ADC：`||x-y||² = ||x||² + ||y||² - 2<x,y>`。

        `||y||²` 走 `centroids_sq_lengths`（faiss 为内积模式准备的那张表）。
        """
        xsq = sum(v * v for v in x)
        ysq = sum(self.centroids_sq_lengths[m][code[m]] for m in range(self.M))
        dot = sum(ip_table[m][code[m]] for m in range(self.M))
        return xsq + ysq - 2.0 * dot

    def reconstruction_error(self, xs):
        """量化误差：平均 ||x - decode(encode(x))||²。"""
        tot = 0.0
        for x in xs:
            y = self.decode(self.compute_code(x))
            tot += sum((a - b) * (a - b) for a, b in zip(x, y))
        return tot / len(xs)


def lcg_points(n, dim, seed=777, scale=1.0):
    x = seed & 0x7FFFFFFF
    pts = []
    for _ in range(n):
        v = []
        for _d in range(dim):
            x = (1103515245 * x + 12345) & 0x7FFFFFFF
            v.append((x % 100000) / 100000.0 * scale)
        pts.append(v)
    return pts


def l2sqr(a, b):
    return sum((p - q) * (p - q) for p, q in zip(a, b))
