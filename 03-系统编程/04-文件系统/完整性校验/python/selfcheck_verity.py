"""fs-verity + dm-integrity 自检：把文档条文变成断言。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verity import (  # noqa: E402
    DESC_OFF_DATA_SIZE, DESC_OFF_HASH_ALG, DESC_OFF_LOG_BLOCKSIZE,
    DESC_OFF_ROOT_HASH, DESC_OFF_SALT, DESC_OFF_SALT_SIZE, DESC_OFF_VERSION,
    DM_INTEGRITY_DEFAULT_BLOCK_SIZE, DM_INTEGRITY_DEFAULT_COMMIT_TIME_MS,
    DM_INTEGRITY_DEFAULT_INTERLEAVE_SECTORS, DM_INTEGRITY_DEFAULT_JOURNAL_WATERMARK,
    FS_VERITY_HASH_ALG_SHA256, FS_VERITY_HASH_ALG_SHA512,
    FS_VERITY_MAX_SALT_SIZE, FSVERITY_DESCRIPTOR_SIZE, FSVERITY_ROOT_HASH_SIZE,
    HASH_ALG_BLOCKSIZE, HASH_ALG_DIGESTSIZE, SECTOR_SIZE, arity,
    build_descriptor, build_merkle_tree, file_digest, integrity_geometry,
    merkle_level_sizes, merkle_tree_overhead, pad_salt, rounddown_pow2,
    verify_block,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-56s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-56s %s" % (label, detail))


BS = 4096
A = FS_VERITY_HASH_ALG_SHA256
DS = 32

print("== 1. 常量（fsverity.h / fsverity.html） ==")
check("FS_VERITY_HASH_ALG_SHA256 == 1", FS_VERITY_HASH_ALG_SHA256 == 1)
check("FS_VERITY_HASH_ALG_SHA512 == 2", FS_VERITY_HASH_ALG_SHA512 == 2)
check("salt 上限 32 字节（与 descriptor 的 salt[32] 一致）",
      FS_VERITY_MAX_SALT_SIZE == 32)
check("root_hash 字段 64 字节（要装得下 SHA-512）", FSVERITY_ROOT_HASH_SIZE == 64)
check("descriptor 定长 256 字节", FSVERITY_DESCRIPTOR_SIZE == 256)
check("SHA-256 压缩函数输入块 64 字节", HASH_ALG_BLOCKSIZE[A] == 64)
check("SHA-512 压缩函数输入块 128 字节",
      HASH_ALG_BLOCKSIZE[FS_VERITY_HASH_ALG_SHA512] == 128)
check("SHA-256 摘要 32 字节 / SHA-512 摘要 64 字节",
      HASH_ALG_DIGESTSIZE[A] == 32 and
      HASH_ALG_DIGESTSIZE[FS_VERITY_HASH_ALG_SHA512] == 64)

print("== 2. salt 零填充（文档：填充到压缩函数输入块的整数倍） ==")
check("空 salt 不填充（返回空）", pad_salt(b"", A) == b"")
check("13 字节 salt(SHA-256) → 填充到 64", len(pad_salt(b"a" * 13, A)) == 64)
check("64 字节 salt 恰好不额外填充", len(pad_salt(b"a" * 64, A)) == 64)
check("65 字节 salt → 填充到 128（跨了一块）", len(pad_salt(b"a" * 65, A)) == 128)
check("13 字节 salt(SHA-512) → 填充到 128",
      len(pad_salt(b"a" * 13, FS_VERITY_HASH_ALG_SHA512)) == 128)
check("填充不改动原 salt 内容", pad_salt(b"abc", A)[:3] == b"abc")
check("填充是零字节", pad_salt(b"abc", A)[3:] == b"\x00" * 61)

print("== 3. 树形与扇出 ==")
check("SHA-256 + 4K 块 → arity 128", arity(BS, DS) == 128,
      "4096/32 = %d" % arity(BS, DS))
check("SHA-512 + 4K 块 → arity 64",
      arity(BS, HASH_ALG_DIGESTSIZE[FS_VERITY_HASH_ALG_SHA512]) == 64)
check("空文件没有层级", merkle_level_sizes(0, BS, DS) == [])
check("1 块文件只有第 0 层", merkle_level_sizes(1, BS, DS) == [1])
check("4096 字节恰好 1 块", merkle_level_sizes(BS, BS, DS) == [1])
check("4097 字节要 2 块", merkle_level_sizes(BS + 1, BS, DS)[0] == 2)
check("128 块 → 第 1 层 1 块（128*32=4096 恰好一满块）",
      merkle_level_sizes(128 * BS, BS, DS) == [128, 1])
check("129 块 → [129, 2, 1]（4128 字节摘要溢出到两块，两块再合成 1 块）",
      merkle_level_sizes(129 * BS, BS, DS) == [129, 2, 1],
      str(merkle_level_sizes(129 * BS, BS, DS)))
check("16384 块 → [16384, 128, 1]",
      merkle_level_sizes(16384 * BS, BS, DS) == [16384, 128, 1],
      str(merkle_level_sizes(16384 * BS, BS, DS)))

print("== 4. 1/127 收敛（文档：Merkle 树约等于原文的 1/127） ==")
big = 128 * 128 * BS                     # 16384 块 → 三层
lv = merkle_level_sizes(big, BS, DS)
check("16384 块的三层结构", lv == [16384, 128, 1], str(lv))
oh = merkle_tree_overhead(big, BS, DS)
check("overhead = (128+1)*4096 = 528384", oh == 129 * BS, "%d" % oh)
ratio = oh / big
check("overhead 占比 ≈ 1/127（实测 %.5f）" % ratio, abs(ratio - 1 / 127) < 1e-6,
      "1/127 = %.5f" % (1 / 127))
# 小文件填充占比明显更大
small = 2 * BS
oh2 = merkle_tree_overhead(small, BS, DS)
check("2 块小文件：树占 1 整块 → 开销 50%（填充占比显著）",
      oh2 == BS and abs(oh2 / small - 0.5) < 1e-12, "%d/%d" % (oh2, small))
oneblk = merkle_tree_overhead(BS, BS, DS)
check("1 块文件完全没有额外树（root 就是那块的 hash）", oneblk == 0, "%d" % oneblk)

print("== 5. Merkle root 与边界 ==")
root0, levels0 = build_merkle_tree(b"", BS, A)
check("空文件 root 是全 0", root0 == b"\x00" * DS)
check("空文件没有层", levels0 == [])
import hashlib  # noqa: E402
one = b"hello fs-verity"
r1, lv1 = build_merkle_tree(one, BS, A)
check("单块非空文件：root == hash(该块零填充)",
      r1 == hashlib.sha256(one.ljust(BS, b"\x00")).digest())
check("单块文件只有第 0 层", len(lv1) == 1)
# 两块：root = hash(h(a)||h(b) 零填充到 4096)
data = b"x" * (BS + 10)
r2, lv2 = build_merkle_tree(data, BS, A)
h0 = hashlib.sha256(data[:BS]).digest()
h1 = hashlib.sha256(data[BS:].ljust(BS, b"\x00")).digest()
check("两块文件 root = hash(h0||h1 填充到块)",
      r2 == hashlib.sha256((h0 + h1).ljust(BS, b"\x00")).digest())
check("带 salt 时 root 改变",
      build_merkle_tree(data, BS, A, salt=b"\x01" * 8)[0] != r2)
check("salt 长度不同 → root 不同",
      build_merkle_tree(data, BS, A, salt=b"\x01" * 8)[0] !=
      build_merkle_tree(data, BS, A, salt=b"\x01" * 9)[0])
# 大小不同但顶层相同 → root 不同（descriptor 存在的理由）
check("verify_block 能回到 root", verify_block(data, 0, r2, BS, A, levels=lv2))
check("第 2 块也能回到 root", verify_block(data, 1, r2, BS, A, levels=lv2))
# 篡改第 0 块的一个字节 → 重算的 root 与原始 root 不符
bad = bytearray(data)
bad[7] ^= 0xFF
r_bad, lv_bad = build_merkle_tree(bytes(bad), BS, A)
check("篡改 1 字节后 root 改变", r_bad != r2)
check("用篡改后的层去验原始 root 会失败",
      not verify_block(bytes(bad), 0, r2, BS, A, levels=lv_bad))
# 支持集探针：只改第 1 块，第 0 块的验证路径应仍然成立
bad2 = bytearray(data)
bad2[BS + 3] ^= 0xFF
r_bad2, lv_bad2 = build_merkle_tree(bytes(bad2), BS, A)
check("只改第 1 块 → 第 1 块验不过", not verify_block(bytes(bad2), 1, r2, BS, A,
                                                    levels=lv_bad2))
check("但第 0 块仍然验得过（定位到被改的那一块）",
      verify_block(bytes(bad2), 0, r_bad2, BS, A, levels=lv_bad2))

print("== 6. fs-verity descriptor ==")
d = build_descriptor(len(data), 12, A, r2, salt=b"\x01" * 8)
check("descriptor 长度 256", len(d) == 256)
check("version == 1", d[DESC_OFF_VERSION] == 1)
check("hash_algorithm == 1(SHA-256)", d[DESC_OFF_HASH_ALG] == 1)
check("log_blocksize == 12（4K 块）", d[DESC_OFF_LOG_BLOCKSIZE] == 12)
check("salt_size == 8", d[DESC_OFF_SALT_SIZE] == 8)
check("data_size 小端 8 字节",
      int.from_bytes(d[DESC_OFF_DATA_SIZE:DESC_OFF_DATA_SIZE + 8], "little") == len(data))
check("root_hash 落在偏移 16", d[DESC_OFF_ROOT_HASH:DESC_OFF_ROOT_HASH + 32] == r2)
check("salt 落在偏移 80", d[DESC_OFF_SALT:DESC_OFF_SALT + 8] == b"\x01" * 8)
check("root_hash 后 32 字节仍为 0（64 字节字段只用了 32）",
      d[DESC_OFF_ROOT_HASH + 32:DESC_OFF_ROOT_HASH + 64] == b"\x00" * 32)
check("文件摘要 != root（摘要是 hash(descriptor)）", file_digest(d, A) != r2)
check("文件摘要长度 == 32", len(file_digest(d, A)) == 32)
# data_size 参与摘要 → 同样 root 但长度不同的文件摘要不同
d2 = build_descriptor(len(data) + 1, 12, A, r2, salt=b"\x01" * 8)
check("data_size 变了摘要就变（这正是 descriptor 存在的意义）",
      file_digest(d, A) != file_digest(d2, A))
check("descriptor 里 data_size 字段确实不同",
      d[DESC_OFF_DATA_SIZE:DESC_OFF_DATA_SIZE + 8] !=
      d2[DESC_OFF_DATA_SIZE:DESC_OFF_DATA_SIZE + 8])

print("== 7. dm-integrity 参数与几何 ==")
check("默认 interleave_sectors 32768", DM_INTEGRITY_DEFAULT_INTERLEAVE_SECTORS == 32768)
check("默认 block_size 512", DM_INTEGRITY_DEFAULT_BLOCK_SIZE == 512)
check("journal_watermark 默认 50", DM_INTEGRITY_DEFAULT_JOURNAL_WATERMARK == 50)
check("commit_time 默认 10000 ms", DM_INTEGRITY_DEFAULT_COMMIT_TIME_MS == 10000)
check("interleave 向下取到 2 的幂：32768→32768", rounddown_pow2(32768) == 32768)
check("interleave 50000 → 32768", rounddown_pow2(50000) == 32768)
check("interleave 100 → 64", rounddown_pow2(100) == 64)

g = integrity_geometry(32768, 512, 4)
check("tag_bytes = 32768*4 = 131072（128 KiB）", g["tag_bytes"] == 131072,
      "%d" % g["tag_bytes"])
check("tag_sectors = 256（文档：256 sectors of metadata per data area）",
      g["tag_sectors"] == 256, "%d" % g["tag_sectors"])
check("buffers_per_run = 2（文档：2 buffers per metadata area）",
      g["buffers_per_run"] == 2, "%d" % g["buffers_per_run"])
check("数据扇区数向下取到 2 的幂：32768-256=32512 → 16384",
      g["data_sectors"] == 16384, "%d" % g["data_sectors"])
check("tag 占比 = 256/(256+16384)",
      abs(g["overhead_ratio"] - 256 / (256 + 16384)) < 1e-12,
      "%.5f" % g["overhead_ratio"])
g2 = integrity_geometry(32768, 4096, 32)
check("4K 块 + 32 字节 tag → tag 涨到 2048 扇区（32768*32=1MiB）",
      g2["tag_bytes"] == 32768 * 32 and g2["tag_bytes"] // SECTOR_SIZE == 2048,
      "tag_bytes=%d" % g2["tag_bytes"])
check("tag 越大开销占比越高（可用扇区被 pow2 取整掩盖了差异）",
      g2["overhead_ratio"] > g["overhead_ratio"],
      "%.5f > %.5f" % (g2["overhead_ratio"], g["overhead_ratio"]))
check("但 data_sectors 都被 pow2 向下取到 16384，两者相同",
      g["data_sectors"] == g2["data_sectors"] == 16384,
      "%d / %d" % (g["data_sectors"], g2["data_sectors"]))

print()
print("TOTAL: %d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
