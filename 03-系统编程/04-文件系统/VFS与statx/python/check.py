"""vfs_statx 自检：把 statx(2) / stat.h / fcntl.h 的条文逐条变成断言。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vfs_statx import (  # noqa: E402
    Filesystem, Inode, STATX_ALL, STATX_ATIME, STATX_BASIC_STATS, STATX_BLOCKS,
    STATX_BTIME, STATX_CTIME, STATX_DIOALIGN, STATX_GID, STATX_INO,
    STATX_MODE, STATX_MNT_ID, STATX_MNT_ID_UNIQUE, STATX_MTIME,
    STATX_NLINK, STATX_SIZE, STATX_TYPE, STATX_UID, STATX__RESERVED,
    STATX_ATTR_APPEND, STATX_ATTR_AUTOMOUNT, STATX_ATTR_COMPRESSED,
    STATX_ATTR_DAX, STATX_ATTR_IMMUTABLE, STATX_ATTR_MOUNT_ROOT,
    STATX_ATTR_VERITY, S_IFDIR, S_IFLNK, S_IFMT, S_IFREG,
    AT_STATX_DONT_SYNC, AT_STATX_FORCE_SYNC, AT_STATX_SYNC_AS_STAT,
    allocated_bytes, apparent_ratio, attr_names, dio_align_consistent,
    mask_names, s_isdir, s_islnk, s_isreg, statx, supported_attrs, sync_mode,
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


def einval_mask(mask):
    try:
        statx(EXT4, Inode(1, S_IFREG, 0, 0), mask)
    except ValueError as e:
        return "EINVAL" in str(e)
    return False


def einval_flags(flags):
    try:
        statx(EXT4, Inode(1, S_IFREG, 0, 0), STATX_TYPE, flags)
    except ValueError as e:
        return "EINVAL" in str(e)
    return False


ALL_BASIC = (STATX_TYPE | STATX_MODE | STATX_NLINK | STATX_UID | STATX_GID
             | STATX_ATIME | STATX_MTIME | STATX_CTIME | STATX_INO
             | STATX_SIZE | STATX_BLOCKS)

# ext4 风格：btime / mnt_id / dioalign 都支持；INO 不请白送
EXT4 = Filesystem(
    "ext4",
    supported=ALL_BASIC | STATX_BTIME | STATX_MNT_ID | STATX_DIOALIGN
    | STATX_MNT_ID_UNIQUE,
    free_bits=STATX_INO,
    attr_mask=STATX_ATTR_IMMUTABLE | STATX_ATTR_APPEND | STATX_ATTR_COMPRESSED,
    attributes=STATX_ATTR_IMMUTABLE,
    blksize=4096,
)

# NFS 风格：btime 不支持、属性位一个都不支持；UID/GID 靠挂载编造
NFS = Filesystem(
    "nfs",
    supported=ALL_BASIC,
    attr_mask=0,
    attributes=STATX_ATTR_DAX,  # fs 自己报了 DAX，但 VFS 不支持 → 必须屏蔽
    blksize=4096,
    dummy_uid=65534,
)

# CIFS 风格：UID 不支持，靠挂载选项编造一个默认 uid（头注释点名的例子）
CIFS = Filesystem(
    "cifs",
    supported=ALL_BASIC & ~STATX_UID,
    dummy_uid=65534,
    blksize=4096,
)

ino = Inode(131073, S_IFREG, 4096, 8, uid=1000, gid=1000, mtime=1700000000,
            btime=1699000000, mnt_id=27, dio_mem=512, dio_off=512)

print("== 1. mask 常量与保留位 ==")
check("STATX_BASIC_STATS 是 11 个基础位之或", STATX_BASIC_STATS == ALL_BASIC,
      "0x%03x" % STATX_BASIC_STATS)
check("STATX_ALL == BASIC_STATS|BTIME（已废弃）",
      STATX_ALL == (STATX_BASIC_STATS | STATX_BTIME), "0x%03x" % STATX_ALL)
check("mask 带 STATX__RESERVED → EINVAL", einval_mask(STATX__RESERVED),
      "0x80000000")

print("== 2. AT_STATX_* 同步三态 ==")
check("默认 = SYNC_AS_STAT", sync_mode(0) == "as_stat")
check("FORCE_SYNC", sync_mode(AT_STATX_FORCE_SYNC) == "force_sync")
check("DONT_SYNC", sync_mode(AT_STATX_DONT_SYNC) == "dont_sync")
check("FORCE|DONT 同时置位 → EINVAL",
      einval_flags(AT_STATX_FORCE_SYNC | AT_STATX_DONT_SYNC))
check("SYNC_AS_STAT 数值为 0（可省略）", AT_STATX_SYNC_AS_STAT == 0)

print("== 3. 请求了不等于拿得到（不支持的位被清） ==")
buf, _ = statx(EXT4, ino, STATX_BTIME)
check("ext4 支持 btime → 位被置上", bool(buf.stx_mask & STATX_BTIME),
      "btime=%s" % buf.stx_btime)
buf, _ = statx(NFS, ino, STATX_BTIME)
check("nfs 不支持 btime → 位被清掉", not (buf.stx_mask & STATX_BTIME),
      "stx_mask=%s" % mask_names(buf.stx_mask))

print("== 4. 没请求也可能白送（stx_mask 可以超出 mask） ==")
buf, _ = statx(EXT4, ino, STATX_SIZE)
check("只请求 SIZE 却额外拿到 INO", bool(buf.stx_mask & STATX_INO),
      "stx_mask=%s" % mask_names(buf.stx_mask))
check("stx_mask != mask（超出请求）", buf.stx_mask != STATX_SIZE,
      "0x%03x" % buf.stx_mask)
buf, _ = statx(NFS, ino, STATX_BTIME)
check("stx_mask != mask（请求被拒）", buf.stx_mask != STATX_BTIME,
      "nfs: %s" % mask_names(buf.stx_mask))

print("== 5. 编造值与属性屏蔽 ==")
buf, _ = statx(CIFS, ino, STATX_UID)
check("不支持 UID 时仍编出 dummy uid", buf.stx_uid == 65534,
      "stx_uid=%d" % buf.stx_uid)
check("但 UID 位没有置上（编造值不可信）", not (buf.stx_mask & STATX_UID),
      "stx_mask=%s" % mask_names(buf.stx_mask))
check("同一 fs 上 GID 仍正常返回",
      bool(statx(CIFS, ino, STATX_GID)[0].stx_mask & STATX_GID))
buf, _ = statx(NFS, ino, STATX_TYPE)
check("attr_mask=0 时任何属性位都不可用", supported_attrs(buf) == 0,
      "raw=0x%x mask=0x%x" % (buf.stx_attributes, buf.stx_attributes_mask))
buf, _ = statx(EXT4, ino, STATX_TYPE)
check("ext4 只认 IMMUTABLE（其余位被屏蔽）",
      supported_attrs(buf) == STATX_ATTR_IMMUTABLE,
      "%s" % attr_names(supported_attrs(buf)))
check("五个属性位互不相等",
      len({STATX_ATTR_DAX, STATX_ATTR_VERITY, STATX_ATTR_MOUNT_ROOT,
           STATX_ATTR_AUTOMOUNT, STATX_ATTR_COMPRESSED}) == 5)

print("== 6. stx_blocks 是 512 字节单位 ==")
buf, _ = statx(EXT4, ino, STATX_BLOCKS)
check("blocks=8 → 实际分配 4096 字节", allocated_bytes(buf) == 4096,
      "%d" % allocated_bytes(buf))
sparse = Inode(131074, S_IFREG, 1024 * 1024, 8)
size, used = apparent_ratio(sparse)
check("稀疏文件：分配 < 逻辑大小", used < size, "%d < %d" % (used, size))
check("比率 1/256", size // used == 256, "%d" % (size // used))

print("== 7. 符号链接的 stx_size 不含结尾 NUL ==")
lnk = Inode(131075, S_IFLNK, 0, 0, link_target="/etc/hostname")
buf, _ = statx(EXT4, lnk, STATX_SIZE)
check("stx_size == 目标路径长度", buf.stx_size == len("/etc/hostname"),
      "%d" % buf.stx_size)
check("且不等于长度+1（无 NUL）", buf.stx_size != len("/etc/hostname") + 1)

print("== 8. st_mode 与 S_IFMT ==")
check("S_IFMT 掩出常规文件", s_isreg(ino.mode) and not s_isdir(ino.mode))
check("S_IFMT 掩出目录", s_isdir(S_IFDIR | 0o755))
check("S_IFMT 掩出符号链接", s_islnk(lnk.mode))
check("S_IFMT 值为 0170000", S_IFMT == 0o170000)
check("mode 低 12 位不参与类型判定",
      s_isreg(S_IFREG | 0o777) and s_isreg(S_IFREG))

print("== 9. mount id 与 DIO 对齐 ==")
buf, _ = statx(EXT4, ino, STATX_MNT_ID | STATX_DIOALIGN)
check("MNT_ID 取到挂载 id", buf.stx_mnt_id == 27)
check("DIO mem/offset 对齐成对出现", dio_align_consistent(buf),
      "mem=%d off=%d" % (buf.stx_dio_mem_align, buf.stx_dio_offset_align))
buf, _ = statx(EXT4, Inode(131076, S_IFREG, 4096, 8), STATX_DIOALIGN)
check("不支持 O_DIRECT 时两者同为 0",
      buf.stx_dio_mem_align == 0 and buf.stx_dio_offset_align == 0)
check("MNT_ID_UNIQUE 是独立位",
      STATX_MNT_ID_UNIQUE != STATX_MNT_ID
      and (STATX_MNT_ID_UNIQUE & STATX_MNT_ID) == 0,
      "0x%x vs 0x%x" % (STATX_MNT_ID_UNIQUE, STATX_MNT_ID))

print("== 10. 同一趟调用里字段可能来自不同时刻 ==")


def race(bit):
    if bit == STATX_MODE:
        ino.uid = 2002  # 填完 mode 之后被并发 chown


buf, _ = statx(EXT4, ino, STATX_MODE | STATX_UID, on_field=race)
check("拿到旧 mode + 新 uid（跨时刻组合）",
      (buf.stx_mode & S_IFMT) == S_IFREG and buf.stx_uid == 2002,
      "uid=%d" % buf.stx_uid)
ino.uid = 1000

print("== 11. 请求空掩码 ==")
buf, _ = statx(EXT4, ino, 0)
check("mask=0 仍带回 [uncond] 字段",
      buf.stx_blksize == 4096 and buf.stx_attributes_mask != 0)
check("mask=0 时 stx_mask 只含白送位", mask_names(buf.stx_mask) == ["INO"],
      "%s" % mask_names(buf.stx_mask))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
