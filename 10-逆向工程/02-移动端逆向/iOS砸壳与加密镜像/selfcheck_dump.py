"""iOS 砸壳自检：逐条对照 xnu 源码 mach-o/loader.h。

运行：python selfcheck_dump.py
"""

from macho_dump import (
    MH_MAGIC_64, MH_CIGAM_64, LC_SYMTAB, LC_SEGMENT_64, LC_UUID,
    LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64,
    MH_EXECUTE, MH_DYLIB, MH_PIE, MH_DYLIB_IN_CACHE,
    HEADER_64_SIZE, SEGMENT_64_SIZE, ENCRYPTION_INFO_SIZE, ENCRYPTION_INFO_64_SIZE,
    CRYPTID_OFFSET, MachO, MachOError, page_aligned_range, build_sample,
)

PASS = [0]


def eq(a, b, label):
    assert a == b, "FAILED: %s (期望 %r 实际 %r)" % (label, b, a)
    PASS[0] += 1


def ok(cond, label):
    assert cond, "FAILED: " + label
    PASS[0] += 1


# ---------- 1. 常量（loader.h） ----------

eq(MH_MAGIC_64, 0xFEEDFACF, "MH_MAGIC_64")
eq(MH_CIGAM_64, 0xCFFAEDFE, "MH_CIGAM_64 是 MH_MAGIC_64 的字节交换")
eq(LC_SYMTAB, 0x2, "LC_SYMTAB")
eq(LC_SEGMENT_64, 0x19, "LC_SEGMENT_64")
eq(LC_UUID, 0x1B, "LC_UUID")
eq(LC_ENCRYPTION_INFO, 0x21, "LC_ENCRYPTION_INFO")
eq(LC_ENCRYPTION_INFO_64, 0x2C, "LC_ENCRYPTION_INFO_64")
eq(MH_EXECUTE, 0x2, "MH_EXECUTE")
eq(MH_DYLIB, 0x6, "MH_DYLIB")
eq(MH_PIE, 0x200000, "MH_PIE")
eq(MH_DYLIB_IN_CACHE, 0x80000000, "MH_DYLIB_IN_CACHE")

# ---------- 2. 结构体大小 ----------

eq(HEADER_64_SIZE, 32, "mach_header_64 是 8 个 uint32 = 32 字节")
eq(SEGMENT_64_SIZE, 72, "segment_command_64 = 4+4+16+32+16 = 72")
eq(ENCRYPTION_INFO_SIZE, 20, "encryption_info_command = 5 个 uint32 = 20")
eq(ENCRYPTION_INFO_64_SIZE, 24, "encryption_info_command_64 补 pad 到 8 的倍数 = 24")
eq(ENCRYPTION_INFO_64_SIZE % 8, 0, "pad 的作用是让结构体大小成为 8 的倍数")
eq(CRYPTID_OFFSET, 16, "cryptid 在命令内偏移 16（cmd+cmdsize+cryptoff+cryptsize）")

# ---------- 3. 解析构造出的样本 ----------

blob, meta = build_sample()
m = MachO(blob)
eq(m.cputype, 0, "cputype 字段落位")
eq(m.filetype, MH_EXECUTE, "filetype 是 MH_EXECUTE")
eq(m.ncmds, 2, "两个 load command")
eq(m.sizeofcmds, SEGMENT_64_SIZE + ENCRYPTION_INFO_64_SIZE, "sizeofcmds 等于两条命令之和")
ok(bool(m.flags & MH_PIE), "MH_PIE 置位（ASLR）")
text = m.segment_named("__TEXT")
ok(text is not None, "解析出 __TEXT 段")
eq(text.fileoff, meta["text_off"], "__TEXT 的 fileoff")
eq(text.filesize, 0x2000, "__TEXT 的 filesize")
ok(m.is_encrypted(), "cryptid 非 0 → 判定为已加密")
eq(m.crypt["cryptoff"], meta["cryptoff"], "cryptoff 解析")
eq(m.crypt["cryptsize"], meta["cryptsize"], "cryptsize 解析")
eq(m.crypt["cmd"], LC_ENCRYPTION_INFO_64, "用的是 64 位加密命令")

# ---------- 4. dump_plan：文件偏移 -> 虚拟地址 ----------

plan = m.dump_plan()
eq(plan["segment"], "__TEXT", "加密区间落在 __TEXT")
eq(plan["file_offset"], meta["cryptoff"], "计划的文件偏移就是 cryptoff")
eq(plan["size"], meta["cryptsize"], "计划的长度就是 cryptsize")
# 关键换算：vmaddr + (cryptoff - fileoff)
eq(plan["vmaddr"], text.vmaddr + (meta["cryptoff"] - text.fileoff), "vmaddr 按段内偏移换算")
eq(plan["vmaddr"], 0x100001000, "换算结果")
ok(text.fileoff_to_vmaddr(meta["cryptoff"]) == plan["vmaddr"], "段内换算函数与计划一致")
try:
    text.fileoff_to_vmaddr(0)
    raise AssertionError("本应抛错")
except MachOError:
    PASS[0] += 1        # 段外偏移不允许换算

# ---------- 5. 执行砸壳：内存 dump + patch cryptid ----------

# 模拟进程内存：整个镜像的明文（已解密），加密区在内存里是 0x42
IMAGE_BASE = text.vmaddr
memory = bytearray(b"\x00" * (text.vmsize + 0x1000))
for i in range(0x1000, 0x2000):
    memory[i] = 0x42                       # 加密区在内存中的明文
before = m.bytes()[meta["cryptoff"]:meta["cryptoff"] + 8]
eq(set(before), {0xEE}, "砸壳前该区间是密文标记")
m.apply_dump(bytes(memory), IMAGE_BASE, plan)
after = m.bytes()[meta["cryptoff"]:meta["cryptoff"] + 8]
eq(set(after), {0x42}, "砸壳后该区间变成内存里的明文")
# 非加密区不能被误改
ok(set(m.bytes()[meta["text_off"]:meta["text_off"] + 8]) == {0x41}, "未加密区保持原样")
eq(len(m.bytes()), len(blob), "砸壳不改变文件长度")

eq(m.crypt["cryptid"], 1, "dump 之后 cryptid 还是 1")
m.patch_cryptid(0)
eq(m.crypt["cryptid"], 0, "patch 后 cryptid 为 0")
ok(not m.is_encrypted(), "cryptid 为 0 → 不再判定为加密")
# 重新解析一遍，确认改动落到字节流里
m2 = MachO(m.bytes())
ok(not m2.is_encrypted(), "重新解析后仍是未加密")
eq(m2.crypt["cryptoff"], meta["cryptoff"], "cryptoff 未被改动")
eq(set(m2.bytes()[meta["cryptoff"]:meta["cryptoff"] + 8]), {0x42}, "明文被持久化")

# ---------- 6. 页对齐：dump 必须覆盖整个页 ----------

start, size = page_aligned_range(0x4000 + 0x1000, 0x1000, 0x1000)
eq(start, 0x5000, "整页对齐后的起始偏移")
eq(size, 0x1000, "整页对齐后的长度")
# 长度不是页整数倍时，dump 范围要向外扩到整页
start2, size2 = page_aligned_range(0x5000, 0x800, 0x1000)
eq((start2, size2), (0x5000, 0x1000), "不足一页时向上取整到整页")
# 起始不是页首时，要向前扩到页首
start3, size3 = page_aligned_range(0x5800, 0x100, 0x1000)
eq((start3, size3), (0x5000, 0x1000), "跨页时向前扩到页首")
# 不同页大小下的行为（页大小由调用方给定，本模型不假定具体值）
start4, size4 = page_aligned_range(0x4800, 0x100, 0x4000)
eq((start4, size4), (0x4000, 0x4000), "页大小 0x4000 时同样按页取整")

# ---------- 7. 未加密样本与 dyld 共享缓存 ----------

plain, _ = build_sample(encrypted=False)
mp = MachO(plain)
ok(not mp.is_encrypted(), "cryptid 为 0 的样本判定为未加密")
eq(mp.crypt["cryptid"], 0, "未加密样本的 cryptid 就是 0")
md = MachO(plain)
md.flags |= MH_DYLIB_IN_CACHE
ok(md.in_dyld_shared_cache(), "MH_DYLIB_IN_CACHE 置位即表示在 dyld 共享缓存里")
ok(not MachO(plain).in_dyld_shared_cache(), "普通 Mach-O 不在共享缓存里")

# ---------- 8. 异常路径 ----------

try:
    MachO(b"\x00" * 64)
    raise AssertionError("本应抛错")
except MachOError:
    PASS[0] += 1        # magic 不对

print("PASS %d 项断言全部通过" % PASS[0])
