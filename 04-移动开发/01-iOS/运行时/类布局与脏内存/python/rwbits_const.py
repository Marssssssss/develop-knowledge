"""objc4 类布局：位域常量 + ptrauth 模型 + 地址堆。

全部常量取自 apple-oss-distributions/objc4 main 分支
`runtime/objc-runtime-new.h`（115042 B 实读，FAST_* 在 118-180 行、
RW_* 在 76-112 行、`class_data_bits_t` 在 2364 行起）。
指针签名（PAC）的真实比特位由硬件定义，源码不可见；本模块只保留
「PAC 落在 FAST_DATA_MASK 之外」这一条从源码可证的性质，
用确定性的 `mix()` 占位——凡涉及具体 PAC 数值的结论一律不作断言。
"""

MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1

# ---- FAST_*：打包进 class_data_bits_t::bits 指针空余位的标志 ----
FAST_IS_SWIFT_LEGACY = 1 << 0
FAST_IS_SWIFT_STABLE = 1 << 1
FAST_HAS_DEFAULT_RR = 1 << 2

# data pointer 掩码：64 位分「iPhone 真机」与「其它」两套
FAST_DATA_MASK_IPHONE = 0x0F00007FFFFFFFF8
FAST_DATA_MASK_OTHER = 0x0F007FFFFFFFFFF8
FAST_DATA_MASK_32 = 0xFFFFFFFC

FAST_FLAGS_MASK_64 = 0x0000000000000007
FAST_FLAGS_MASK_32 = 0x00000003

FAST_IS_RW_POINTER_64 = 0x8000000000000000
FAST_IS_RW_POINTER_32 = 0

# ---- RW_*：class_rw_t->flags（class_ro_t->flags 复用同一批低位） ----
RW_REALIZED = 1 << 31
RW_FUTURE = 1 << 30
RW_INITIALIZED = 1 << 29
RW_INITIALIZING = 1 << 28
RW_COPIED_RO = 1 << 27
RW_CONSTRUCTING = 1 << 26
RW_CONSTRUCTED = 1 << 25
RW_LOADED = 1 << 23
RW_REALIZING = 1 << 19
RW_META = 1 << 0
RO_META = 1 << 0
RW_REALIZING_OR_FUTURE = RW_REALIZING | RW_FUTURE

# ---- RO_*：class_ro_t->flags ----
RO_HAS_SWIFT_INITIALIZER = 1 << 18


class Arch:
    """一套目标 ABI 上的 FAST_* 位布局。"""

    def __init__(self, name, width, data_mask, flags_mask, rw_bit):
        self.name = name
        self.width = width
        self.full = (1 << width) - 1
        self.data_mask = data_mask
        self.flags_mask = flags_mask
        self.rw_bit = rw_bit
        # PAC 可用位 = 既不在 data 掩码里、也不在 flags 掩码里、也不是 RW 标记位
        self.pac_mask = (~(data_mask | flags_mask | rw_bit)) & self.full

    def __repr__(self):
        return "Arch(%s)" % self.name


LP64_IPHONE = Arch("LP64 iPhone device", 64, FAST_DATA_MASK_IPHONE,
                   FAST_FLAGS_MASK_64, FAST_IS_RW_POINTER_64)
LP64_OTHER = Arch("LP64 non-device", 64, FAST_DATA_MASK_OTHER,
                  FAST_FLAGS_MASK_64, FAST_IS_RW_POINTER_64)
ILP32 = Arch("ILP32", 32, FAST_DATA_MASK_32,
             FAST_FLAGS_MASK_32, FAST_IS_RW_POINTER_32)


def _mix(v, disc):
    """确定性 64 位混淆，仅用于模拟 PAC 占位值。"""
    x = (v * 0x9E3779B97F4A7C15 + disc * 0xC2B2AE3D27D4EB4F + 0x165667B19E3779F9) & MASK64
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & MASK64
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & MASK64
    x ^= x >> 31
    return x


class AuthFailure(Exception):
    """模拟 ptrauth 验签失败（真实硬件上是 trap）。"""


class PtrAuth:
    """sign / auth / strip 三件套。

    真实 arm64e 上 `ptrauth_strip`（xpacd）不校验、也不区分密钥——
    objc4 正是靠这一点在 `class_data_bits_t::flags()` 里「无条件用 RO
    密钥 strip」，源码注释说两个密钥都会生成 xpacd 指令。本模型因此让
    `pac()` 只依赖（值, 判别子）而不依赖密钥，`strip()` 不做任何校验。
    """

    def __init__(self, arch, disc):
        self.arch = arch
        self.disc = disc & arch.full
        self.pac_mask = arch.pac_mask

    def pac(self, v):
        return _mix(v & self.arch.full, self.disc) & self.pac_mask

    def sign(self, v):
        if v & self.pac_mask:
            raise ValueError("raw value 0x%x has bits inside the PAC field" % v)
        return v | self.pac(v)

    def auth(self, v):
        raw = v & ~self.pac_mask & self.arch.full
        if (v & self.pac_mask) != self.pac(raw):
            raise AuthFailure("bad signature for 0x%x" % v)
        return raw

    def strip(self, v):
        return v & ~self.pac_mask & self.arch.full


# ---- 极简地址堆：只为让 flags(bits) 能「按偏移 0 读 uint32」 ----
HEAP = {}
_CURSOR = [0x1000]


def reset_heap():
    HEAP.clear()
    _CURSOR[0] = 0x1000


def alloc(obj, arch, step=0x40):
    a = _CURSOR[0]
    if a % step:
        a += step - a % step
    if a & ~arch.data_mask:
        raise ValueError("0x%x does not fit in FAST_DATA_MASK" % a)
    if a & 1:
        raise ValueError("class_ro_t / class_rw_t must be at least 2-byte aligned")
    HEAP[a] = obj
    _CURSOR[0] = a + step
    return a


def deref(addr):
    return HEAP[addr]


# ---- 对象地址：同一个对象在同一套 ABI 下只分配一次 ----
ADDRS = {}


def addr_of(obj, arch):
    key = (id(obj), arch.name)
    if key not in ADDRS:
        ADDRS[key] = alloc(obj, arch)
    return ADDRS[key]


def reset_all():
    reset_heap()
    ADDRS.clear()
