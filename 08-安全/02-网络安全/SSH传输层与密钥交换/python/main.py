"""SSH 传输层：把 RFC 4253 §6/§7 与 RFC 8308 §2 的关键量跑出来。"""

from sshtrans import (
    mpint, ssh_string, padding_length, build_packet, parse_packet,
    block_size_for, Alg, select_kex, select_first_match, need_ok,
    derive_all, derive_key, exchange_hash, offers_ext_info,
    ext_negotiation_error, ext_info_allowed,
)

print("== 1. mpint 编码（RFC 4251 §5）==")
for v in (0, 127, 128, 255, -1, -128, -129):
    print("   %-6d -> %s" % (v, mpint(v).hex() or "(空串)"))

print("\n== 2. padding 与整包长度（§6）==")
print("   payload  blk  padding  packet_length  整包(不含mac)")
for L in (0, 1, 7, 10, 11, 16, 100):
    for blk in (8, 16):
        pad = padding_length(L, blk)
        print("   %-8d %-4d %-8d %-14d %d" % (L, blk, pad, pad + 1 + L, 4 + 1 + L + pad))
print("   约束：padding>=4 且<=255；(4+1+payload+padding) %% max(blk,8) == 0；整包>=max(16,blk)")

print("\n== 3. 组包/解包往返 ==")
raw = build_packet(b"hello", 8, mac=b"\xaa" * 32)
info = parse_packet(raw, 8, mac_len=32)
print("   载荷往返: %r" % info["payload"])
print("   padding_length=%d  packet_length=%d  mac=%s"
      % (info["padding_length"], info["packet_length"], info["mac"].hex()[:8] + "…"))

print("\n== 4. 算法协商（§7.1）==")
caps = {"ssh-rsa": {"sign", "encrypt"}, "ssh-dss": {"sign"}, "ssh-ed25519": {"sign"}}
kexs = [Alg("rsa2048-sha256", "encrypt"), Alg("curve25519-sha256"),
        Alg("diffie-hellman-group14-sha1")]
for hk in ("ssh-ed25519", "ssh-rsa"):
    got = select_kex(kexs, kexs, [hk], [hk], caps)
    print("   主机密钥=%-12s -> KEX=%s" % (hk, got.name if got else None))
print("   cipher 选法（只看名字）: %s"
      % select_first_match(["aes256-ctr", "aes128-ctr"], ["aes128-ctr", "3des-cbc"]))

print("\n== 5. 密钥派生（§7.2）==")
K, H1, H2 = mpint(0xABCDEF), b"\x11" * 32, b"\x22" * 32
k1 = derive_all(K, H1, H1)
k2 = derive_all(K, H2, H1)   # rekey：H 变、session_id 不变
for name in sorted(k1):
    print("   %-8s 首轮=%s… rekey=%s… 变了=%s"
          % (name, k1[name].hex()[:12], k2[name].hex()[:12], k1[name] != k2[name]))
print("   session_id 在 rekey 后仍是首次 KEX 的 H：%s" % (H1 == H1))

print("\n== 6. exchange hash（§8）==")
h = exchange_hash(b"SSH-2.0-C", b"SSH-2.0-S", b"I_C", b"I_S", b"K_S", 5, 7, 35)
print("   H = %s…" % h.hex()[:24])
print("   互换 V_C/V_S 后不同: %s"
      % (exchange_hash(b"SSH-2.0-S", b"SSH-2.0-C", b"I_C", b"I_S", b"K_S", 5, 7, 35) != h))

print("\n== 7. RFC 8308 扩展协商 ==")
print("   客户端放 ext-info-c: %s" % offers_ext_info(["ext-info-c"], "client"))
print("   服务端放 ext-info-c 不算数: %s" % (not offers_ext_info(["ext-info-c"], "server")))
print("   正常协商: %r" % ext_negotiation_error("curve25519-sha256", ["ext-info-c"], ["ext-info-s"]))
print("   indicator 被协商成 KEX: %r"
      % ext_negotiation_error("ext-info-c", ["ext-info-c"], ["ext-info-c"]))
print("   对端没给 indicator 时能否发 EXT_INFO: %s" % ext_info_allowed("server", False))
