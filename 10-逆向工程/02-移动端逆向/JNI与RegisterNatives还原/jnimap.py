# -*- coding: utf-8 -*-
"""JNI 符号解析与 RegisterNatives 动态注册的模型。

口径(实读源):docs.oracle.com JNI 规范 design.html「Resolving Native Method Names」
与 functions.html「RegisterNatives/UnregisterNatives」。

命名拼接:Java_ + 转义(FQCN 中 / 换 _) + _ + 转义方法名 [+ 重载: __ + 转义**参数**签名]
转义表:_0XXXX(Unicode,小写 hex)/ _1='_') / _2=';'(签名) / _3='['(签名)
"""

REGISTER_NATIVES_INDEX = 215   # functions.html:LINKAGE Index 215 in the JNIEnv table

import struct


def mangle_ident(s):
    """类名/方法名转义:仅 _ → _1 与非 ASCII → _0xxxx(小写)。"""
    out = []
    for ch in s:
        if ch == "_":
            out.append("_1")
        elif ord(ch) > 0x7f:
            out.append("_0%04x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def mangle_args(descriptor):
    """方法描述符取**参数部分**(去括号去返回值),按签名转义表处理 ; 与 [。"""
    args = descriptor[1:descriptor.rindex(")")]
    out = []
    for ch in args:
        if ch == "_":
            out.append("_1")
        elif ch == ";":
            out.append("_2")
        elif ch == "[":
            out.append("_3")
        elif ch == "/":
            out.append("_")          # Ljava/lang/String; 里的斜杠同 FQCN 一样换下划线
        elif ord(ch) > 0x7f:
            out.append("_0%04x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def short_name(fqcn, method):
    """斜杠是分隔符:各段先转义(段内 '_' → _1),再用 '_' 连接。"""
    cls = "_".join(mangle_ident(p) for p in fqcn.split("/"))
    return "Java_" + cls + "_" + mangle_ident(method)


def long_name(fqcn, method, descriptor):
    return short_name(fqcn, method) + "__" + mangle_args(descriptor)


def resolve(exported, fqcn, method, descriptor, native_overload_siblings):
    """镜像 VM 的解析序:先短名后长名。
    native_overload_siblings:同名方法里**同为 native**的其他重载(Java 方法不算)。"""
    if short_name(fqcn, method) in exported:
        return ("short", short_name(fqcn, method))
    if native_overload_siblings:
        ln = long_name(fqcn, method, descriptor)
        if ln in exported:
            return ("long", ln)
    return (None, None)


# ---- RegisterNatives 侧:假 .so 里的 JNINativeMethod 数组 ----


class SoImage:
    """假共享库字节镜像:cstr 表 + {name,sig,fnPtr} 结构数组。"""

    def __init__(self):
        self.buf = bytearray()

    def cstr(self, s):
        off = len(self.buf)
        self.buf += s.encode() + b"\x00"
        return off

    def method_table(self, entries):
        """entries: [(name, sig, fnPtr)] → 返回表偏移与字节数。
        先把字符串全部落好,再连续打包三元组,保证表内存连续。"""
        refs = [(self.cstr(name), self.cstr(sig), fn) for name, sig, fn in entries]
        while len(self.buf) % 8:
            self.buf += b"\x00"
        off = len(self.buf)
        for n_off, s_off, fn in refs:
            self.buf += struct.pack("<QQQ", n_off, s_off, fn)
        return off, len(entries) * 24

    def read_table(self, off, n):
        out = []
        for i in range(n):
            n_, s_, f_ = struct.unpack_from("<QQQ", self.buf, off + i * 24)
            out.append((self.str_at(n_), self.str_at(s_), f_))
        return out

    def str_at(self, off):
        end = self.buf.index(b"\x00", off)
        return self.buf[off:end].decode()


def register_natives(so, table_off, n, cls_methods):
    """cls_methods: {name: "static"|"instance"|None(非 native)}。
    返回 (retcode, bound, errors)。口径:成功 0,失败负值;
    方法不存在或**不是 native** → NoSuchMethodError。"""
    bound, errors = [], []
    for name, sig, fn in so.read_table(table_off, n):
        kind = cls_methods.get(name)
        if kind not in ("static", "instance"):
            errors.append(("NoSuchMethodError", name))   # 找不到,或不是 native
            continue
        bound.append({"name": name, "sig": sig, "fnPtr": fn,
                      "second_arg": "jclass" if kind == "static" else "jobject"})
    return (0 if not errors else -1), bound, errors


def unregister(bound):
    """UnregisterNatives:回到链接/注册前——符号名解析重新生效。"""
    bound.clear()
    return True
