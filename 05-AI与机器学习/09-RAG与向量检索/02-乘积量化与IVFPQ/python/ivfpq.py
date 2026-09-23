"""IVF-PQ 倒排 + 乘积量化索引 —— 按 faiss 官方 IndexIVF / IndexIVFPQ 转写。

依据（facebookresearch/faiss @ main）：
  - `faiss/IndexIVFPQ.cpp::IndexIVFPQ(...)`：`code_size = pq.code_size`、
    `by_residual = true`、`use_precomputed_table = 0`、`scan_table_threshold = 0`
  - `faiss/IndexIVFPQ.cpp::initialize_IVFPQ_precomputed_table`
  - `faiss/IndexIVFPQ.cpp::train_encoder_num_vectors`
  - `faiss/IndexIVF.cpp::search` 的 `cur_nprobe = std::min(nlist, params ? ... : nprobe)`
    与 `FAISS_THROW_IF_NOT(cur_nprobe > 0)`
  - `faiss/IndexIVF.h`：`size_t nprobe = 1;`
  - `faiss/Clustering.h`：`niter = 25`、`min_points_per_centroid = 39`、
    `max_points_per_centroid = 256`
"""

from pq import ProductQuantizer, kmeans, l2sqr  # noqa: E402

# `size_t precomputed_table_max_bytes = ((size_t)1) << 31;` —— 2 GiB
PRECOMPUTED_TABLE_MAX_BYTES = 1 << 31
# `ClusteringParameters` 的默认训练样本量口径
MAX_POINTS_PER_CENTROID = 256
MIN_POINTS_PER_CENTROID = 39
NITER = 25


class IndexIVFPQ:
    """`faiss::IndexIVFPQ` 的语义转写（单线程、无 SIMD）。"""

    def __init__(self, d, nlist, M, nbits, metric="L2", by_residual=None):
        self.d = d
        self.nlist = nlist
        self.metric = metric
        self.pq = ProductQuantizer(d, M, nbits)
        self.code_size = self.pq.code_size
        self.by_residual = True if by_residual is None else by_residual
        self.use_precomputed_table = 0
        self.scan_table_threshold = 0
        self.nprobe = 1
        self.coarse = []          # nlist 个粗质心
        self.lists = []           # nlist 个 [(id, code, residual_norm_sq), ...]
        self.vectors = []

    # -- 训练 -----------------------------------------------------
    def train_encoder_num_vectors(self):
        """`pq.cp.max_points_per_centroid * pq.ksub` = 256 * ksub。"""
        return MAX_POINTS_PER_CENTROID * self.pq.ksub

    def train(self, xs, seed=42):
        if len(xs) < self.nlist:
            raise ValueError("training set smaller than nlist")
        self.coarse = kmeans(xs, self.nlist, niter=NITER, seed=seed)
        if self.by_residual:
            resid = self._residuals(xs)
            self.pq.train(resid, niter=NITER, seed=seed + 101)
        else:
            self.pq.train(xs, niter=NITER, seed=seed + 101)
        self.precompute_table()
        return self

    def _residuals(self, xs):
        out = []
        for x in xs:
            ci = self.assign_coarse(x)
            out.append([a - b for a, b in zip(x, self.coarse[ci])])
        return out

    def assign_coarse(self, x):
        bi, bd = 0, None
        for i, c in enumerate(self.coarse):
            dd = l2sqr(x, c)
            if bd is None or dd < bd:
                bd, bi = dd, i
        return bi

    # -- 预计算表 -------------------------------------------------
    def precompute_table(self, verbose=False):
        """`initialize_IVFPQ_precomputed_table` 的三条决策路径。"""
        if self.use_precomputed_table == -1:
            self.precomputed_table_size = 0
            return 0
        m_ksub = self.pq.M * self.pq.ksub
        if self.use_precomputed_table == 0:
            # 只有 L2 且开残差才值得预计算
            if not (self.metric == "L2" and self.by_residual):
                self.precomputed_table_size = 0
                return 0
            table_size = m_ksub * self.nlist * 4
            if table_size > PRECOMPUTED_TABLE_MAX_BYTES:
                self.precomputed_table_size = 0
                return 0
            self.use_precomputed_table = 1
            self.precomputed_table_size = table_size
        else:
            self.precomputed_table_size = m_ksub * self.nlist * 4
        return self.use_precomputed_table

    # -- 入库 -----------------------------------------------------
    def add(self, xs):
        if not self.coarse:
            raise RuntimeError("index not trained")
        self.lists = [[] for _ in range(self.nlist)]
        base = len(self.vectors)
        for i, x in enumerate(xs):
            self.vectors.append(list(x))
            ci = self.assign_coarse(x)
            src = ([a - b for a, b in zip(x, self.coarse[ci])]
                   if self.by_residual else list(x))
            self.lists[ci].append((base + i, self.pq.compute_code(src)))

    # -- 检索 -----------------------------------------------------
    def effective_nprobe(self, nprobe=None):
        """`cur_nprobe = std::min(nlist, params ? params->nprobe : this->nprobe)`。"""
        p = self.nprobe if nprobe is None else nprobe
        if p <= 0:
            raise ValueError("nprobe must be > 0")
        return min(self.nlist, p)

    def search(self, q, k, nprobe=None):
        cur = self.effective_nprobe(nprobe)
        order = sorted(range(self.nlist), key=lambda i: l2sqr(q, self.coarse[i]))[:cur]
        if self.metric == "L2":
            table = self.pq.compute_distance_table(q)
        else:
            ip_table = self.pq.compute_inner_prod_table(q)
        cands = []
        for li in order:
            cid = self.coarse[li]
            qres = ([a - b for a, b in zip(q, cid)] if self.by_residual else list(q))
            if self.metric == "L2":
                tab = self.pq.compute_distance_table(qres) if self.by_residual else table
            else:
                tab = self.pq.compute_inner_prod_table(qres) if self.by_residual else ip_table
            for idx, code in self.lists[li]:
                if self.metric == "L2":
                    dist = self.pq.adc_l2(tab, code)
                else:
                    dist = self.pq.adc_ip_asymmetric(qres, tab, code)
                cands.append((dist, idx))
        cands.sort(key=lambda t: (t[0], t[1]))
        return cands[:k]

    def recall_at_k(self, qs, k, nprobe=None):
        hit = tot = 0
        for q in qs:
            got = set(i for _, i in self.search(q, k, nprobe))
            brute = sorted(((l2sqr(q, v), i) for i, v in enumerate(self.vectors)))[:k]
            want = set(i for _, i in brute)
            hit += len(got & want)
            tot += k
        return hit / float(tot)

    def compression_ratio(self):
        """float32 原始向量 → PQ code 的压缩比（不含倒排开销）。"""
        return (self.d * 4) / float(self.code_size)
