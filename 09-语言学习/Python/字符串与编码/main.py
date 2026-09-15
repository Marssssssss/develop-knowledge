# -*- coding: utf-8 -*-
"""字符串柔性表示与编码(PEP 393):按 maxchar 动态选 1/2/4 字节 + 编解码语义。

运行: python3 main.py
依据: PEP 393 + 官方 HOWTO Unicode(见 README 参考资料)。
"""

import sys

PASS = []


def ok(name):
    PASS.append(name)
    print(f"[PASS] {name}")


def bytes_per_char(s):
    """由 sys.getsizeof 的斜率推出每字符字节数(PEP 393 的 kind)。
    用差分避开不同 kind 结构头大小不一的干扰(ASCII 头 41B,latin-1 头更大)。"""
    n = max(len(s), 1)
    return (sys.getsizeof(s * 2) - sys.getsizeof(s)) / n


def demo_kinds():
    """kind 由字符串内最大码点决定:ASCII→1B,latin-1→1B,BMP→2B,非 BMP→4B。"""
    a = "a" * 1000
    lat = "é" * 1000
    bmp = "中" * 1000
    astral = "😀" * 1000
    assert bytes_per_char(a) == 1.0
    assert bytes_per_char(lat) == 1.0            # latin-1 档也是 1 字节
    assert bytes_per_char(bmp) == 2.0
    assert bytes_per_char(astral) == 4.0
    # 单个非 BMP 字符 → 整串升到 4 字节档
    mixed = "a" * 999 + "😀"
    assert bytes_per_char(mixed) == 4.0
    ok("kind 分档:ASCII/latin-1=1B,BMP=2B,含非 BMP 字符=4B;一个字符拉满整串")


def demo_compact_layout():
    """compact 布局:数据内联,固定开销不随长度增长(对比 latin-1 需更大的结构头)。"""
    a1, a2 = "a" * 1, "a" * 1000
    # 每 ASCII 字符恰好 +1 字节 → 说明数据是内联的、无独立数据指针
    assert sys.getsizeof(a2) - sys.getsizeof(a1) == 999
    # latin-1 结构头比 ASCII 大(PyCompactUnicodeObject 多出 utf8/wstr_length 字段)
    size_lat_empty_head = sys.getsizeof("é") - 1
    size_ascii_head = sys.getsizeof("a")
    assert size_lat_empty_head > size_ascii_head, (size_lat_empty_head, size_ascii_head)
    ok(f"compact 内联:ASCII 每 +1 字符恰好 +1B;latin-1 结构头({size_lat_empty_head}B)>ASCII({size_ascii_head}B)")


def demo_kind_promotion():
    """拼接按结果最大码点重算 kind;不会降档。"""
    s = "abc"
    assert bytes_per_char(s + "é") == 1.0        # ASCII + latin1 → latin1
    assert bytes_per_char(s + "中") == 2.0       # ASCII + BMP → 2B
    assert bytes_per_char(s + "😀") == 4.0
    t = "中" + "😀"
    assert bytes_per_char(t) == 4.0
    # 与空串拼接不变档(仍可能触发重新分配对象,但 kind 不升)
    assert bytes_per_char("中" + "") == 2.0
    ok("kind 提升:拼接按新 maxchar 重选;空串拼接不变档")


def demo_utf8_encoding():
    """UTF-8 变长编码:1/2/3 字节 + 4 字节(非 BMP,经代理对转义)。"""
    assert "a".encode("utf-8") == b"a"           # 1B
    assert "é".encode("utf-8") == b"\xc3\xa9"    # 2B
    assert "中".encode("utf-8") == b"\xe4\xb8\xad"  # 3B
    assert "😀".encode("utf-8") == b"\xf0\x9f\x98\x80"  # 4B
    assert len("😀".encode()) == 4
    # 不可编码:latin-1 装不下 BMP 字符
    try:
        "中".encode("latin-1")
        raise AssertionError("latin-1 应无法编码中文字符")
    except UnicodeEncodeError as e:
        assert e.encoding == "latin-1"
    # UTF-8 不允许未配对的代理项
    lone = "\ud800"
    try:
        lone.encode("utf-8")
        raise AssertionError("孤立代理项应报 UnicodeEncodeError")
    except UnicodeEncodeError:
        pass
    assert lone.encode("utf-8", "surrogatepass") == b"\xed\xa0\x80"
    ok("UTF-8 变长 1/2/3/4B;latin-1 编不了中文;孤立代理 strict 报错/surrogatepass 放行")


def demo_decoding():
    """解码:非法字节序列报 UnicodeDecodeError,错误信息带位置与 reason。"""
    try:
        b"\xff\xfe\xfd".decode("ascii")
        raise AssertionError("非 ASCII 字节应解码失败")
    except UnicodeDecodeError as e:
        assert e.start == 0 and e.reason == "ordinal not in range(128)"
    try:
        b"\xc3\x28".decode("utf-8")              # 0xC3 后需接续字节,0x28 不是
        raise AssertionError("非法 UTF-8 序列应报错")
    except UnicodeDecodeError as e:
        # 3.13 实测:报告的是首字节位置 0(CPython 对"接续字节缺失"的报错习惯)
        assert e.start == 0 and "invalid continuation byte" in e.reason
    # errors 处理策略
    assert b"ab\xff".decode("utf-8", "replace") == "ab\ufffd"
    assert b"ab\xff".decode("utf-8", "ignore") == "ab"
    ok("解码:strict 报错带 start/reason;replace→U+FFFD;ignore 丢弃")


def demo_code_point_semantics():
    """比较按码点:无区域设置参与;'Z' < 'a','z' < 'é'。"""
    assert ord("Z") < ord("a") and "Z" < "a"
    assert "z" < "é"                             # 码点比较,不是字母表顺序
    assert chr(0x4e2d) == "中" and ord("中") == 0x4E2D
    # len 按码点计:一个 emoji 就是一个码点(内部分给的是 UTF-32 档)
    assert len("😀") == 1
    # 索引 O(1)(等宽存储的直接收益)
    s = "中" * 10000
    assert s[9999] == "中"
    ok("码点语义:比较/len/索引均按码点;区域无关")


def demo_intern():
    """sys.intern:标识符风格的字符串驻留;动态构造的非标识符不自动驻留。"""
    lit = "identifier"
    assert sys.intern("identifier") is lit       # intern 后与字面量同一对象
    dyn = "identi" + "fier"                       # 编译期常量折叠 → 同一对象
    assert dyn is lit
    # 非标识符字符串:真正运行期拼接的结果不会被自动驻留
    # (注意:"hello"+"!" 会在编译期被常量折叠,必须经由变量绕开折叠)
    def make(pfx, sfx):
        return pfx + sfx

    bang = make("hello", "!")
    bang2 = make("hello", "!")
    assert not (bang is bang2), "非标识符不应自动驻留"
    assert sys.intern(bang) is sys.intern(bang2)  # 手动 intern 后共享
    ok("驻留:标识符/折叠常量同一对象;非标识符动态串不自动驻留,intern 可共享")


def main():
    print(f"Python {sys.version.split()[0]}\n")
    demo_kinds()
    demo_compact_layout()
    demo_kind_promotion()
    demo_utf8_encoding()
    demo_decoding()
    demo_code_point_semantics()
    demo_intern()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
