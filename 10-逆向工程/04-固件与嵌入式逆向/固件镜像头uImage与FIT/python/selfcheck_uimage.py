"""demo591 自检：uImage 头 + FIT 结构与签名选区。

每条断言都对应一处官方源码/规范条款。成对构造的用例会注明对偶面。
"""

import hashlib
import struct
import sys

import uimage
from uimage import (IH_MAGIC, LEGACY_HDR_SIZE, crc32, LegacyHeader, parse_legacy,
                    check_hcrc, check_dcrc, image_size, data_offset,
                    multi_sizes, build_multi, multi_part_offsets,
                    IH_OS, IH_ARCH, IH_TYPE, IH_COMP)
from fit import (HASH_ALGO_SIZE, SIG_ALGO_SIZE, validate, node_list, hashed_struct,
                 hashed_region, image_hash, make_sample_fit,
                 referenced_images, EXCLUDED_DATA_PROPS)
from fdt import build_fdt, parse_fdt, FDT_MAGIC, FDT_BEGIN_NODE, FDT_PROP, FDT_END

PASS = 0
FAIL = []


def ck(cond, label):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)


def eq(got, want, label):
    ck(got == want, "%s (got=%r want=%r)" % (label, got, want))


# ---------------------------------------------------------------- A. 常量
eq(IH_MAGIC, 0x27051956, "A1 IH_MAGIC")
eq(LEGACY_HDR_SIZE, 64, "A2 legacy header 64 字节")
eq(uimage.IH_NMLEN, 32, "A3 IH_NMLEN")
eq(IH_OS["linux"], 5, "A4 IH_OS_LINUX")
eq(IH_ARCH["arm"], 2, "A5 IH_ARCH_ARM")
eq(IH_TYPE["kernel"], 2, "A6 IH_TYPE_KERNEL")
eq(IH_TYPE["ramdisk"], 3, "A7 IH_TYPE_RAMDISK")
eq(IH_TYPE["multi"], 4, "A8 IH_TYPE_MULTI")
eq(IH_TYPE["filesystem"], 7, "A9 IH_TYPE_FILESYSTEM")
eq(IH_TYPE["flatdt"], 8, "A10 IH_TYPE_FLATDT")
eq(IH_COMP["none"], 0, "A11 IH_COMP_NONE")
eq(IH_COMP["gzip"], 1, "A12 IH_COMP_GZIP")
eq(IH_COMP["zstd"], 6, "A13 IH_COMP_ZSTD")

# ---------------------------------------------------------------- B. CRC 口径
eq(crc32(b"123456789", 0xFFFFFFFF), 0x340BC6D9, "B1 寄存器初值 ~0 时得到 JAMCRC 校验值")
eq(crc32(b"123456789", 0), 0x2DFD2D88, "B2 寄存器初值 0（JFFS2/SquashFS 的口径）")
import zlib as _z
eq(crc32(b"abc", 0xFFFFFFFF) ^ 0xFFFFFFFF, _z.crc32(b"abc"),
   "B3 与 zlib 的关系：zlib 额外做了首尾两次取反")
eq(crc32(b"hello" + b"world", 0), crc32(b"world", crc32(b"hello", 0)),
   "B4 可增量串联：crc(a+b) = crc(b, crc(a))")
ck(crc32(b"abc", 0) != crc32(b"abc", 0xFFFFFFFF), "B5 初值不同则结果不同")

payload = bytes(range(64)) * 3
h = LegacyHeader(time=1700000000, size=len(payload), load=0x80008000,
                 ep=0x80008000, os=IH_OS["linux"], arch=IH_ARCH["arm"],
                 type=IH_TYPE["kernel"], comp=IH_COMP["gzip"],
                 name=b"Linux-6.6.0")
h.seal(payload)
blob = h.pack() + payload
ck(check_hcrc(h), "B4 自己算的 hcrc 能过校验")
ck(check_dcrc(h, payload), "B5 dcrc 覆盖 payload")
ck(uimage.check_magic(h), "B6 magic 校验")
ck(not uimage.check_magic(parse_legacy(b"\x00" * 64 + payload)), "B6b 空头不过 magic")
eq(h.dcrc, crc32(payload), "B7 dcrc = crc32(0, payload)")
eq(h.hcrc, crc32(h.with_hcrc_zeroed().pack()), "B8 hcrc 计算时自身字段清零")

b2 = bytearray(blob)
b2[64] ^= 0xFF                      # 改 payload 的第一个字节（头占 0..63）
ck(not check_dcrc(parse_legacy(bytes(b2)), bytes(b2[64:])), "B9 改 payload 后 dcrc 失败")
ck(check_hcrc(parse_legacy(bytes(b2))), "B10 改 payload 不影响 hcrc（对偶）")

b3 = bytearray(blob)
b3[32] ^= 0xFF                      # 改 ih_name 首字节（偏移 32）
ck(not check_hcrc(parse_legacy(bytes(b3))), "B11 改名字后 hcrc 失败")
ck(check_dcrc(parse_legacy(bytes(b3)), payload), "B12 改名字不影响 dcrc（对偶）")

b4 = bytearray(blob)
b4[4:8] = struct.pack(">I", 0)      # 直接把 hcrc 抹成 0
ck(not check_hcrc(parse_legacy(bytes(b4))), "B13 hcrc 置 0 后校验失败")

# ---------------------------------------------------------------- C. 大端与布局
p = parse_legacy(blob)
eq(p.magic, IH_MAGIC, "C1 magic 大端读出")
eq(p.load, 0x80008000, "C2 load 大端读出")
eq(p.name.rstrip(b"\0"), b"Linux-6.6.0", "C3 name 定长 32 字节，尾部补零")
eq(len(h.pack()), 64, "C4 pack 出 64 字节")
eq(data_offset(0), 64, "C5 payload 起点 = 头尾")
eq(data_offset(0x1000), 0x1000 + 64, "C6 payload 起点随镜像基址平移")
eq(image_size(p), len(payload) + 64, "C7 image_size = size + 64")
eq(struct.unpack(">I", blob[8:12])[0], 1700000000, "C8 ih_time 在偏移 8")
eq(blob[28], IH_OS["linux"], "C9 ih_os 在偏移 28")
eq(blob[29], IH_ARCH["arm"], "C10 ih_arch 在偏移 29")
eq(blob[30], IH_TYPE["kernel"], "C11 ih_type 在偏移 30")
eq(blob[31], IH_COMP["gzip"], "C12 ih_comp 在偏移 31")

# ---------------------------------------------------------------- D. 多文件镜像
parts = [b"A" * 100, b"B" * 200, b"C" * 37]
mp = build_multi(parts)
eq(multi_sizes(mp), [100, 200, 37], "D1 sizes 表")
eq(len(mp), (len(parts) + 1) * 4 + 337, "D2 表项 + 结尾 0 + 数据")
eq(multi_part_offsets(parts), [64 + 16, 64 + 16 + 100, 64 + 16 + 300], "D3 子镜像偏移")
for i, off in enumerate(multi_part_offsets(parts)):
    got = mp[(len(parts) + 1) * 4 + sum(len(x) for x in parts[:i]):][:len(parts[i])]
    eq(got, parts[i], "D4.%d 第 %d 个子镜像按偏移取回" % (i, i))
mh = LegacyHeader(time=1, os=IH_OS["linux"], arch=IH_ARCH["arm"],
                  type=IH_TYPE["multi"], comp=IH_COMP["none"], name=b"multi")
mh.seal(mp)
eq(parse_legacy(mh.pack() + mp).type, IH_TYPE["multi"], "D5 multi 类型")

# ---------------------------------------------------------------- E. FDT
root = make_sample_fit()
fblob = build_fdt(root)
rt, toks, strings, hdr = parse_fdt(fblob)
eq(hdr["magic"], FDT_MAGIC, "E1 FDT magic")
eq(hdr["version"], 17, "E2 version 17")
eq(len(fblob), hdr["totalsize"], "E3 totalsize 自洽")
eq(toks[0].kind, FDT_BEGIN_NODE, "E4 结构块以 BEGIN_NODE 开始")
eq(toks[-1].kind, FDT_END, "E5 结构块以 FDT_END 结束")
eq(rt.child("images", "kernel-1").props["type"].rstrip(b"\0"), b"kernel", "E6 属性回读")
eq(rt.child("configurations", "conf-1").props["kernel"].rstrip(b"\0"),
   b"kernel-1", "E7 配置引用回读")
ck(toks[0].start < toks[1].start, "E8 token 偏移单调")
ck(all((t.end - t.start) % 4 == 0 or t.kind == FDT_END for t in toks),
   "E9 token 边界四字节对齐")

# ---------------------------------------------------------------- F. FIT schema
errs = validate(rt)
eq(errs, [], "F1 样例 FIT 合规")

r2 = make_sample_fit()
del r2.props["timestamp"]
ck(any("timestamp" in e for e in validate(r2)), "F2 去掉 timestamp 报错")

r3 = make_sample_fit()
del r3.props["#address-cells"]
ck(any("#address-cells" in e for e in validate(r3)), "F3 有 load/entry 时 #address-cells 必选")
r4 = make_sample_fit()
del r4.props["#address-cells"]
del r4.child("images", "kernel-1").props["load"]
del r4.child("images", "kernel-1").props["entry"]
eq(validate(r4), [], "F4 不用 load/entry 时 #address-cells 可省（对偶）")

r5 = make_sample_fit()
r5.child("images", "kernel-1", "hash-1").props["value"] = b"\x00" * 16
ck(any("must be 32 bytes" in e for e in validate(r5)), "F5 sha256 value 必须 32 字节")
r5.child("images", "kernel-1", "hash-1").props["value"] = b"\x00" * 32
eq(validate(r5), [], "F6 改成 32 字节即合规")

r6 = make_sample_fit()
del r6.children["images"]
ck(any("'/images'" in e for e in validate(r6)), "F7 缺 /images 报错")
r7 = make_sample_fit()
del r7.children["configurations"]
ck(any("'/configurations'" in e for e in validate(r7)), "F8 缺 /configurations 报错")

eq(HASH_ALGO_SIZE["crc32"], 4, "F9 crc32 4 字节")
eq(HASH_ALGO_SIZE["md5"], 16, "F10 md5 16 字节")
eq(HASH_ALGO_SIZE["sha1"], 20, "F11 sha1 20 字节")
eq(HASH_ALGO_SIZE["sha512"], 64, "F12 sha512 64 字节")
eq(HASH_ALGO_SIZE["crc16-ccitt"], 2, "F13 crc16-ccitt 2 字节")
eq(SIG_ALGO_SIZE["rsa2048"], 256, "F14 rsa2048 签名 256 字节")
eq(SIG_ALGO_SIZE["ecdsa256"], 64, "F15 ecdsa256 签名 64 字节")

r8 = make_sample_fit()
r8.child("configurations", "conf-1", "signature-1").props["algo"] = b"sha512,rsa2048\0"
eq(validate(r8), [], "F16 哈希 sha512 与签名 rsa2048 可混搭，长度只跟签名算法走")

# ---------------------------------------------------------------- G. 引用与 node list
imgs = rt.children["images"]
cfg = rt.children["configurations"].children["conf-1"]
eq(sorted(referenced_images(cfg, imgs)), ["fdt-1", "kernel-1"], "G1 镜像引用")
nl = node_list("conf-1", imgs, rt)
eq(nl, {"/", "/configurations/conf-1", "/images/kernel-1",
        "/images/kernel-1/hash-1", "/images/fdt-1", "/images/fdt-1/hash-1"},
   "G2 node list = 根+配置+镜像+镜像的 hash 子结点")

cfg2 = dict(cfg.props)
cfg.props["description"] = b"kernel-1\0"
eq(sorted(referenced_images(cfg, imgs)), ["fdt-1", "kernel-1"],
   "G3 description 不是镜像引用（对偶：kernel 属性才是）")
cfg.props.clear()
cfg.props.update(cfg2)

cfg.props["kernel"] = b"nonexistent\0"
eq(referenced_images(cfg, imgs), ["fdt-1"], "G4 引用不到的镜像被跳过且不报错")
cfg.props["kernel"] = b"kernel-1\0"

# ---------------------------------------------------------------- H. §7.3 选区
# 关键前提：FDT 的 strings 块按"首次出现"顺序累积，任何新属性名都会平移后续
# 属性的 nameoff，从而改变结构块字节。所以所有对照组必须**属性名集合相同**，
# 只改属性值，否则测到的是 FDT 编码位移而不是 §7.3 的选区规则。
def mk():
    t = make_sample_fit()
    k = t.child("images", "kernel-1")
    k.props["data-size"] = (64).to_bytes(4, "big")
    k.props["data-position"] = (0).to_bytes(4, "big")
    return t


d0 = hashed_struct(build_fdt(mk()), nl)

r9 = mk()
r9.child("images", "kernel-1").props["data"] = b"\x01" * 64
eq(hashed_struct(build_fdt(r9), nl), d0, "H1 改镜像 data 不改变签名哈希（data 被排除）")

r10 = mk()
r10.child("images", "kernel-1", "hash-1").props["value"] = b"\x02" * 32
ck(hashed_struct(build_fdt(r10), nl) != d0, "H2 改镜像 hash 的 value 会改变（对偶）")

r11 = mk()
r11.child("configurations", "conf-1", "signature-1").props["value"] = b"\x03" * 256
eq(hashed_struct(build_fdt(r11), nl), d0,
   "H3 改签名自身的 value 不改变（签名结点不在 node list 里）")

r12 = mk()
r12.child("configurations", "conf-1").props["description"] = b"changed\0"
ck(hashed_struct(build_fdt(r12), nl) != d0,
   "H4 改配置的 description 会改变（它不在四个排除属性里）")

r13 = mk()
r13.child("images", "kernel-1").props["data-size"] = (65).to_bytes(4, "big")
eq(hashed_struct(build_fdt(r13), nl), d0, "H5 data-size 的值被排除")

r13b = mk()
r13b.child("images", "kernel-1").props["data-position"] = (9).to_bytes(4, "big")
eq(hashed_struct(build_fdt(r13b), nl), d0, "H5b data-position 的值被排除")

r14 = mk()
r14.child("images", "kernel-1").props["load"] = (0x60001000).to_bytes(4, "big")
ck(hashed_struct(build_fdt(r14), nl) != d0, "H6 load 不排除（对偶）")

_rt2, t2, s2, _h2 = parse_fdt(build_fdt(mk()))
eq(len([t for t in t2 if t.kind == FDT_END]), 1, "H7 只有一个 FDT_END")
ck(hashed_region(build_fdt(mk()), nl).endswith(s2), "H8 结构块之后拼上 strings 块区间")
ck(len(hashed_region(build_fdt(mk()), nl)) == len(d0) + len(s2), "H8b 两段落相接")

r16 = mk()
from fdt import Node as _Node
r16.child("images").children["unrelated"] = _Node()
r16.child("images", "unrelated").props.update({
    "description": b"x\0", "type": b"kernel\0", "arch": b"arm\0",
    "os": b"linux\0", "compression": b"none\0", "data": b"\x00" * 8})
eq(hashed_struct(build_fdt(r16), nl), d0, "H9 未被引用的镜像不进哈希")

# FDT_BEGIN_NODE 的"或父结点在列表里"规则：/images 自己不在列表里，但因为它
# 的父结点 "/" 在，所以它的 BEGIN/END token 仍被计入。
ck(b"\x01images\x00" in d0, "H10 /images 的 BEGIN_NODE 因父结点被计入")

# ---------------------------------------------------------------- I. 镜像哈希（§7.3.3）
kerndata = rt.child("images", "kernel-1").props["data"]
eq(image_hash("sha256", kerndata), hashlib.sha256(kerndata).digest(), "I1 sha256 覆盖 data")
eq(rt.child("images", "kernel-1", "hash-1").props["value"],
   image_hash("sha256", kerndata), "I2 样例里的 value 与之一致")
ck(image_hash("sha1", kerndata) != image_hash("sha1", kerndata + b"\x00"),
   "I3 尾部追加一个字节会改变镜像哈希")
ck(len(image_hash("sha256", b"")) == 32, "I4 sha256 空输入仍是 32 字节")
eq(len(image_hash("sha1", kerndata)), 20, "I5 sha1 长度 20")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1 if FAIL else 0)
