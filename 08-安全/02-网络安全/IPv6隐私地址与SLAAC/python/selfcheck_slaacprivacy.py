"""IPv6 隐私地址与 SLAAC 自检（实跑）。"""

import sys
from slaacprivacy import (
    TEMP_VALID_LIFETIME, TEMP_PREFERRED_LIFETIME, TEMP_IDGEN_RETRIES,
    UL_BIT_MASK, regen_advance, max_desync_factor, desync_valid,
    md5_iid, generate_iid_with_retries, eui64_from_mac,
    temp_address_lifetimes, should_create_temp_address, clamp_existing,
    regeneration_interval, rid_hmac_sha256, iid_from_rid_low,
    iid_from_rid_high, generate_iid_rfc8981,
)

N_PASS = 0


def ok(cond, msg):
    global N_PASS
    assert cond, "ASSERT FAILED: " + msg
    N_PASS += 1


# ---------------------------------------------------------------- E1 §3.8 常量

ok(TEMP_VALID_LIFETIME == 172800, "E1a TEMP_VALID_LIFETIME = 2 天 = 172800s")
ok(TEMP_PREFERRED_LIFETIME == 86400, "E1b TEMP_PREFERRED_LIFETIME = 1 天 = 86400s")
ok(TEMP_IDGEN_RETRIES == 3, "E1c TEMP_IDGEN_RETRIES = 3")
ok(TEMP_PREFERRED_LIFETIME < TEMP_VALID_LIFETIME,
   "E1d TEMP_PREFERRED 必须小于 TEMP_VALID（否则地址一建就快失效）")
ok(regen_advance() == 5.0, "E1e 默认 REGEN_ADVANCE = 2 + 3*1*1000/1000 = 5 秒")
ok(regen_advance(3, 2, 1000) == 8.0, "E1f DAD=2 时 REGEN_ADVANCE = 8")
ok(regen_advance(3, 1, 2000) == 8.0, "E1g RetransTimer=2000ms 时 = 8")
ok(max_desync_factor() == 34560.0, "E1h MAX_DESYNC_FACTOR = 0.4*86400 = 34560s")
ok(desync_valid(0) and desync_valid(34560), "E1i DESYNC 的两个端点都合法")
ok(not desync_valid(34561), "E1j 超过 MAX_DESYNC_FACTOR 非法")
ok(not desync_valid(-1), "E1k 负数非法")
ok(desync_valid(34560), "E1l DESYNC 上限 34560 < 86400-5 = 86395，满足第二条约束")

# ---------------------------------------------------------------- E2 §3.2.1 MD5 方案

hist, pub = 0x0123456789ABCDEF, 0xAABBCCDDEEFF0011
iid, nxt = md5_iid(hist, pub)
ok(0 <= iid < (1 << 64), "E2a IID 是 64 位")
ok(iid & (UL_BIT_MASK << 56) == 0, "E2b bit 6（U/L 位）被清零 —— 表示本地意义")
ok(md5_iid(hist, pub) == (iid, nxt), "E2c 确定性：同输入同输出")
ok(md5_iid(hist + 1, pub)[0] != iid, "E2d history 变 -> IID 变")
ok(md5_iid(hist, pub + 1)[0] != iid, "E2e 公开 IID 变 -> IID 变（MAC 参与计算）")
# 右 64 位就是下一次的 history
ok(nxt == int.from_bytes(__import__("hashlib").md5(
    hist.to_bytes(8, "big") + pub.to_bytes(8, "big")).digest()[8:], "big"),
   "E2f 下一次的 history = MD5 的右 64 位")
# 链：连续生成永不重复（除非碰撞）
seen = set()
h = hist
for _ in range(200):
    i, h = md5_iid(h, pub)
    seen.add(i)
ok(len(seen) > 198, "E2g 连续 200 次生成的 IID 几乎互不重复（实测 %d 个不同）" % len(seen))
# 两个不同的 MAC 即使第一次撞了，下一次也会分开（§3.2.1 末段的设计目的）
ok(md5_iid(hist, pub)[0] != md5_iid(hist, pub ^ 1)[0], "E2h 不同 MAC 的临时地址不同")

ok(eui64_from_mac(b"\x00\x11\x22\x33\x44\x55") ==
   int.from_bytes(b"\x02\x11\x22\xff\xfe\x33\x44\x55", "big"),
   "E2i EUI-64：插入 fffe 并翻转 U/L 位")
ok(eui64_from_mac(b"\x00\x11\x22\x33\x44\x55") & (UL_BIT_MASK << 56) != 0,
   "E2j 公开地址的 U/L 位被翻成 1（全局）")
ok(md5_iid(hist, eui64_from_mac(b"\x00\x11\x22\x33\x44\x55"))[0] & (UL_BIT_MASK << 56) == 0,
   "E2k 临时地址的 U/L 位被清成 0（本地）—— 两者正好相反")

# ---------------------------------------------------------------- E3 §3.3 step 7 冲突重试

reserved = {md5_iid(hist, pub)[0]}
got, h2 = generate_iid_with_retries(hist, pub, reserved)
ok(got is not None and got not in reserved, "E3a 第一次冲突后用右 64 位重来")
iid2, nxt2 = md5_iid(nxt, pub)
ok(got == iid2, "E3b 重来一次得到的就是 MD5(右64位 || pub) 的左 64 位")
ok(h2 == nxt2, "E3c 成功后保存的 history 是**这次** MD5 的右 64 位")
reserved3 = {md5_iid(hist, pub)[0], md5_iid(nxt, pub)[0]}
got3, _ = generate_iid_with_retries(hist, pub, reserved3)
ok(got3 is not None and got3 not in reserved3, "E3d 连撞两次仍能在 3 次内成功")
reserved_all = set()
hh = hist
for _ in range(TEMP_IDGEN_RETRIES):
    i, hh = md5_iid(hh, pub)
    reserved_all.add(i)
ok(generate_iid_with_retries(hist, pub, reserved_all) == (None, None),
   "E3e 连续 TEMP_IDGEN_RETRIES 次都撞 -> 放弃并记录系统错误")

# ---------------------------------------------------------------- E4 §3.3 step 4 生命周期

v, p = temp_address_lifetimes(2592000, 604800)
ok(v == TEMP_VALID_LIFETIME, "E4a 前缀 valid 更长时取 TEMP_VALID_LIFETIME")
ok(p == TEMP_PREFERRED_LIFETIME, "E4b 前缀 preferred 更长时取 TEMP_PREFERRED_LIFETIME")
ok(temp_address_lifetimes(3600, 3600) == (3600, 3600),
   "E4c 前缀生命周期更短时两个都按前缀来")
v2, p2 = temp_address_lifetimes(2592000, 604800, desync_factor=34560)
ok(v2 == TEMP_VALID_LIFETIME, "E4d **valid 不减 DESYNC_FACTOR**")
ok(p2 == TEMP_PREFERRED_LIFETIME - 34560, "E4e preferred 才减 DESYNC_FACTOR")
ok(p2 == 51840, "E4f DESYNC 取上限时 preferred = 51840s（14.4 小时）")
ok(temp_address_lifetimes(2592000, 604800, 0)[0] ==
   temp_address_lifetimes(2592000, 604800, 34560)[0],
   "E4g valid 与 DESYNC 无关 —— 最易写错的一点")
ok(temp_address_lifetimes(100, 100) == (100, 100), "E4h 前缀 100s 时两者都取 100")

# ---------------------------------------------------------------- E5 §3.3 step 5 是否创建

ok(should_create_temp_address(604800), "E5a 正常前缀 -> 创建")
ok(not should_create_temp_address(0), "E5b 前缀 preferred = 0 -> 不创建")
ok(not should_create_temp_address(5), "E5c preferred 恰好等于 REGEN_ADVANCE(5) -> 不创建（必须严格大于）")
ok(should_create_temp_address(6), "E5d preferred = 6 > 5 -> 创建")
ok(not should_create_temp_address(604800, desync_factor=86400),
   "E5e DESYNC 大到把 preferred 压到 0 -> 不创建")

# ---------------------------------------------------------------- E6 §3.3 step 2 上限裁剪

ok(clamp_existing(0, 0, 604800) == TEMP_PREFERRED_LIFETIME,
   "E6a RA 给的更长时按 CREATION_TIME + TEMP_PREFERRED 截断")
ok(clamp_existing(0, 0, 3600) == 3600, "E6b RA 给的更短时按 RA 来")
ok(clamp_existing(0, 100, 604800) == TEMP_PREFERRED_LIFETIME,
   "E6c 上限是绝对时刻而非剩余时长")
ok(clamp_existing(0, 1000, 604800, desync_factor=34560) == 86400 - 34560,
   "E6d 已有地址的 preferred 上限同样减 DESYNC")
ok(clamp_existing(0, 0, 604800, 0) > clamp_existing(0, 0, 604800, 34560),
   "E6e DESYNC 让不同客户端的过期时刻错开")

# ---------------------------------------------------------------- E7 §3.5 重新生成频率

ok(regeneration_interval() == 86400 - 5, "E7a DESYNC=0 时每 86395 秒重生成一次")
ok(regeneration_interval(34560) == 86400 - 5 - 34560, "E7b DESYNC 取上限时缩短到 51835 秒")
ok(regeneration_interval() > regeneration_interval(34560), "E7c DESYNC 越大重生成越频繁")
ok(regeneration_interval(max_desync_factor()) > 0, "E7d 即使 DESYNC 取上限间隔仍为正")

# ---------------------------------------------------------------- E8 RFC 8981 §3.3.2

key = bytes(range(32))
prefix = int("20010db8", 16) << 96
rid = rid_hmac_sha256(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid", 1700000000, 0)
ok(len(rid) == 32, "E8a HMAC-SHA-256 输出 32 字节")
ok(iid_from_rid_low(rid) != iid_from_rid_high(rid),
   "E8b 从最低位取与从最高位取得到不同的 IID —— 两个 RFC 的取位方向相反")
ok(iid_from_rid_low(rid) == int.from_bytes(rid[-8:], "big"), "E8c 取的是最后 8 字节")
ok(len(key) * 8 >= 128, "E8d secret_key 至少 128 位")
ok(rid_hmac_sha256(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid", 1700000000, 0) ==
   rid_hmac_sha256(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid", 1700000000, 0),
   "E8e F() 是确定性的")
ok(rid_hmac_sha256(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid", 1700000000, 1) != rid,
   "E8f DAD_Counter 加 1 会改变 RID")
ok(rid_hmac_sha256(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid", 1700000001, 0) != rid,
   "E8g Time 变会改变 RID（同一网络下随时间推移换地址）")
ok(rid_hmac_sha256(key, prefix, b"\x00\x11\x22\x33\x44\x56", b"ssid", 1700000000, 0) != rid,
   "E8h Net_Iface 变会改变 RID")
ok(rid_hmac_sha256(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"other", 1700000000, 0) != rid,
   "E8i Network_ID 变会改变 RID（换 SSID 就换地址）")
ok(rid_hmac_sha256(bytes(range(1, 33)), prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid",
                   1700000000, 0) != rid, "E8j secret_key 变会改变 RID")

iid98, cnt = generate_iid_rfc8981(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid",
                                  1700000000, set())
ok(cnt == 0 and iid98 is not None, "E8k 无冲突时 DAD_Counter 停在 0")
bad = {iid98}
iid98b, cnt2 = generate_iid_rfc8981(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid",
                                    1700000000, bad)
ok(cnt2 == 1 and iid98b != iid98, "E8l 冲突后 DAD_Counter 加 1 再算")
ok(iid98 != md5_iid(hist, pub)[0], "E8m 两个 RFC 的方案给出不同的 IID")
# RFC 8981 不再清 U/L 位：一批 IID 里应当出现 U/L = 1 的（约一半）
ul_set = 0
for t in range(1700000000, 1700000064):
    v, _ = generate_iid_rfc8981(key, prefix, b"\x00\x11\x22\x33\x44\x55", b"ssid", t, set())
    if v & (UL_BIT_MASK << 56):
        ul_set += 1
ok(ul_set > 0, "E8n RFC 8981 不清 U/L 位：64 个时间片里有 %d 个 U/L=1（RFC 7136：IID 无特殊位）" % ul_set)
ul_set_md5 = 0
hh = hist
for _ in range(64):
    v, hh = md5_iid(hh, pub)
    if v & (UL_BIT_MASK << 56):
        ul_set_md5 += 1
ok(ul_set_md5 == 0, "E8o 对照：RFC 4941 方案 64 个里 U/L=1 的个数为 %d（全部被清零）" % ul_set_md5)

print("PASS %d assertions" % N_PASS)
sys.exit(0)
