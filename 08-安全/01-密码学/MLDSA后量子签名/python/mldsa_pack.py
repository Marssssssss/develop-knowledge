"""ML-DSA 位打包：把系数压成定长位串。

对应 pq-crystals/dilithium `ref/packing.c`。每个多项式的位宽由参数集唯一决定，
`ref/params.h:50-70` 的 *_PACKEDBYTES 就是 `256 * bits / 8`：

| 对象        | 位宽                    | ML-DSA-44 | ML-DSA-65/87 |
| ----------- | ----------------------- | --------- | ------------ |
| t1          | 10                      | 320 B     | 320 B        |
| t0          | 13                      | 416 B     | 416 B        |
| s1/s2 (eta) | 3 (eta=2) / 4 (eta=4)  | 96 B      | 128 B        |
| z (gamma1)  | 18 (2^17) / 20 (2^19)  | 576 B     | 640 B        |
| w1          | 6 ((Q-1)/88) / 4 (/32) | 192 B     | 128 B        |
"""

from mldsa_params import N, Q

BYTES_OF_BITS = {10: 320, 13: 416, 3: 96, 4: 128, 18: 576, 20: 640, 6: 192}


def pack(coeffs, bits):
    """小端位流打包：value = sum(c[i] << (bits*i))。"""
    acc = 0
    for i, c in enumerate(coeffs):
        acc |= (c & ((1 << bits) - 1)) << (bits * i)
    nbytes = (N * bits + 7) // 8
    return acc.to_bytes(nbytes, "little")


def unpack(buf, bits):
    acc = int.from_bytes(buf, "little")
    mask = (1 << bits) - 1
    return [(acc >> (bits * i)) & mask for i in range(N)]


def polyt1_pack(a):
    """t1 在 [0, 2^9)，直接 10 位（packing.c 实为 10 位存储）。"""
    return pack(a, 10)


def polyt1_unpack(buf):
    return unpack(buf, 10)


def polyt0_pack(a):
    """t0 存成 2^(D-1) - a0 的 13 位形式，这里对已归正的 t0 直接打包。"""
    return pack(a, 13)


def polyt0_unpack(buf):
    return unpack(buf, 13)


def polyeta_pack(a, eta):
    """存 eta - c（eta=2 用 3 位、eta=4 用 4 位），使值非负。"""
    return pack([eta - c for c in a], 3 if eta == 2 else 4)


def polyeta_unpack(buf, eta):
    bits = 3 if eta == 2 else 4
    return [eta - c for c in unpack(buf, bits)]


def polyw1_pack(a, gamma2):
    """w1 = a1，只有 44/65 两种位宽。"""
    return pack(a, 6 if gamma2 == (Q - 1) // 88 else 4)


def pack_hint(hints, omega, k):
    """hint 位图（ref/packing.c pack_hint）。

    只记录「哪些位置是 1」的下标，末尾 k 个字节分别记录每行用掉了多少槽位。
    因此签名里 hint 段恒为 omega + k 字节，与实际 hint 个数无关。
    """
    out = bytearray(omega + k)
    idx = 0
    for i, row in enumerate(hints):
        for pos, h in enumerate(row):
            if h and idx < omega:
                out[idx] = pos
                idx += 1
        out[omega + i] = idx
    return bytes(out)
