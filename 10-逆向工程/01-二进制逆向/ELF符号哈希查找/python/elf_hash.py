"""ELF 符号哈希查找：DT_HASH 与 DT_GNU_HASH。

复刻对象（原文实读）：
  * glibc master `elf/simple-dl-new-hash.h`  -> __simple_dl_new_hash（GNU hash）
  * glibc master `elf/simple-dl-hash.h`      -> __simple_dl_elf_hash（SysV hash）
  * glibc master `elf/dl-setup_hash.c`       -> _dl_setup_hash（表头布局）
  * glibc master `elf/dl-lookup.c`           -> do_lookup_x（bloom 过滤 + chain 扫描）
  * glibc master `sysdeps/generic/ldsodefs.h`-> ELF_MACHINE_HASH_SYMIDX 默认定义

关键事实（全部来自上述源码，不是推测）：
  1. GNU hash: h = 5381; h = h*33 + c（uint32 回绕）
  2. SysV hash: h = (h<<4) + c; hi = h & 0xf0000000; h ^= hi>>24; h &= 0x0fffffff
  3. DT_GNU_HASH 表头 4 个 uint32：nbuckets / symbias / bitmask_nwords / shift；
     bitmask_nwords 必须是 2 的幂（源码里直接 assert）
  4. bloom 字下标 = (hash / __ELF_NATIVE_CLASS) & (nwords-1)，注意是**除法**不是移位
  5. 判据 (w >> (h&63)) & (w >> ((h>>shift)&63)) & 1
  6. chain 项：((hv ^ h) >> 1) == 0 才是候选（最低位被让给了 chain 终止位）
  7. symidx = hasharr - chain_zero（默认 ELF_MACHINE_HASH_SYMIDX）
"""

MASK32 = 0xFFFFFFFF
NATIVE_CLASS = 64          # __ELF_NATIVE_CLASS，ELF64 下为 64
CLASS_BITS = 6             # log2(64)，用于 hash & (__ELF_NATIVE_CLASS-1)
POW2_MINUS_1 = NATIVE_CLASS - 1

UNDEF = 0                  # STN_UNDEF


# ---------------------------------------------------------------- 哈希函数

def dl_elf_hash(name):
    """SysV DT_HASH 用：glibc __simple_dl_elf_hash。"""
    if isinstance(name, str):
        name = name.encode()
    h = 0
    for c in name:
        h = (h << 4) + c
        hi = h & 0xF0000000
        h ^= hi >> 24
        h &= 0x0FFFFFFF
    return h & MASK32


def dl_new_hash(name):
    """GNU DT_GNU_HASH 用：glibc __simple_dl_new_hash（djb2 变体）。"""
    if isinstance(name, str):
        name = name.encode()
    h = 5381
    for c in name:
        h = (h * 33 + c) & MASK32
    return h


# ---------------------------------------------------------------- 建表

class GnuHashTable:
    """按真实 linker 的口径构造一张 .gnu.hash。

    symbols: 完整符号表（下标 = 符号索引）
    symbias: 前 symbias 个符号不进哈希表（undefined / local），与 DT_GNU_HASH 的
             第二个字同义
    """

    def __init__(self, symbols, symbias=1, nbuckets=4, nwords=2, shift=6):
        if nwords & (nwords - 1) != 0:
            raise ValueError("bitmask_nwords must be a power of two")
        if nbuckets <= 0:
            raise ValueError("nbuckets must be positive")
        self.symbols = list(symbols)
        self.symbias = symbias
        self.nbuckets = nbuckets
        self.nwords = nwords
        self.idxbits = nwords - 1
        self.shift = shift

        exported = list(range(symbias, len(self.symbols)))
        # linker 会按 bucket 排序让同一 bucket 的符号在符号表里连成一段
        exported.sort(key=lambda i: dl_new_hash(self.symbols[i]) % nbuckets)
        order = {i: k for k, i in enumerate(exported)}
        # 重排符号表本体，使 chain 连续（真实 .so 就是这个形态）
        head = self.symbols[:symbias]
        self.symbols = head + [self.symbols[i] for i in exported]
        self.exported = list(range(symbias, len(self.symbols)))

        self.buckets = [0] * nbuckets
        self.chain_zero = [0] * len(self.symbols)
        self.bitmask = [0] * nwords

        for pos, i in enumerate(self.exported):
            h = dl_new_hash(self.symbols[i])
            b = h % nbuckets
            if self.buckets[b] == 0:
                self.buckets[b] = i
            nxt = self.exported[pos + 1] if pos + 1 < len(self.exported) else None
            last = nxt is None or dl_new_hash(self.symbols[nxt]) % nbuckets != b
            self.chain_zero[i] = (h & ~1) | (1 if last else 0)
            w = (h // NATIVE_CLASS) & self.idxbits
            self.bitmask[w] |= (1 << (h & POW2_MINUS_1))
            self.bitmask[w] |= (1 << ((h >> self.shift) & POW2_MINUS_1))

    # ------------------------------------------------------------ 原始字节

    def words32(self):
        """序列化成 DT_GNU_HASH 指向的 uint32 数组（_dl_setup_hash 的逆）。"""
        w = [self.nbuckets, self.symbias, self.nwords, self.shift]
        for v in self.bitmask:
            w.append(v & MASK32)
            w.append((v >> 32) & MASK32)
        w.extend(self.buckets)
        w.extend(self.chain_zero[self.symbias:])
        return w

    # ------------------------------------------------------------ 查找

    def bloom_probe(self, h):
        """只做 bloom 这一步，返回 (命中?, 用到的字下标, bit1, bit2)。"""
        widx = (h // NATIVE_CLASS) & self.idxbits
        word = self.bitmask[widx]
        b1 = h & POW2_MINUS_1
        b2 = (h >> self.shift) & POW2_MINUS_1
        hit = ((word >> b1) & (word >> b2) & 1) == 1
        return hit, widx, b1, b2

    def lookup(self, name, trace=False):
        """复刻 do_lookup_x 的 GNU hash 分支。返回符号下标或 None。"""
        h = dl_new_hash(name)
        steps = []
        hit, widx, b1, b2 = self.bloom_probe(h)
        steps.append(("bloom", widx, b1, b2, hit))
        if not hit:
            return (None, steps) if trace else None
        bucket = self.buckets[h % self.nbuckets]
        steps.append(("bucket", h % self.nbuckets, bucket))
        if bucket == 0:
            return (None, steps) if trace else None
        i = bucket
        while True:
            hv = self.chain_zero[i]
            if ((hv ^ h) >> 1) == 0:
                steps.append(("candidate", i, self.symbols[i]))
                if self.symbols[i] == name:
                    return (i, steps) if trace else i
            if hv & 1:
                break
            i += 1
        return (None, steps) if trace else None

    def symidx_from_hasharr(self, bucket, offset):
        """ELF_MACHINE_HASH_SYMIDX 的默认实现：hasharr - l_gnu_chain_zero。

        chain_zero 以符号索引为下标，所以「链上第 offset 项」的符号索引就是
        bucket + offset —— 这也是为什么 l_gnu_chain_zero 要减掉 symbias。
        """
        return bucket + offset


class SysvHashTable:
    """DT_HASH：nbuckets / nchain / buckets[] / chain[]。"""

    def __init__(self, symbols, symbias=1, nbuckets=4):
        self.symbols = list(symbols)
        self.symbias = symbias
        self.nbuckets = nbuckets
        nchain = len(self.symbols)
        self.buckets = [UNDEF] * nbuckets
        self.chain = [UNDEF] * nchain
        # chain 是「同 bucket 的下一个符号下标」，倒序插入形成链表
        for i in range(nchain - 1, symbias - 1, -1):
            b = dl_elf_hash(self.symbols[i]) % nbuckets
            self.chain[i] = self.buckets[b]
            self.buckets[b] = i

    def words32(self):
        w = [self.nbuckets, len(self.symbols)]
        w.extend(self.buckets)
        w.extend(self.chain)
        return w

    def lookup(self, name, trace=False):
        h = dl_elf_hash(name)
        steps = [("hash", h)]
        y = self.buckets[h % self.nbuckets]
        steps.append(("bucket", h % self.nbuckets, y))
        while y != UNDEF:
            steps.append(("candidate", y, self.symbols[y]))
            if self.symbols[y] == name:
                return (y, steps) if trace else y
            y = self.chain[y]
        return (None, steps) if trace else None


# ---------------------------------------------------------------- 解析

def parse_gnu_hash(words):
    """复刻 _dl_setup_hash 的 DT_GNU_HASH 分支。"""
    nbuckets = words[0]
    symbias = words[1]
    nwords = words[2]
    if nwords & (nwords - 1) != 0:
        raise ValueError("bitmask_nwords must be a power of two")
    idxbits = nwords - 1
    shift = words[3]
    off = 4
    bitmask = []
    for _ in range(nwords):
        lo = words[off]
        hi = words[off + 1]
        bitmask.append((hi << 32) | lo)
        off += 2
    buckets = words[off:off + nbuckets]
    off += nbuckets
    # l_gnu_chain_zero = hash32 - symbias：下标直接就是符号索引
    chain_zero = [0] * symbias + list(words[off:off + len(words) - off])
    return {
        "nbuckets": nbuckets,
        "symbias": symbias,
        "nwords": nwords,
        "idxbits": idxbits,
        "shift": shift,
        "bitmask": bitmask,
        "buckets": buckets,
        "chain_zero": chain_zero,
    }


def parse_sysv_hash(words):
    nbuckets = words[0]
    nchain = words[1]
    return {
        "nbuckets": nbuckets,
        "nchain": nchain,
        "buckets": words[2:2 + nbuckets],
        "chain": words[2 + nbuckets:2 + nbuckets + nchain],
    }
