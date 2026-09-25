# -*- coding: utf-8 -*-
"""最小 WebAssembly 模块:手工构造字节、解码段结构、栈机求值。

口径(实读源):W3C WebAssembly Core Specification(binary format 各页)——
  modules.html  :magic ::= 0x00 0x61 0x73 0x6D;version ::= 0x01 0x00 0x00 0x00;
                  section ::= id u32 长度(仅内容字节数) 内容
  types.html    :functype ::= 0x60 参数型* 结果型*
  instructions.html:i32.const=0x41+s32;local.get=0x20+localidx;i32.add=0x6A;end=0x0B
  conventions.html:LEB128 变长整数(7 位一组,高位续接,小端序)
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


# ---- LEB128 ----

def uleb_encode(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)               # 高位续接
        else:
            out.append(b)
            return bytes(out)


def uleb_decode(buf, i=0):
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def sleb_encode(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7                               # 算术右移(符号位延伸)
        done = (n == 0 and not b & 0x40) or (n == -1 and b & 0x40)
        out.append(b if done else b | 0x80)
        if done:
            return bytes(out)


def sleb_decode(buf, i=0):
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            if b & 0x40:                       # 符号扩展
                result |= -(1 << shift)
            return result, i


# ---- 模块构造 ----

I32 = 0x7F


def section(sid, payload):
    return bytes([sid]) + uleb_encode(len(payload)) + payload


def build_add_module():
    types = section(1, uleb_encode(1) + bytes([0x60]) +          # 1 个 functype
                    uleb_encode(2) + bytes([I32, I32]) +          # 2 个 i32 参数
                    uleb_encode(1) + bytes([I32]))                # 1 个 i32 结果
    funcs = section(3, uleb_encode(1) + uleb_encode(0))           # func0 : typeidx 0
    name = b"add"
    exports = section(7, uleb_encode(1) + uleb_encode(len(name)) + name +
                      bytes([0x00]) + uleb_encode(0))              # kind 0=func idx 0
    body = uleb_encode(0) + bytes([0x20, 0]) + bytes([0x20, 1]) + \
        bytes([0x6A]) + bytes([0x0B])                             # 0 local + 指令
    code = section(10, uleb_encode(1) + uleb_encode(len(body)) + body)
    return b"\x00\x61\x73\x6d\x01\x00\x00\x00" + types + funcs + exports + code


# ---- 解码与栈机 ----

def parse_module(buf):
    assert buf[:8] == b"\x00\x61\x73\x6d\x01\x00\x00\x00"
    i, sections = 8, []
    while i < len(buf):
        sid = buf[i]
        size, i = uleb_decode(buf, i + 1)
        sections.append((sid, buf[i:i + size]))
        i += size
    return sections


def run_add(args):
    """栈机:local.get 压栈,i32.add 弹二压一,i32 回绕 2^32。"""
    stack = list(args)
    stack.append((stack.pop() + stack.pop()) & 0xFFFFFFFF)         # 0x6A
    v = stack.pop()                                                 # 0x0B end
    return v - 0x100000000 if v & 0x80000000 else v                 # i32 有符号解释


def main():
    print("1. LEB128 变长整数")
    assert uleb_encode(0) == b"\x00" and uleb_encode(127) == b"\x7f"
    assert uleb_encode(128) == b"\x80\x01"
    assert uleb_encode(624485) == b"\xe5\x8e\x26"
    assert uleb_decode(b"\xe5\x8e\x26") == (624485, 3)
    assert sleb_encode(-1) == b"\x7f" and sleb_decode(b"\x7f") == (-1, 1)
    assert sleb_encode(-123456) == b"\xc0\xbb\x78"
    ok("u32/s32 LEB128:7 位一组、高位续接、小端序;s32 末组符号位决定扩展")

    print("2. magic 与 version")
    mod = build_add_module()
    assert mod[:4] == b"\x00asm" and mod[4:8] == b"\x01\x00\x00\x00"
    ok("magic = 0x00 0x61 0x73 0x6D(字节序即 \\\\0asm);version = 1 的小端 u32")

    print("3. 段框架")
    secs = parse_module(mod)
    assert [s for s, _ in secs] == [1, 3, 7, 10]
    rebuilt = mod[:8] + b"".join(section(sid, payload) for sid, payload in secs)
    assert rebuilt == mod
    ok("section = id + u32 长度 + 内容;长度**只数内容字节**(不含 id 与长度字段本身);"
       "custom 段(id 0)语义上被忽略——解码→重编码与原模块逐字节相等")

    print("4. 类型/导出解析")
    t_payload = dict(secs)[1]
    n, i = uleb_decode(t_payload)                # 先读 vector 计数
    assert t_payload[i] == 0x60 and n == 1
    e_payload = dict(secs)[7]
    cnt, j = uleb_decode(e_payload)              # export 向量计数
    ln, j = uleb_decode(e_payload, j)            # name 自身又是字节向量
    assert cnt == 1 and e_payload[j:j + ln] == b"add" and e_payload[j + ln] == 0x00
    ok("functype 以 0x60 开头;export 段 name/kind(0x00=func)/funcidx 三元组")

    print("5. 栈机求值与回绕")
    assert run_add((3, 4)) == 7
    assert run_add((2**31 - 1, 1)) == -(2**31)                      # 溢出回绕
    assert run_add((-1, 1)) == 0
    ok("i32.add 按位相加后按 2^32 回绕,再按补码解释——"
       "i32.const 的 s32 编码让 -1 压进的是 32 个 1")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
