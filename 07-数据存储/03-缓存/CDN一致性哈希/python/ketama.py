"""libketama（RJ/ketama）一致性哈希环的可执行转写。

对应源码：https://github.com/RJ/ketama/blob/master/libketama/ketama.c
  - ketama_md5_digest()  :194
  - ketama_hashi()       :335
  - ketama_get_server()  :348
  - ketama_create_continuum() 中的环构建部分 :421-465
  - ketama_compare()     :657

与 C 的差异（显式落地）：
  - C 用共享内存 + 定长 `mcs continuum[numservers*160]`，这里用 list。
  - C 的 `while (1)` 二分加了迭代上限保护；真实输入下远达不到，
    加保护只为避免脚本因为断言失败而挂死（见 README「注意事项」）。
"""

import hashlib
import math
import struct

MAX_SEARCH_STEPS = 4096


class Mcs:
    """continuum 上的一个点。C 里是 (point, ip[22])。"""

    __slots__ = ("point", "ip")

    def __init__(self, point, ip):
        self.point = point
        self.ip = ip

    def __eq__(self, other):
        return isinstance(other, Mcs) and (self.point, self.ip) == (other.point, other.ip)

    def __repr__(self):
        return f"Mcs({self.point}, {self.ip!r})"


def ketama_md5_digest(s):
    return hashlib.md5(s.encode("utf-8")).digest()


def ketama_hashi(s):
    """ketama.c:335 —— 取 md5 的前 4 字节，按小端拼成 unsigned int。"""
    d = ketama_md5_digest(s)
    return (d[3] << 24) | (d[2] << 16) | (d[1] << 8) | d[0]


def ketama_compare(a, b):
    """ketama.c:657 —— qsort 比较器，按 point 升序。"""
    if a.point < b.point:
        return -1
    if a.point > b.point:
        return 1
    return 0


def f32(x):
    """把 double 舍入到 IEEE-754 单精度。C 里 `float pct` 与 `floorf()` 都是单精度。"""
    return struct.unpack("f", struct.pack("f", x))[0]


def ketama_ks(mem, memory, numservers):
    """ketama.c:424 —— `ks = floorf(pct * 40.0 * (float)numservers)`，按 C 的单精度语义算。

    `pct` 是 float，乘积先按 double 求值，再被 floorf 收成 float —— 这最后一步的
    舍入很关键：等权 7 台机器时 double 直算得 39.99999999999999（ks=39），
    而 C 的单精度路径得 40.0（ks=40）。
    """
    pct = f32(f32(mem) / f32(memory))
    return int(math.floor(f32(pct * 40.0 * f32(numservers))))


def ketama_ks_double(mem, memory, numservers):
    """对照实现：全程 double。用来说明上面那个单精度细节不是可有可无的。"""
    return int(math.floor((float(mem) / float(memory)) * 40.0 * float(numservers)))


def ketama_create_continuum(servers):
    """ketama.c:421-465 —— servers 为 [(addr, memory), ...]。

    每个 server 的点数：`ks = floorf(memory/total * 40 * numservers)`，
    每个 `ks` 再做一次 md5、取 4 段 → 每 server 约 160 个点。
    """
    numservers = len(servers)
    if numservers < 1:
        return []
    memory = sum(m for _, m in servers)
    if memory == 0:
        return []

    continuum = []
    for addr, mem in servers:
        ks = ketama_ks(mem, memory, numservers)
        for k in range(ks):
            ss = f"{addr}-{k}"
            digest = ketama_md5_digest(ss)
            # 一次 md5 取 4 个 32 位数 = 4 个环上点
            for h in range(4):
                point = (
                    (digest[3 + h * 4] << 24)
                    | (digest[2 + h * 4] << 16)
                    | (digest[1 + h * 4] << 8)
                    | digest[h * 4]
                )
                continuum.append(Mcs(point, addr))

    continuum.sort(key=lambda m: m.point)
    return continuum


def ketama_get_server_h(h, continuum):
    """ketama.c:348 —— 已知 key 哈希 h 时的二分查找（便于断言注入确定值）。"""
    numpoints = len(continuum)
    if numpoints == 0:
        return None
    lowp = 0
    highp = numpoints
    for _ in range(MAX_SEARCH_STEPS):
        midp = (lowp + highp) // 2

        if midp == numpoints:
            return continuum[0]  # 走到末尾 → 回滚到第 0 个点

        midval = continuum[midp].point
        midval1 = 0 if midp == 0 else continuum[midp - 1].point

        if h <= midval and h > midval1:
            return continuum[midp]

        if midval < h:
            lowp = midp + 1
        else:
            highp = midp - 1

        if lowp > highp:
            return continuum[0]
    raise RuntimeError("ketama_get_server 二分未收敛（不应发生）")


def ketama_get_server(key, continuum):
    return ketama_get_server_h(ketama_hashi(key), continuum)


def ketama_hashi_reference(s):
    """独立参考实现：用 struct 解小端 uint32，用来交叉验证 ketama_hashi 的移位。"""
    return struct.unpack("<I", ketama_md5_digest(s)[:4])[0]
