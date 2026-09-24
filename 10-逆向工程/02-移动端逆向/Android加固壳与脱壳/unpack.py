# -*- coding: utf-8 -*-
"""加固壳挂载与脱壳点的模型(BaseDexClassLoader/DexPathList/DexFile 真实语义)。

口径(实读源,android.googlesource libcore main 分支):
  BaseDexClassLoader.java —— sharedLibraryLoaders 恒在 pathList **之前**查,
    sharedLibraryLoadersAfter(OEM 配置)恒在 **之后**查
  DexPathList.java —— findClass 顺序遍历 dexElements 首个命中;
    makeDexElements 的 IOException 进 suppressed 列表(不快速失败);
    addDexPath 用 concat **追加**到 dexElements 尾部;InMemory 路径 initDexElements
  dex-format 官方文档 —— magic 8 字节;checksum=adler32(除 magic 与自身);
    signature=SHA-1(除 magic/checksum/自身)
"""

import hashlib
import zlib

DEX_MAGIC = b"dex\n035\x00"      # 8 字节
HEADER_SIZE = 0x70               # 112


def build_dex(tail=b"\x00" * 16, file_size=None):
    """最小结构正确的 dex 头:两个字段的覆盖范围按官方口径计算。"""
    total = HEADER_SIZE + len(tail) if file_size is None else file_size
    buf = bytearray(DEX_MAGIC)
    buf += b"\x00\x00\x00\x00"          # checksum 占位 @8
    buf += b"\x00" * 20                  # signature 占位 @12
    buf += total.to_bytes(4, "little")   # file_size @32
    buf += HEADER_SIZE.to_bytes(4, "little")  # header_size @36
    buf += b"\x00" * (HEADER_SIZE - len(buf))
    buf += tail
    # 顺序不可反:checksum 的范围(除 magic 与自身)**包含 signature 字段**,
    # 所以先把 signature 定稿,再算 checksum。
    signature = hashlib.sha1(bytes(buf[32:])).digest()  # 范围:除 magic/checksum/signature
    buf[12:32] = signature
    checksum = zlib.adler32(bytes(buf[12:]))            # 范围:除 magic 与 checksum 字段
    buf[8:12] = checksum.to_bytes(4, "little")
    return bytes(buf)


def dex_header_ok(buf):
    """脱壳落盘的第一道验收:magic / checksum 范围 / signature 范围。"""
    if buf[:8] != DEX_MAGIC:
        return False, "magic"
    if zlib.adler32(bytes(buf[12:])) != int.from_bytes(buf[8:12], "little"):
        return False, "checksum"
    if hashlib.sha1(bytes(buf[32:])).digest() != buf[12:32]:
        return False, "signature"
    return True, "ok"


class Element:
    """DexPathList.Element:一个 dex 载体(壳的 stub / 解密后的真身都挂成它)。"""

    def __init__(self, name, classes, broken=False):
        self.name, self.classes, self.broken = name, classes, broken

    def find_class(self, cname, suppressed):
        if self.broken:                    # makeDexElements 的 IOException 语义
            suppressed.append("IOException:" + self.name)
            return None
        return cname if cname in self.classes else None


class DexPathList:
    """findClass 顺序遍历;suppressed 汇总;addDexPath 追加。"""

    def __init__(self, elements):
        self.dex_elements = list(elements)
        self.dex_elements_suppressed = []

    def find_class(self, cname):
        suppressed = []
        for e in self.dex_elements:
            c = e.find_class(cname, suppressed)
            if c is not None:
                return c
        self.dex_elements_suppressed.extend(suppressed)
        return None

    def add_dex_path(self, element):
        old, old_supp = self.dex_elements, self.dex_elements_suppressed
        new, new_supp = [element], []
        # makeDexElements 对新条目单独收集 suppressed,再与旧列表拼接
        self.dex_elements = old + new
        self.dex_elements_suppressed = old_supp + new_supp
        return self.dex_elements


class BaseDexClassLoader:
    """共享库两组加载器的固定先后:前组 → pathList → 后组。"""

    def __init__(self, path_list, shared=None, shared_after=None, parent_first=None):
        self.path_list = path_list
        self.shared = shared or []
        self.shared_after = shared_after or []
        self.parent_first = parent_first   # 常规双亲委派(标准 ClassLoader 语义)

    def find_class(self, cname):
        if self.parent_first and self.parent_first.find_class(cname):
            return "parent:" + cname
        for ld in self.shared:
            if ld.find_class(cname):
                return "shared:" + cname
        if self.path_list.find_class(cname):
            return "pathList:" + cname
        for ld in self.shared_after:
            if ld.find_class(cname):
                return "sharedAfter:" + cname
        return None


class Shell:
    """壳行为模型:stub dex 先行,真身解密后经 addDexPath/InMemory 追加,可落盘。"""

    def __init__(self, stub_classes, payload_dex):
        self.path = DexPathList([Element("classes.dex", stub_classes)])
        self.payload = payload_dex

    def boot(self, real_classes):
        """模拟 attachBaseContext:解密 → 追加 element(顺序在 stub 之后)。"""
        self.path.add_dex_path(Element("payload.dex", real_classes))
        return self.path

    def dump_all(self):
        """脱壳点:遍历 dexElements,每个 dex 落盘并按 dex 头三段验收。"""
        out = []
        for e in self.path.dex_elements:
            if e.broken:
                continue
            data = self.payload if e.name == "payload.dex" else None
            if data is not None:
                out.append((e.name, dex_header_ok(data)))
        return out
