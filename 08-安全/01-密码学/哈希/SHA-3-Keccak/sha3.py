# -*- coding: utf-8 -*-
"""SHA-3 / Keccak-f[1600] 海绵函数实现(FIPS 202)。

依据:
  - Keccak 官方团队规格摘要 https://keccak.team/keccak_specs_summary.html
    (Keccak-f 五步轮函数 theta/rho/pi/chi/iota 伪代码、旋转偏移表、
     各标准实例的 rate/capacity 与域分离后缀)
  - RC 常量表按官方 CompactFIPS202 的 LFSR 生成式在文件内重推导并断言校验
    (https://github.com/XKCP/XKCP ... CompactFIPS202/Python)
  - NIST 官方示例值(SHA3-256("")=a7ffc6f8... 等,见 README)

单文件自测 5 组:
  1) 空串与 "abc" 的 SHA3-224/256/384/512 官方向量 + hashlib 对照
  2) SHAKE128/SHAKE256 空串输出
  3) 跨吸收块的长输入(200/1000 字节)与 hashlib 对照
  4) 雪崩效应: 相邻输入哈希差异位数接近 256/2
  5) padding 细节: 末块剩 1 字节时 0x06|0x80 合并为 0x86; 与 hashlib 对照
"""
import hashlib

# 轮常数: 按官方 CompactFIPS202 的 LFSR 生成式推导(R 跨轮持续演化)
def _gen_rc():
    R, out = 1, []
    for _ in range(24):
        val = 0
        for j in range(7):
            R = ((R << 1) ^ ((R >> 7) * 0x71)) % 256
            if R & 2:
                val ^= 1 << ((1 << j) - 1)
        out.append(val)
    return out


RC = _gen_rc()
# 旋转偏移表 ROT[x][y] 与 keccak.team Table 2 一致
ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
M64 = (1 << 64) - 1
# 已知官方向量抽检, 防抄录错误
assert RC[0] == 1 and RC[1] == 0x8082 and RC[11] == 0x8000000A and RC[23] == 0x8000000080008008


def rotl(v, n):
    n %= 64
    return ((v << n) | (v >> (64 - n))) & M64 if n else v


def keccak_f1600(a):
    """a: 长度 25 的 lane 列表, lane A[x][y] 存于 a[x + 5*y] (LSB-first)。
    24 轮, 每轮 theta / rho+pi / chi / iota。"""
    for rnd in RC:
        # theta: 列奇偶校验 + 旋转扩散
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] ^= d[x]
        # rho+pi: B[y, 2x+3y] = rot(A[x,y], ROT[x][y])
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = rotl(a[x + 5 * y], ROT[x][y])
        # chi: 非线性层 (唯一的非线性运算)
        for y in range(5):
            for x in range(5):
                a[x + 5 * y] = b[x + 5 * y] ^ (
                    (~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y] & M64)
        # iota: 破坏对称性
        a[0] ^= rnd
    return a


def sponge(data: bytes, rate: int, suffix: int, out_len: int) -> bytes:
    """pad10*1 填充 + 域分离后缀(与 XKCP CompactFIPS202 结构一致)。

    填充规则: suffix 字节放在消息末尾后第一字节, 0x80 恒放块尾;
    两者同位时(末块恰剩 1 字节)合并, 如 0x06|0x80 = 0x86。"""
    state = bytearray(200)
    off, n = 0, len(data)
    last = 0  # 末块已吸收字节数(吸收满块即置换并归零)
    while off < n:
        take = min(n - off, rate)
        for i in range(take):
            state[i] ^= data[off + i]
        off += take
        last = take
        if take == rate:
            _permute(state)
            last = 0
    # padding
    state[last] ^= suffix
    state[rate - 1] ^= 0x80
    _permute(state)
    # squeeze
    out = bytearray()
    while len(out) < out_len:
        out += state[:min(rate, out_len - len(out))]
        if len(out) < out_len:
            _permute(state)
    return bytes(out)


def _permute(state):
    lanes = keccak_f1600([int.from_bytes(state[8 * k:8 * k + 8], "little")
                          for k in range(25)])
    for k in range(25):
        state[8 * k:8 * k + 8] = lanes[k].to_bytes(8, "little")


def sha3(data: bytes, bits: int) -> bytes:
    rate = {224: 144, 256: 136, 384: 104, 512: 72}[bits]  # rate = (1600-2*bits)/8
    return sponge(data, rate, 0x06, bits // 8)  # 域分离后缀 01


def shake(data: bytes, out_len: int, security: int) -> bytes:
    rate = {128: 168, 256: 136}[security]
    return sponge(data, rate, 0x1F, out_len)  # 后缀 1111


def demo():
    # --- 1) NIST 官方向量: 空串 / "abc" ---
    expect = {
        (224, b""): "6b4e03423667dbb73b6e15454f0eb1abd4597f9a1b078e3f5b5a6bc7",
        (256, b""): "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
        (384, b""): "0c63a75b845e4f7d01107d852e4c2485c51a50aaaa94fc61995e71bbee983a2a"
                    "c3713831264adb47fb6bd1e058d5f004",
        (512, b""): "a69f73cca23a9ac5c8b567dc185a756e97c982164fe25859e0d1dcc1475c80a6"
                    "15b2123af1f5f94c11e3e9402c3ac558f500199d95b6d3e301758586281dcd26",
        (224, b"abc"): "e642824c3f8cf24ad09234ee7d3c766fc9a3a5168d0c94ad73b46fdf",
        (256, b"abc"): "3a985da74fe225b2045c172d6bd390bd855f086e3e9d525b46bfe24511431532",
        (512, b"abc"): "b751850b1a57168a5693cd924b6b096e08f621827444f70d884f5d0240d2712e"
                        "10e116e9192af3c91a7ec57647e3934057340b4cf408d5a56592f8274eec53f0",
    }
    for (bits, msg), hexv in expect.items():
        got = sha3(msg, bits).hex()
        assert got == hexv, f"SHA3-{bits}({msg!r}) mismatch"
        assert got == hashlib.new(f"sha3_{bits}", msg).hexdigest()
    print("demo1 官方向量 + hashlib 对照: PASS (%d 组)" % len(expect))

    # --- 2) SHAKE128/SHAKE256 空串 ---
    assert shake(b"", 32, 128).hex() == \
        "7f9c2ba4e88f827d616045507605853ed73b8093f6efbc88eb1a6eacfa66ef26"
    assert shake(b"", 64, 256).hex() == \
        "46b9dd2b0ba88d13233b3feb743eeb243fcd52ea62b81b82b50c27646ed5762f" \
        "d75dc4ddd8c0f200cb05019d67b592f6fc821c49479ab48640292eacb3b7c4be"
    assert shake(b"", 32, 128) == hashlib.shake_128(b"").digest(32)
    assert shake(b"", 100, 256) == hashlib.shake_256(b"").digest(100)
    print("demo2 SHAKE128/256 官方向量 + hashlib 对照: PASS")

    # --- 3) 跨吸收块长输入 ---
    for n in (135, 136, 137, 168, 200, 1000):
        data = b"a" * n
        assert sha3(data, 256) == hashlib.sha3_256(data).digest(), f"len={n}"
        assert sha3(data, 512) == hashlib.sha3_512(data).digest(), f"len={n}"
    print("demo3 跨块长输入(135..1000 字节) hashlib 对照: PASS")

    # --- 4) 雪崩效应 ---
    h1 = int.from_bytes(sha3(b"the quick brown fox", 256), "big")
    h2 = int.from_bytes(sha3(b"the quick brown fox!", 256), "big")
    diff = bin(h1 ^ h2).count("1")
    assert 100 < diff < 156, f"avalanche diff bits = {diff}"
    print(f"demo4 雪崩效应: 1 字节输入变化 -> {diff}/256 位翻转 (期望≈128): PASS")

    # --- 5) padding 边界: 末块剩 1 字节时 0x86 合并 ---
    for n in (135, 136, 271, 272, 407):
        d = bytes((i * 7) % 256 for i in range(n))
        assert sha3(d, 256) == hashlib.sha3_256(d).digest()
        assert shake(d, 48, 128) == hashlib.shake_128(d).digest(48)
    # 直接构造: 消息长度 = rate-1 = 135, 此时填充字节为 0x06^0x80 = 0x86
    d = b"z" * 135
    assert sha3(d, 256) == hashlib.sha3_256(d).digest()
    print("demo5 padding 块边界(0x86 合并/跨块) 对照: PASS")


if __name__ == "__main__":
    demo()
    print("ALL PASS")
