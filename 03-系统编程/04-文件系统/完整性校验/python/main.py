"""演示入口：fs-verity Merkle 树与 dm-integrity 布局。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verity import (  # noqa: E402
    DM_INTEGRITY_DEFAULT_INTERLEAVE_SECTORS, FS_VERITY_HASH_ALG_SHA256,
    FS_VERITY_HASH_ALG_SHA512, arity, build_descriptor, build_merkle_tree,
    file_digest, integrity_geometry, merkle_level_sizes, merkle_tree_overhead,
    pad_salt,
)

BS = 4096
A = FS_VERITY_HASH_ALG_SHA256


def hdr(t):
    print()
    print("== %s ==" % t)


hdr("salt 零填充（填充到压缩函数输入块的整数倍）")
for n in (0, 13, 32, 64, 65):
    print("  %-3d 字节 salt(SHA-256) → %-4d 字节"
          % (n, len(pad_salt(b"a" * n, A))))
print("  %-3d 字节 salt(SHA-512) → %-4d 字节"
      % (13, len(pad_salt(b"a" * 13, FS_VERITY_HASH_ALG_SHA512))))
print("  >>> 目的：让每个 hash 的输入长度一致（便于预计算 salted 状态 / 硬件加速）")

hdr("扇出与层级")
print("  SHA-256 + 4K 块 → arity = 4096/32 = %d" % arity(BS, 32))
print("  SHA-512 + 4K 块 → arity = 4096/64 = %d" % arity(BS, 64))
for nblk in (1, 2, 128, 129, 16384):
    lv = merkle_level_sizes(nblk * BS, BS, 32)
    print("  %-6d 块 → 层级 %s" % (nblk, lv))

hdr("1/127 收敛")
for nblk in (2, 128, 16384, 128 * 128 * 128):
    size = nblk * BS
    oh = merkle_tree_overhead(size, BS, 32)
    print("  %-8d 块（%d MiB）→ 树 %-8d 字节，占比 %.4f"
          % (nblk, size >> 20, oh, oh / size))
print("  >>> 大文件收敛到 1/127；小文件被填充主导（2 块文件开销 50%）")

hdr("Merkle root")
for name, data in (("空文件", b""), ("1 块", b"hello fs-verity"),
                   ("2 块", b"x" * (BS + 10))):
    root, lv = build_merkle_tree(data, BS, A)
    print("  %-6s → root=%s  层数=%d" % (name, root.hex()[:24] + "...", len(lv)))
d = build_descriptor(BS + 10, 12, A, build_merkle_tree(b"x" * (BS + 10), BS, A)[0])
print("  descriptor(256B) → 文件摘要 = sha256(descriptor) = %s"
      % file_digest(d, A).hex()[:24] + "...")
print("  >>> 文件摘要 ≠ Merkle root：root 有歧义，descriptor 带上 data_size 才唯一")

hdr("dm-integrity 几何（默认 interleave_sectors=%d）"
    % DM_INTEGRITY_DEFAULT_INTERLEAVE_SECTORS)
for bs, tag in ((512, 4), (512, 16), (4096, 32)):
    g = integrity_geometry(32768, bs, tag)
    print("  block=%-5d tag=%-3d → 标签 %-7d 扇区, 数据 %-6d 扇区,"
          " buffer/区 %d, 开销 %.4f"
          % (bs, tag, g["tag_sectors"], g["data_sectors"],
             g["buffers_per_run"], g["overhead_ratio"]))
