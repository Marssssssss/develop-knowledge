"""fs-verity Merkle 树 + dm-integrity 布局的可计算模型。

来源（全部实际读过，见 README）：
* ``Documentation/filesystems/fsverity.html``   —— 分块、salt 填充、descriptor
* ``include/uapi/linux/fsverity.h``             —— 常量与 ioctl 结构
* ``Documentation/admin-guide/device-mapper/dm-integrity.html`` —— 盘上布局
"""

import hashlib

# ---- include/uapi/linux/fsverity.h ----------------------------------------
FS_VERITY_HASH_ALG_SHA256 = 1
FS_VERITY_HASH_ALG_SHA512 = 2

# 最大 salt 长度：文档写 "Currently the maximum salt size is 32 bytes"，
# 与 descriptor 里的 __u8 salt[32] 一致（常量名在内核内部头里，未取到原文）
FS_VERITY_MAX_SALT_SIZE = 32

# fsverity.h 的 FS_VERITY_METADATA_TYPE_*
FS_VERITY_METADATA_TYPE_MERKLE_TREE = 1
FS_VERITY_METADATA_TYPE_DESCRIPTOR = 2
FS_VERITY_METADATA_TYPE_SIGNATURE = 3

# ---- Documentation/filesystems/fsverity.html -------------------------------
FSVERITY_DESCRIPTOR_SIZE = 256
FSVERITY_ROOT_HASH_SIZE = 64        # struct fsverity_descriptor.root_hash[64]
FSVERITY_SALT_FIELD = 32            # struct fsverity_descriptor.salt[32]

# 各算法"压缩函数输入块"大小，salt 要零填充到它的整数倍
HASH_ALG_BLOCKSIZE = {
    FS_VERITY_HASH_ALG_SHA256: 64,
    FS_VERITY_HASH_ALG_SHA512: 128,
}
HASH_ALG_DIGESTSIZE = {
    FS_VERITY_HASH_ALG_SHA256: 32,
    FS_VERITY_HASH_ALG_SHA512: 64,
}
HASH_ALG_NAME = {
    FS_VERITY_HASH_ALG_SHA256: "sha256",
    FS_VERITY_HASH_ALG_SHA512: "sha512",
}

# ---- fsverity descriptor 字段偏移（小端）----------------------------------
# version(1) hash_algorithm(1) log_blocksize(1) salt_size(1)
# __reserved_0x04(4) data_size(8) root_hash(64) salt(32) __reserved[144]
DESC_OFF_VERSION = 0
DESC_OFF_HASH_ALG = 1
DESC_OFF_LOG_BLOCKSIZE = 2
DESC_OFF_SALT_SIZE = 3
DESC_OFF_RESERVED_0x04 = 4
DESC_OFF_DATA_SIZE = 8
DESC_OFF_ROOT_HASH = 16
DESC_OFF_SALT = 16 + 64
DESC_OFF_RESERVED = 16 + 64 + 32


def pad_salt(salt: bytes, alg: int) -> bytes:
    """盐零填充到压缩函数输入块大小的整数倍。

    文档原话："the salt is zero-padded to the closest multiple of the input
    size of the hash algorithm's compression function, e.g. 64 bytes for
    SHA-256 or 128 bytes for SHA-512"。
    这样做的目的是让每个 hash 的输入长度一致（便于硬件加速 / 预计算状态）。
    """
    bs = HASH_ALG_BLOCKSIZE[alg]
    if not salt:
        return b""
    n = (len(salt) + bs - 1) // bs * bs
    return salt + b"\x00" * (n - len(salt))


def merkle_level_sizes(data_size: int, block_size: int, digest_size: int):
    """返回每一层"有多少个 block"，第 0 层是数据块。

    文档：把文件内容按 block_size 切块，末尾零填充；每块算 hash 得到第一层；
    第一层的 hash 再按 block_size 分组、零填充、再 hash，直到只剩一块。
    """
    if data_size == 0:
        return []
    nblocks = (data_size + block_size - 1) // block_size
    levels = [nblocks]
    hashes = nblocks * digest_size
    while True:
        if nblocks == 1:
            break
        nblocks = (hashes + block_size - 1) // block_size
        levels.append(nblocks)
        hashes = nblocks * digest_size
    return levels


def arity(block_size: int, digest_size: int) -> int:
    """每个 block 能装多少个下层 hash。"""
    return block_size // digest_size


def merkle_tree_overhead(data_size: int, block_size: int, digest_size: int) -> int:
    """Merkle 树本身的字节数（不含 descriptor）。

    大文件收敛到约 1/(arity-1) 的原文大小：SHA-256 + 4K 块即 1/127。
    """
    levels = merkle_level_sizes(data_size, block_size, digest_size)
    if not levels:
        return 0
    return sum(n * block_size for n in levels[1:])


def _hash(data: bytes, alg: int) -> bytes:
    return hashlib.new(HASH_ALG_NAME[alg], data).digest()


def build_merkle_tree(data: bytes, block_size: int, alg: int, salt: bytes = b""):
    """返回 (root_hash, levels)，levels[i] 是第 i 层的 block 列表。

    第 0 层是原文分块（末尾零填充）；第 i+1 层是第 i 层各块 hash 拼接后
    再分块再 hash。空文件的 root 是全 0。
    """
    ds = HASH_ALG_DIGESTSIZE[alg]
    psalt = pad_salt(salt, alg)
    if not data:
        return b"\x00" * ds, []

    def blk(i):
        return data[i * block_size:(i + 1) * block_size]

    n0 = (len(data) + block_size - 1) // block_size
    level0 = [blk(i).ljust(block_size, b"\x00") for i in range(n0)]
    levels = [level0]
    cur = level0
    while True:
        digests = b"".join(_hash(psalt + b, alg) for b in cur)
        if len(cur) == 1:
            # 只剩一块：这一层的单个 hash 就是 root
            # （若文件本身只占一块，root 就是那块数据的 hash）
            return digests[:ds], levels
        packed = [digests[i * block_size:(i + 1) * block_size].ljust(block_size, b"\x00")
                  for i in range((len(digests) + block_size - 1) // block_size)]
        levels.append(packed)
        cur = packed


def build_descriptor(data_size: int, log_blocksize: int, alg: int,
                     root_hash: bytes, salt: bytes = b"") -> bytes:
    """struct fsverity_descriptor 的 256 字节小端序列化。"""
    d = bytearray(FSVERITY_DESCRIPTOR_SIZE)
    d[DESC_OFF_VERSION] = 1
    d[DESC_OFF_HASH_ALG] = alg
    d[DESC_OFF_LOG_BLOCKSIZE] = log_blocksize
    d[DESC_OFF_SALT_SIZE] = len(salt)
    d[DESC_OFF_DATA_SIZE:DESC_OFF_DATA_SIZE + 8] = data_size.to_bytes(8, "little")
    d[DESC_OFF_ROOT_HASH:DESC_OFF_ROOT_HASH + len(root_hash)] = root_hash
    d[DESC_OFF_SALT:DESC_OFF_SALT + len(salt)] = salt
    return bytes(d)


def file_digest(descriptor: bytes, alg: int) -> bytes:
    """fs-verity 的"文件摘要"= hash(descriptor)。

    关键点：文件摘要**不是** Merkle root 本身。root 有歧义——无法区分
    "一个大文件"和"一个内容恰好等于前者顶层 hash 块的小文件"。
    """
    return _hash(descriptor, alg)


def verify_block(data: bytes, index: int, root: bytes, block_size: int,
                 alg: int, salt: bytes = b"", levels=None) -> bool:
    """给定块的兄弟路径，验证其 hash 链能回到 root。

    这里直接用完整 levels 做（真实内核只保存需要的那部分），
    重点是把"路径长度 == 层数-1"这条性质钉住。
    """
    ds = HASH_ALG_DIGESTSIZE[alg]
    psalt = pad_salt(salt, alg)
    if levels is None:
        _, levels = build_merkle_tree(data, block_size, alg, salt)
    a = arity(block_size, ds)
    h = _hash(psalt + levels[0][index], alg)
    idx = index
    for lv in range(1, len(levels)):
        sib_off = (idx // a) * a
        packed = b"".join(_hash(psalt + levels[lv - 1][j], alg)
                          for j in range(sib_off, min(sib_off + a, len(levels[lv - 1]))))
        h = _hash(psalt + packed.ljust(block_size, b"\x00"), alg)
        idx //= a
    return h == root


# --------------------------------------------------------------------------
# dm-integrity 盘上布局
# --------------------------------------------------------------------------
DM_INTEGRITY_DEFAULT_INTERLEAVE_SECTORS = 32768
DM_INTEGRITY_DEFAULT_BUFFER_SECTORS = 128
DM_INTEGRITY_DEFAULT_JOURNAL_WATERMARK = 50
DM_INTEGRITY_DEFAULT_COMMIT_TIME_MS = 10000
DM_INTEGRITY_BLOCK_SIZES = (512, 1024, 2048, 4096)
DM_INTEGRITY_DEFAULT_BLOCK_SIZE = 512
DM_INTEGRITY_SUPERBLOCK_SECTORS = 8          # 4 KiB / 512
DM_INTEGRITY_JOURNAL_ENTRY_DATA = 504        # 每扇区存 504 字节，末 8 字节在 journal
SECTOR_SIZE = 512


def rounddown_pow2(x: int) -> int:
    """interleave_sectors / buffer_sectors 都是"向下取到 2 的幂"。"""
    if x <= 0:
        return 0
    return 1 << (x.bit_length() - 1)


def integrity_geometry(interleave_sectors: int, block_size: int, tag_size: int,
                       buffer_sectors: int = DM_INTEGRITY_DEFAULT_BUFFER_SECTORS):
    """算一个 interleave run 里"标签区 + 数据区"的扇区布局。

    文档示例：interleave_sectors=32768、block_size=512、crc32c tag=4 字节
    → 一整个数据区需要 32768*4 = 128 KiB 标签 = 256 个扇区；
    buffer_sectors=128 → 每个元数据区 2 个 buffer，即每 16 MiB 数据 2 个 buffer。
    """
    il = rounddown_pow2(interleave_sectors)
    # 标签区至少 4 KiB，向上取整到扇区
    tag_bytes = il * tag_size
    tag_sectors = (tag_bytes + SECTOR_SIZE - 1) // SECTOR_SIZE
    # 标签区本身要占掉 interleave 区域里的扇区，且它自己也要 4KiB 对齐
    tag_sectors = max(tag_sectors, SECTOR_SIZE // SECTOR_SIZE * 8)   # >= 4 KiB
    data_sectors = il - tag_sectors
    # 数据扇区数必须是 2 的幂（log2 存在 superblock 里）
    data_sectors = rounddown_pow2(data_sectors)
    bufs = (tag_sectors + buffer_sectors - 1) // buffer_sectors
    return {
        "interleave_sectors": il,
        "tag_bytes": tag_bytes,
        "tag_sectors": tag_sectors,
        "data_sectors": data_sectors,
        "buffers_per_run": bufs,
        "data_bytes": data_sectors * SECTOR_SIZE,
        "overhead_ratio": tag_sectors / (tag_sectors + data_sectors),
    }
