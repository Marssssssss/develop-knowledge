"""
jbd2_sim.py — JBD2 journal 块格式 in-memory 模拟器

运行: python3 jbd2_sim.py

演示:
  1. 写入一笔事务: descriptor + data + commit,展示字节布局
  2. Recovery:顺序扫描 journal,重放已 commit 事务,丢弃未 commit
  3. JBD2 大端 vs ext4 小端:同一份数据两种序列化
  4. JBD2_FLAG_ESCAPE:当 data block 前 4 字节 == magic 时的处理
"""

import struct

BLCKSIZE = 4096
JBD2_MAGIC = 0xC03B3998
DESC_BLOCK = 1
COMMIT_BLOCK = 2
REVOKE_BLOCK = 5
SAME_UUID = 0x2
ESCAPE = 0x1
LAST_TAG = 0x8


def be32(n):
    return struct.pack(">I", n & 0xFFFFFFFF)


def be32u(buf, off=0):
    return struct.unpack_from(">I", buf, off)[0]


def make_descriptor_block(seq, tags):
    """tags: list of (blocknr:int, flags:int, uuid:bytes(16))"""
    hdr = be32(JBD2_MAGIC) + be32(DESC_BLOCK) + be32(seq)
    body = b""
    for i, (bn, flags, uuid) in enumerate(tags):
        f = flags | (LAST_TAG if i == len(tags) - 1 else 0)
        body += be32(bn) + be32(f)
        if not (f & SAME_UUID):
            body += uuid
    return (hdr + body).ljust(BLCKSIZE, b'\x00')


def make_commit_block(seq):
    return (be32(JBD2_MAGIC) + be32(COMMIT_BLOCK) + be32(seq)).ljust(BLCKSIZE, b'\x00')


def parse_header(block):
    return be32u(block, 0), be32u(block, 4), be32u(block, 8)


def parse_tags(descriptor_block):
    """从 offset 12 起按 SAME_UUID flag 解析 tag 数组"""
    tags = []
    off = 12
    while off < BLCKSIZE:
        bn = be32u(descriptor_block, off); off += 4
        fl = be32u(descriptor_block, off); off += 4
        tags.append((bn, fl))
        if fl & LAST_TAG:
            break
        if not (fl & SAME_UUID):
            off += 16
    return tags


def demo_write_transaction():
    print("\n=== demo 1: write a transaction (descriptor + data + commit) ===")
    seq = 42
    uuid = b'\xaa\xbb\xcc\xdd' + b'\x00' * 11 + b'\x01'
    tags = [(256, SAME_UUID, uuid), (257, SAME_UUID, uuid)]
    descriptor = make_descriptor_block(seq, tags)
    data0 = b"INODE_BLOCK_FOR_FILE_42".ljust(BLCKSIZE, b'\x00')
    data1 = b"DIRENT: hello.txt -> inode 42".ljust(BLCKSIZE, b'\x00')
    commit = make_commit_block(seq)
    log = descriptor + data0 + data1 + commit

    print("descriptor header bytes (12):", descriptor[:12].hex(" "))
    print("  expect: c03b3998 (magic)  00000001 (descriptor)  0000002a (seq=42)")
    print("descriptor tags:")
    print(f"  tag 0: blocknr=256, flags=0x{parse_tags(descriptor)[0][1]:x}")
    print(f"  tag 1: blocknr=257, flags=0x{parse_tags(descriptor)[1][1]:x}  "
          f"(LAST_TAG|SAME_UUID = 0x{LAST_TAG|SAME_UUID:x})")
    print("commit header bytes (12):", commit[:12].hex(" "))
    print("  expect: c03b3998  00000002  0000002a")

    return log


def demo_replay(log):
    print("\n=== demo 2: replay committed, drop uncommitted ===")
    # 构造: T1 完整(有 commit),T2 缺 commit(模拟崩溃)
    seq1, seq2 = 10, 11
    t1 = (make_descriptor_block(seq1, [(100, SAME_UUID, b'\x00'*16)]) +
          b"T1_data_at_block_100".ljust(BLCKSIZE, b'\x00') +
          make_commit_block(seq1))
    t2 = (make_descriptor_block(seq2, [(200, SAME_UUID, b'\x00'*16)]) +
          b"T2_data_at_block_200_orphan".ljust(BLCKSIZE, b'\x00'))
    log = t1 + t2

    n = len(log) // BLCKSIZE
    print(f"log contains {n} blocks (3 from T1 + 2 from T2)")
    replayed = []
    i = 0
    while i < n:
        m, t, s = parse_header(log[i * BLCKSIZE:])
        if m != JBD2_MAGIC:
            i += 1
            continue
        if t == DESC_BLOCK:
            data_start = i + 1
            j = data_start
            found_commit = False
            while j < n:
                m2, t2_, s2 = parse_header(log[j * BLCKSIZE:])
                if m2 != JBD2_MAGIC:
                    j += 1
                    continue
                if t2_ in (DESC_BLOCK, REVOKE_BLOCK):
                    break
                if t2_ == COMMIT_BLOCK and s2 == s:
                    found_commit = True
                    break
                j += 1
            if found_commit:
                print(f"  ✓ replay T#{s} (data blocks {data_start}..{j-1})")
                replayed.append(s)
                i = j + 1
            else:
                print(f"  ✗ drop T#{s} (no commit block found)")
                i = j if j > i else i + 1
        else:
            i += 1
    print(f"total replayed transactions: {len(replayed)} (expect [10])")
    assert replayed == [10], f"replay mismatch: got {replayed}"


def demo_endian():
    print("\n=== demo 3: JBD2 big-endian vs ext4 little-endian ===")
    v = 42
    be = struct.pack(">I", v)
    le = struct.pack("<I", v)
    print(f"value 42 → JBD2 big-endian   : {' '.join(f'{b:02x}' for b in be)}")
    print(f"value 42 → ext4 little-endian: {' '.join(f'{b:02x}' for b in le)}")
    print("  → 解析 real ext4 journal 必须用 be32_to_cpu()/cpu_to_be32()")


def demo_escape():
    print("\n=== demo 4: JBD2_FLAG_ESCAPE — data 前 4 字节 == magic 时 ===")
    block = bytearray(BLCKSIZE)
    struct.pack_into(">I", block, 0, JBD2_MAGIC)
    block[4:4+18] = b"rest of payload..."

    # 写入时:检测到前 4B == magic → 清零 + set ESCAPE flag
    on_disk = bytearray(block)
    needs_escape = (be32u(on_disk) == JBD2_MAGIC)
    if needs_escape:
        on_disk[0:4] = b'\x00\x00\x00\x00'

    # replay 时:tag.flags 含 ESCAPE → 还原前 4B 为 magic
    recovered = bytearray(on_disk)
    if needs_escape:
        struct.pack_into(">I", recovered, 0, JBD2_MAGIC)

    print(f"original block[0..7] : {bytes(block[:8]).hex(' ')}")
    print(f"on_disk[0..7]        : {bytes(on_disk[:8]).hex(' ')}  (前 4B 清零)")
    print(f"recovered[0..7]      : {bytes(recovered[:8]).hex(' ')}  (前 4B 还原)")
    assert bytes(block) == bytes(recovered), "ESCAPE round-trip failed!"


if __name__ == "__main__":
    demo_write_transaction()
    demo_replay(None)
    demo_endian()
    demo_escape()
    print("\nall 4 demos done")