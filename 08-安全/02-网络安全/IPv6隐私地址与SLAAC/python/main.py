"""IPv6 隐私地址：把 RFC 4941 与 RFC 8981 的两套算法与生命周期跑出来。"""

from slaacprivacy import (
    TEMP_VALID_LIFETIME, TEMP_PREFERRED_LIFETIME, TEMP_IDGEN_RETRIES,
    regen_advance, max_desync_factor, desync_valid, md5_iid, eui64_from_mac,
    temp_address_lifetimes, should_create_temp_address, clamp_existing,
    regeneration_interval, rid_hmac_sha256, iid_from_rid_low,
    iid_from_rid_high, generate_iid_rfc8981,
)


def hx(v, n=8):
    return v.to_bytes(n, "big").hex()


print("== 1. RFC 8981 §3.8 默认参数 ==")
print("   TEMP_VALID_LIFETIME     = %d s (2 天)" % TEMP_VALID_LIFETIME)
print("   TEMP_PREFERRED_LIFETIME = %d s (1 天)" % TEMP_PREFERRED_LIFETIME)
print("   TEMP_IDGEN_RETRIES      = %d" % TEMP_IDGEN_RETRIES)
print("   REGEN_ADVANCE           = %.0f s  = 2 + 3*1*1000/1000" % regen_advance())
print("   MAX_DESYNC_FACTOR       = %.0f s  = 0.4 * TEMP_PREFERRED" % max_desync_factor())
print("   重生成间隔(DESYNC=0/上限) = %.0f s / %.0f s"
      % (regeneration_interval(), regeneration_interval(max_desync_factor())))

print("\n== 2. RFC 4941 §3.2.1 的 MD5 方案 ==")
mac = b"\x00\x11\x22\x33\x44\x55"
pub = eui64_from_mac(mac)
print("   MAC          -> EUI-64 (公开 IID) = %s  U/L=%d"
      % (hx(pub), (pub >> 56) & 0x02 != 0))
hist = 0x0123456789ABCDEF
for i in range(4):
    iid, hist = md5_iid(hist, pub)
    print("   第 %d 次      -> 临时 IID = %s  U/L=%d  下一个 history=%s"
          % (i + 1, hx(iid), (iid >> 56) & 0x02 != 0, hx(hist)))
print("   注意：公开地址 U/L=1（全局），临时地址 U/L=0（本地）—— 两者相反")

print("\n== 3. 生命周期（§3.3 step 4）：只有 preferred 减 DESYNC ==")
for d in (0, 10000, max_desync_factor()):
    v, p = temp_address_lifetimes(2592000, 604800, d)
    print("   DESYNC=%-7.0f valid=%-7d preferred=%-7d（valid 恒为 %d）"
          % (d, v, p, TEMP_VALID_LIFETIME))

print("\n== 4. 是否创建（§3.3 step 5）与上限裁剪（step 2）==")
for pref in (0, 4, 5, 6, 3600, 604800):
    print("   前缀 preferred=%-7d -> 创建=%s" % (pref, should_create_temp_address(pref)))
print("   已有地址 preferred 到期时刻 = %d（RA 更长时按 CREATION+TEMP_PREFERRED 截断）"
      % clamp_existing(0, 0, 604800))

print("\n== 5. RFC 8981 §3.3.2 的 PRF 方案 ==")
key, prefix = bytes(range(32)), int("20010db8", 16) << 96
rid = rid_hmac_sha256(key, prefix, mac, b"ssid", 1700000000, 0)
print("   RID            = %s…" % rid.hex()[:32])
print("   IID(从最低位取) = %s" % hx(iid_from_rid_low(rid)))
print("   IID(从最高位取) = %s   <- 与上一行不同，两个 RFC 取位方向相反"
      % hx(iid_from_rid_high(rid)))
for label, kw in (("Time+1", {"time_s": 1700000001}),
                  ("换 SSID", {"network_id": b"other"}),
                  ("DAD_Counter=1", {"dad_counter": 1})):
    base = dict(prefix=prefix, net_iface=mac, network_id=b"ssid",
                time_s=1700000000, dad_counter=0)
    base.update(kw)
    r2 = rid_hmac_sha256(key, **base)
    print("   %-14s -> IID = %s" % (label, hx(iid_from_rid_low(r2))))

print("\n== 6. DAD 冲突重试 ==")
iid0, c0 = generate_iid_rfc8981(key, prefix, mac, b"ssid", 1700000000, set())
iid1, c1 = generate_iid_rfc8981(key, prefix, mac, b"ssid", 1700000000, {iid0})
print("   无冲突: IID=%s counter=%d" % (hx(iid0), c0))
print("   撞一次: IID=%s counter=%d" % (hx(iid1), c1))
