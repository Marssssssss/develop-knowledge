"""RedisBloom 的布谷鸟过滤器：指纹、备选桶、踢出重插入与删除压缩。

事实来源（本轮实读，非记忆）：RedisBloom@master
  - `src/cuckoo.h`：CUCKOO_BKTSIZE 2 / CUCKOO_NULLFP 0 / CF_MAX_NUM_BUCKETS（56 位）/
    SubCF（numBuckets:56 + bucketSize:8）/ CuckooFilter / CuckooInsertStatus
  - `src/cuckoo.c` 33-42：getNextN2（先减一再连续或移位再加一）
  - `src/cuckoo.c` 44-59：CuckooFilter_Init（numBuckets = getNextN2(capacity/bucketSize)，0 → 1）
  - `src/cuckoo.c` 78-112：CuckooFilter_Grow（growth = expansion^numFilters，56 位溢出检查）
  - `src/cuckoo.c` 116-152：getAltHash / getLookupParams（fp = hash%255+1）/ SubCF_GetIndex
  - `src/cuckoo.c` 199-229：CuckooFilter_Delete（numDeletes > numItems*0.10 触发 Compact）
  - `src/cuckoo.c` 254-282：CuckooFilter_InsertFP（先找空位，再踢，再扩容）
  - `src/cuckoo.c` 292-343：Filter_KOInsert（踢出链与失败回滚）
  - `src/rebloom.c` 115-121：cfCreate 的 capacity < bucketSize*2 校验
"""

CUCKOO_BKTSIZE = 2
CUCKOO_NULLFP = 0
CF_MAX_NUM_BUCKETS = 0x00FFFFFFFFFFFFFF      # 56 位
ALT_HASH_MULTIPLIER = 0x5BD1E995             # 1539678357
COMPACT_DELETE_RATIO = 0.10

DEFAULT_CF_BUCKET_SIZE = 2
DEFAULT_CF_INITIAL_SIZE = 1024
DEFAULT_CF_MAX_ITERATIONS = 20
DEFAULT_CF_EXPANSION_FACTOR = 1
DEFAULT_CF_MAX_EXPANSIONS = 32

INSERTED = 1
EXISTS = 0
NO_SPACE = -1
MEM_ALLOC_FAILED = -2

CUCKOO_OK = 0
CUCKOO_ERR = -1
CUCKOO_OOM = -2


class CuckooError(Exception):
    """容量或参数非法。"""


def get_next_n2(n):
    """cuckoo.c:35：把 n 向上取到最近的 2 的幂（n=0 → 0，调用方再兜底为 1）。"""
    if n == 0:
        return 0
    n -= 1
    for shift in (1, 2, 4, 8, 16, 32):
        n |= n >> shift
    return n + 1


def get_alt_hash(fp, index):
    """cuckoo.c:116：index ^ (fp * 0x5bd1e995)。"""
    return index ^ (fp * ALT_HASH_MULTIPLIER)


def get_lookup_params(h):
    """cuckoo.c:120：fp 落在 1..255（0 被留作空槽标记），h1 就是原始哈希。"""
    return h, get_alt_hash(h % 255 + 1, h), h % 255 + 1


class SubCF:
    __slots__ = ("num_buckets", "bucket_size", "data")

    def __init__(self, num_buckets, bucket_size):
        self.num_buckets = num_buckets
        self.bucket_size = bucket_size
        self.data = [CUCKOO_NULLFP] * (num_buckets * bucket_size)

    def index_of(self, h):
        """cuckoo.c:130：**取模**而不是按位与（numBuckets 是 2 的幂，两者等价）。"""
        return (h % self.num_buckets) * self.bucket_size


class CuckooFilter:
    def __init__(self, capacity, bucket_size=DEFAULT_CF_BUCKET_SIZE,
                 max_iterations=DEFAULT_CF_MAX_ITERATIONS,
                 expansion=DEFAULT_CF_EXPANSION_FACTOR):
        if capacity < bucket_size * 2:
            raise CuckooError(f"capacity {capacity} < bucketSize*2 = {bucket_size * 2}")
        self.num_buckets = get_next_n2(capacity // bucket_size)
        if self.num_buckets == 0:
            self.num_buckets = 1
        self.num_items = 0
        self.num_deletes = 0
        self.num_filters = 0
        self.bucket_size = bucket_size
        self.max_iterations = max_iterations
        self.expansion = get_next_n2(expansion)
        self.filters = []
        self.compacted = 0
        if self.grow() != CUCKOO_OK:
            raise CuckooError("CUCKOO_OOM")

    # -------------------------------------------------------------- 扩容
    def grow(self):
        """cuckoo.c:78：growth = expansion^numFilters，且总数不能突破 56 位。"""
        if self.num_filters == 0xFFFF:
            return CUCKOO_ERR
        growth = self.expansion ** self.num_filters
        if growth > CF_MAX_NUM_BUCKETS // self.num_buckets:
            return CUCKOO_ERR
        sub = SubCF(self.num_buckets * growth, self.bucket_size)
        self.filters.append(sub)
        self.num_filters += 1
        return CUCKOO_OK

    # -------------------------------------------------------------- 查询
    def check(self, h):
        """cuckoo.c:171：两个候选桶任一命中即存在。"""
        h1, h2, fp = get_lookup_params(h)
        for sub in self.filters:
            for hh in (h1, h2):
                base = sub.index_of(hh)
                for k in range(sub.bucket_size):
                    if sub.data[base + k] == fp:
                        return True
        return False

    def count(self, h):
        """cuckoo.c:189：可能 > 1 —— 同一指纹在候选桶里出现多次。"""
        h1, h2, fp = get_lookup_params(h)
        total = 0
        for sub in self.filters:
            for hh in (h1, h2):
                base = sub.index_of(hh)
                for k in range(sub.bucket_size):
                    if sub.data[base + k] == fp:
                        total += 1
        return total

    # -------------------------------------------------------------- 插入
    def _find_available(self, sub, h1, h2):
        for hh in (h1, h2):
            base = sub.index_of(hh)
            for k in range(sub.bucket_size):
                if sub.data[base + k] == CUCKOO_NULLFP:
                    return base + k
        return None

    def _ko_insert(self, sub, h1, fp):
        """cuckoo.c:292：踢出链；失败后按原路回滚。"""
        victim = 0
        bucket_ix = h1 % sub.num_buckets
        counter = 0
        while counter < self.max_iterations:
            counter += 1
            base = bucket_ix * sub.bucket_size
            sub.data[base + victim], fp = fp, sub.data[base + victim]
            bucket_ix = get_alt_hash(fp, bucket_ix) % sub.num_buckets
            empty = None
            alt_base = bucket_ix * sub.bucket_size
            for k in range(sub.bucket_size):
                if sub.data[alt_base + k] == CUCKOO_NULLFP:
                    empty = alt_base + k
                    break
            if empty is not None:
                sub.data[empty] = fp
                return True
            victim = (victim + 1) % sub.bucket_size
        # 回滚：把沿路换出去的指纹换回来
        counter = 0
        while counter < self.max_iterations:
            counter += 1
            victim = (victim + self.bucket_size - 1) % self.bucket_size
            bucket_ix = get_alt_hash(fp, bucket_ix) % sub.num_buckets
            base = bucket_ix * sub.bucket_size
            sub.data[base + victim], fp = fp, sub.data[base + victim]
        return False

    def _insert_fp(self, h1, h2, fp):
        """cuckoo.c:254：找空位 → 踢 → 扩容 → 重试。"""
        for i in range(self.num_filters - 1, -1, -1):
            slot = self._find_available(self.filters[i], h1, h2)
            if slot is not None:
                self.filters[i].data[slot] = fp
                self.num_items += 1
                return INSERTED
        if self._ko_insert(self.filters[-1], h1, fp):
            self.num_items += 1
            return INSERTED
        if self.expansion == 0:
            return NO_SPACE
        if self.grow() != CUCKOO_OK:
            return MEM_ALLOC_FAILED
        return self._insert_fp(h1, h2, fp)

    def insert(self, h):
        """cuckoo.c:278：**不查重**，同一元素可重复写入。"""
        h1, h2, fp = get_lookup_params(h)
        return self._insert_fp(h1, h2, fp)

    def insert_unique(self, h):
        """cuckoo.c:284：先查存在，存在则返回 EXISTS。"""
        h1, h2, fp = get_lookup_params(h)
        if self.check(h):
            return EXISTS
        return self._insert_fp(h1, h2, fp)

    # -------------------------------------------------------------- 删除
    def delete(self, h):
        """cuckoo.c:199：从最新子过滤器往回删；删除量超过 10% 触发压缩。"""
        h1, h2, fp = get_lookup_params(h)
        for i in range(self.num_filters, 0, -1):
            sub = self.filters[i - 1]
            for hh in (h1, h2):
                base = sub.index_of(hh)
                for k in range(sub.bucket_size):
                    if sub.data[base + k] == fp:
                        sub.data[base + k] = CUCKOO_NULLFP
                        self.num_items -= 1
                        self.num_deletes += 1
                        if (self.num_filters > 1
                                and self.num_deletes > self.num_items * COMPACT_DELETE_RATIO):
                            self.compact()
                        return True
        return False

    def compact(self):
        """cuckoo.c 的 CuckooFilter_Compact 本轮未实读，这里只记录触发次数（见 README 口径）。"""
        self.compacted += 1
