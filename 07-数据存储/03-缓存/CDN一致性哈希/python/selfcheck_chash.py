"""nginx chash 自检：CRC-32、环构建、二分查找、tries>20 回退。"""

import zlib

from ngx_chash import (
    CRC32_TABLE,
    ChashPeer,
    build_chash_points,
    chash_lookup,
    find_chash_point,
    get_chash_peer,
    ngx_crc32,
    ngx_crc32_final,
    ngx_crc32_init,
    ngx_crc32_update,
    split_host_port,
)

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got!r} want {want!r}")


# ---------------------------------------------------------------- CRC-32 表
check("ngx_crc32_table256 前 4 项（ngx_crc32.c:35）",
      CRC32_TABLE[:4], [0x00000000, 0x77073096, 0xEE0E612C, 0x990951BA])
check("表长 256", len(CRC32_TABLE), 256)
check("表项都在 uint32 内", all(0 <= v <= 0xFFFFFFFF for v in CRC32_TABLE), True)

# ---------------------------------------------------------------- crc32 与 zlib 对拍
check("crc32('') = 0（init 0xffffffff 与 final 异或抵消）", ngx_crc32(b""), 0)
check("crc32('a')", ngx_crc32(b"a"), 0xE8B7BE43)
for s in (b"", b"a", b"abc", b"127.0.0.1\x0080", b"\x00" * 8, bytes(range(64))):
    check(f"ngx_crc32({s[:12]!r}...) == zlib.crc32", ngx_crc32(s), zlib.crc32(s) & 0xFFFFFFFF)

# ---------------------------------------------------------------- base hash 的构造
# ngx 写的是 ngx_crc32_update(&base_hash, (u_char *) "", 1)，即一个 NUL 字节
host, port = "127.0.0.1", "80"
base = ngx_crc32_init()
base = ngx_crc32_update(base, host.encode())
base = ngx_crc32_update(base, b"\x00")
base = ngx_crc32_update(base, port.encode())
check("base_hash 定稿后 == zlib.crc32(HOST \\0 PORT)",
      ngx_crc32_final(base), zlib.crc32(b"127.0.0.1\x0080") & 0xFFFFFFFF)

# 少了那个 NUL 字节就对不上了（成对反例）
base_nod = ngx_crc32_update(ngx_crc32_update(ngx_crc32_init(), host.encode()), port.encode())
check("  少了 NUL 字节 → 结果不同",
      ngx_crc32_final(base_nod) != zlib.crc32(b"127.0.0.1\x0080") & 0xFFFFFFFF, True)

# ---------------------------------------------------------------- host/port 切分
cases = [
    ("127.0.0.1:80", ("127.0.0.1", "80")),
    ("unix:/tmp/x.sock", ("/tmp/x.sock", "")),
    ("UNIX:/tmp/x.sock", ("/tmp/x.sock", "")),
    ("backend", ("backend", "")),
    ("[::1]:8080", ("[::1]", "8080")),
    ("1.2.3.4", ("1.2.3.4", "")),
    ("", ("", "")),
]
for srv, want in cases:
    check(f"split_host_port({srv!r})", split_host_port(srv), want)

# ---------------------------------------------------------------- 环构建
srv1 = [("10.0.0.1:80", 1), ("10.0.0.2:80", 2)]
raw = build_chash_points(srv1, dedup=False)
pts = build_chash_points(srv1)
check("原始点数 = total_weight * 160", len(raw), 3 * 160)
check("去重后点数 <= 原始点数", len(pts) <= len(raw), True)
check("去重后仍按 hash 升序", all(pts[i][0] < pts[i + 1][0] for i in range(len(pts) - 1)), True)
check("去重后无相邻重复 hash", len({p[0] for p in pts}), len(pts))
check("两个 server 都出现在环上", {p[1] for p in pts}, {"10.0.0.1:80", "10.0.0.2:80"})
n1 = sum(1 for p in pts if p[1] == "10.0.0.1:80")
n2 = sum(1 for p in pts if p[1] == "10.0.0.2:80")
check("weight=2 的点数约为 weight=1 的两倍", abs(n2 / n1 - 2.0) < 0.15, True)

check("空 server 列表 → 空环", build_chash_points([]), [])
check("空环查找返回 0（等于 len）", find_chash_point([], 1), 0)

# ---------------------------------------------------------------- 二分查找（手算）
manual = [(10, "a"), (20, "b"), (30, "c")]
for h, want in [(0, 0), (5, 0), (10, 0), (11, 1), (20, 1), (21, 2), (30, 2), (31, 3)]:
    check(f"find_chash_point(h={h}) → 下标 {want}", find_chash_point(manual, h), want)

check("h 超过所有点 → 返回 len（由调用方回绕）", find_chash_point(manual, 31), len(manual))
check("回绕后取第 0 个点", manual[0][1] if find_chash_point(manual, 31) == len(manual) else manual[find_chash_point(manual, 31)][1], "a")
small = [(1, "a"), (2, "b")]  # 所有点的 hash 都很小
check("crc32('hello') 远大于环上所有点", ngx_crc32(b"hello") > 2, True)
check("chash_lookup 对超界 hash 回绕到首点 a", chash_lookup(small, "hello"), "a")

# ---------------------------------------------------------------- 重试与回退
st = ChashPeer(manual, 5, unavailable={"a"})
check("首选 a 不可用 → 递增 hash 重试到 b", get_chash_peer(st), "b")
check("  重试次数 6（hash 5→11）", st.tries, 6)
check("  未回退到 RR", st.fell_back_to_rr, False)

st = ChashPeer(manual, 5, unavailable=set())
check("首选可用 → 0 次重试", (get_chash_peer(st), st.tries), ("a", 0))

st = ChashPeer(manual, 5, unavailable={"a", "b", "c"})
check("全部不可用 → 回退 round-robin", get_chash_peer(st), "a")
check("  tries 达到 21（> 20 才回退）", st.tries, 21)
check("  已标记回退", st.fell_back_to_rr, True)
check("  hash 被递增了 21 次（5 → 26）", st.hash, 26)

# ---------------------------------------------------------------- 真实环稳定性
real = build_chash_points([(f"10.0.0.{i}:80", 1) for i in range(1, 5)])
picks = [chash_lookup(real, f"/img/{i}.jpg") for i in range(500)]
check("4 台都被用到", len(set(picks)), 4)
check("结果可重复", picks, [chash_lookup(real, f"/img/{i}.jpg") for i in range(500)])
check("每台至少 50 个 key", all(picks.count(s) >= 50 for s in set(picks)), True)

print(f"\nnginx chash 自检：{PASS} 条通过，{FAIL} 条失败")
if FAIL:
    raise SystemExit(1)
