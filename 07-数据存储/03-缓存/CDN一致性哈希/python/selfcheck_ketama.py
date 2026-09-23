"""libketama 一致性哈希自检：所有期望值都是手算或独立参考实现得出的。"""

import hashlib

from ketama import (
    Mcs,
    f32,
    ketama_compare,
    ketama_create_continuum,
    ketama_get_server,
    ketama_get_server_h,
    ketama_hashi,
    ketama_hashi_reference,
    ketama_ks,
    ketama_ks_double,
    ketama_md5_digest,
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


# ---------------------------------------------------------------- ketama_hashi
for s in ("", "a", "abc", "127.0.0.1:11211", "key-42"):
    check(f"ketama_hashi({s!r}) 与 struct 小端解包一致",
          ketama_hashi(s), ketama_hashi_reference(s))

check("md5('') 的摘要长度", len(ketama_md5_digest("")), 16)
check("ketama_hashi 结果落在 uint32 内", 0 <= ketama_hashi("任意键") <= 0xFFFFFFFF, True)
check("空串哈希 = md5('') 前 4 字节小端",
      ketama_hashi(""), int.from_bytes(hashlib.md5(b"").digest()[:4], "little"))

# ---------------------------------------------------------------- 环构建：点数
for n in (2, 3, 4, 5, 6):
    cont = ketama_create_continuum([(f"s{i}", 1) for i in range(n)])
    check(f"等权 {n} 台 → 每台 160 点，共 {n*160}", len(cont), n * 160)

cont = ketama_create_continuum([("a", 1), ("b", 1), ("c", 1)])
check("每台都是 160 点", {ip: sum(1 for m in cont if m.ip == ip) for ip in "abc"},
      {"a": 160, "b": 160, "c": 160})
check("环按 point 升序", all(cont[i].point <= cont[i + 1].point for i in range(len(cont) - 1)), True)

# 不等权
cont2 = ketama_create_continuum([("a", 3), ("b", 1)])
check("3:1 权重 → ks 分别 60 / 20", (ketama_ks(3, 4, 2), ketama_ks(1, 4, 2)), (60, 20))
check("  对应点数 240 / 80", {ip: sum(1 for m in cont2 if m.ip == ip) for ip in "ab"},
      {"a": 240, "b": 80})
check("  总点数 = numservers * 160", len(cont2), 320)

# ---------------------------------------------------------------- 单精度细节（易踩坑）
for n in (3, 7, 11, 13):
    check(f"等权 {n} 台：C 的单精度路径 ks=40", ketama_ks(1, n, n), 40)
check("  等权 7 台：全程 double 会算成 39（与 C 不同）", ketama_ks_double(1, 7, 7), 39)
check("  等权 6 台：double 与 C 一致", ketama_ks_double(1, 6, 6), 40)
check("f32 把 0.1 收成单精度", f32(0.1) != 0.1, True)

# ---------------------------------------------------------------- 点值公式
d = ketama_md5_digest("s0-0")
pts = {m.point for m in ketama_create_continuum([("s0", 1)])}
check("一次 md5 的 4 段全部成为环上的点",
      all(int.from_bytes(d[i * 4:i * 4 + 4], "little") in pts for i in range(4)), True)
first = ketama_create_continuum([("s0", 1)])
expect = sorted(int.from_bytes(ketama_md5_digest(f"s0-{k}")[i * 4:i * 4 + 4], "little")
                for k in range(40) for i in range(4))
check("整个环的点值与独立重算完全一致", [m.point for m in first], expect)

# ---------------------------------------------------------------- 比较器
check("ketama_compare 小于", ketama_compare(Mcs(1, "a"), Mcs(2, "b")), -1)
check("ketama_compare 大于", ketama_compare(Mcs(3, "a"), Mcs(2, "b")), 1)
check("ketama_compare 相等（不看 ip）", ketama_compare(Mcs(2, "a"), Mcs(2, "b")), 0)

# ---------------------------------------------------------------- 二分查找（手算）
manual = [Mcs(10, "a"), Mcs(20, "b"), Mcs(30, "c")]
cases = [
    (0, "a"),    # h=0：highp 走到 -1 → 回滚
    (5, "a"),
    (10, "a"),   # 命中 a 自身（h > midval1=0）
    (11, "b"),   # 落在 (10, 20]
    (15, "b"),
    (20, "b"),
    (21, "c"),   # 落在 (20, 30]
    (30, "c"),
    (35, "a"),   # 超出最大点 → midp == numpoints → 回滚到 0
]
for h, want in cases:
    check(f"h={h} → {want}", ketama_get_server_h(h, manual).ip, want)

check("空环返回 None", ketama_get_server_h(1, []), None)
check("ketama_get_server 先算 hash 再查", ketama_get_server("x", manual),
      ketama_get_server_h(ketama_hashi("x"), manual))

# ---------------------------------------------------------------- 真实环上的稳定性
real = ketama_create_continuum([(f"10.0.0.{i}", 1) for i in range(1, 5)])
picks = [ketama_get_server(f"key-{i}", real).ip for i in range(200)]
check("同一个 key 两次查询结果一致", picks, [ketama_get_server(f"key-{i}", real).ip for i in range(200)])
check("4 台机器都被用到", len(set(picks)), 4)
counts = {ip: picks.count(ip) for ip in set(picks)}
check("每台至少分到 20 个 key（远非 0）", all(v >= 20 for v in counts.values()), True)

print(f"\nketama 自检：{PASS} 条通过，{FAIL} 条失败")
if FAIL:
    raise SystemExit(1)
