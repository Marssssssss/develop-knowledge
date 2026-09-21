"""SSH 传输层与密钥交换自检（实跑）。"""

import sys
from sshtrans import (
    MIN_PADDING, MAX_PADDING, MAX_UNCOMPRESSED_PAYLOAD, MAX_TOTAL_PACKET,
    mpint, ssh_string, name_list, block_size_for, padding_length, build_packet,
    parse_packet, max_payload_per_packet, Alg, select_kex, select_first_match,
    derive_key, derive_all, LETTERS, exchange_hash,
    EXT_INFO_C, EXT_INFO_S, offers_ext_info, ext_negotiation_error,
    ext_info_allowed, HASH,
)

N_PASS = 0


def ok(cond, msg):
    global N_PASS
    assert cond, "ASSERT FAILED: " + msg
    N_PASS += 1


# ---------------------------------------------------------------- E1 mpint 编码

ok(mpint(0) == b"", "E1a 0 编码为空串")
ok(mpint(127) == b"\x7f", "E1b 127 -> 单字节")
ok(mpint(128) == b"\x00\x80", "E1c 128 最高位为 1，前置 0x00")
ok(mpint(0x80) == b"\x00\x80", "E1d 0x80 同 128")
ok(mpint(-1) == b"\xff", "E1e -1 -> 0xff")
ok(mpint(255) == b"\x00\xff", "E1f 255 需要前置零")
ok(len(mpint(1 << 200)) == 26, "E1g 2^200 -> 25 字节加一个前置零")
ok(ssh_string(b"abc") == b"\x00\x00\x00\x03abc", "E1h string 带 4 字节长度")
ok(name_list(["a", "b"]) == ssh_string(b"a,b"), "E1i name-list 是逗号分隔的 string")

# ---------------------------------------------------------------- E2 padding 规则

ok(block_size_for(8) == 8 and block_size_for(16) == 16, "E2a 对齐单位 = max(block,8)")
ok(block_size_for(1) == 8, "E2b 流密码也要按 8 对齐（§6 明确要求）")
for payload_len in range(0, 40):
    for blk in (8, 16):
        pad = padding_length(payload_len, blk)
        ok(MIN_PADDING <= pad <= MAX_PADDING, "E2c payload=%d blk=%d padding=%d 在 [4,255]" % (payload_len, blk, pad))
        ok((4 + 1 + payload_len + pad) % block_size_for(blk) == 0,
           "E2d payload=%d blk=%d 总长按 %d 对齐" % (payload_len, blk, block_size_for(blk)))
        ok(4 + 1 + payload_len + pad >= max(16, blk),
           "E2e payload=%d blk=%d 不小于最小包长" % (payload_len, blk))
        # 最小性：减一个块就会破坏 padding >= 4
        ok(pad - block_size_for(blk) < MIN_PADDING,
           "E2f payload=%d blk=%d 再减一个块就 <4（最小性）" % (payload_len, blk))
ok(padding_length(0, 8) == 11, "E2g 空载荷 + 8 字节块 -> padding=11，整包 16 字节")
ok(padding_length(10, 8) == 9, "E2h payload=10 blk=8 -> padding=9（5+10=15 需补 1，不足 4 故加 8）")
ok(padding_length(11, 8) == 8, "E2i payload=11 blk=8 -> padding=8")
ok(padding_length(0, 16) == 11, "E2j 空载荷 + 16 字节块 -> padding=11，整包仍 16")
ok(max_payload_per_packet(8) <= MAX_UNCOMPRESSED_PAYLOAD,
   "E2k 单包最大载荷不超过文档规定的 32768")

# ---------------------------------------------------------------- E3 组包与解包

for blk in (8, 16):
    for L in (0, 1, 7, 16, 100):
        raw = build_packet(b"x" * L, blk, mac=b"\x00" * 32)
        info = parse_packet(raw, blk, mac_len=32)
        ok(info["payload"] == b"x" * L, "E3a blk=%d L=%d 载荷往返一致" % (blk, L))
        ok(info["packet_length"] == info["padding_length"] + 1 + L,
           "E3b blk=%d L=%d packet_length = padding+1+payload" % (blk, L))
        ok(len(raw) == 4 + info["packet_length"] + 32, "E3c blk=%d L=%d 总长含 mac" % (blk, L))
try:
    parse_packet(build_packet(b"abc", 8)[:-1], 8)
    ok(False, "E3d 截断的包应报错")
except ValueError:
    ok(True, "E3d 截断的包被拒绝")
bad = bytearray(build_packet(b"abc", 8))
bad[4] = 3                                    # 把 padding_length 改成 3
try:
    parse_packet(bytes(bad), 8)
    ok(False, "E3e padding<4 应报错")
except ValueError:
    ok(True, "E3e padding 少于 4 被拒绝")
ok(MAX_TOTAL_PACKET == 35000, "E3f 文档规定的总包上限 35000")

# ---------------------------------------------------------------- E4 算法协商

# RFC 4253 §7.1：ssh-rsa 既可签名也可加密，ssh-dss / ssh-ed25519 只能签名
caps = {"ssh-rsa": {"sign", "encrypt"}, "ssh-dss": {"sign"}, "ssh-ed25519": {"sign"}}
# rsa2048-sha256（RFC 4432）需要**加密**型主机密钥；curve25519 与 DH 需要签名型
c_kex = [Alg("rsa2048-sha256", "encrypt"), Alg("curve25519-sha256"),
         Alg("diffie-hellman-group14-sha1")]
s_kex = [Alg("rsa2048-sha256", "encrypt"), Alg("curve25519-sha256"),
         Alg("diffie-hellman-group14-sha1")]
ok(select_kex(c_kex, s_kex, ["ssh-ed25519"], ["ssh-ed25519"], caps).name ==
   "curve25519-sha256",
   "E4a 首选需要 encryption-capable 主机密钥而服务端只有 ed25519 -> 跳过首选")
ok(select_kex(c_kex, s_kex, ["ssh-rsa"], ["ssh-rsa"], caps).name == "rsa2048-sha256",
   "E4b 主机密钥是 ssh-rsa（具备加密能力）时首选才被选中")
ok(select_kex([Alg("only-client")], s_kex, ["ssh-rsa"], ["ssh-rsa"], caps) is None,
   "E4c 无交集 -> 返回 None（双方必须断开）")
ok(select_first_match(["aes256-ctr", "aes128-ctr"], ["aes128-ctr", "3des-cbc"]) == "aes128-ctr",
   "E4d cipher 取客户端列表里第一个双方都有的")
ok(select_first_match(["aes256-ctr"], ["3des-cbc"]) is None,
   "E4e cipher 无交集 -> None")
# 关键差异：同样的列表，KEX 会因为主机密钥能力而跳项，cipher 不会
ok(select_kex(c_kex, s_kex, ["ssh-ed25519"], ["ssh-ed25519"], caps).name == "curve25519-sha256",
   "E4f 换成 ed25519（仅签名）后仍然跳过 encrypt 型 KEX")
ok(select_first_match(["ext-info-c", "curve25519-sha256"], ["curve25519-sha256"]) ==
   "curve25519-sha256", "E4g indicator 不在服务端列表里时不影响选择")

# ---------------------------------------------------------------- E5 密钥派生

K, H1 = mpint(0xABCDEF), b"\x11" * 32
keys1 = derive_all(K, H1, H1)
ok(len(set(keys1.values())) == 6, "E5a 六个字母给出六个不同的密钥")
ok(keys1["iv_c2s"] == derive_key(K, H1, b"A", H1), "E5b A = 客户端到服务端 IV")
ok(keys1["mac_s2c"] == derive_key(K, H1, b"F", H1), "E5c F = 服务端到客户端完整性密钥")
ok(derive_key(K, H1, b"A", H1, need_bytes=64) != derive_key(K, H1, b"A", H1, need_bytes=32),
   "E5d 需求超过哈希长度时走链式扩展")
k64 = derive_key(K, H1, b"A", H1, need_bytes=64)
k32 = derive_key(K, H1, b"A", H1, need_bytes=32)
ok(k64[:32] == k32, "E5e 扩展是**追加**：前 32 字节与不扩展时相同")
ok(derive_key(K, H1, b"A", H1) != derive_key(K, H1, b"B", H1), "E5f 字母不同 -> 密钥不同")
# rekey：H 变了但 session_id 不变
H2 = b"\x22" * 32
keys2 = derive_all(K, H2, H1)
ok(keys2["enc_c2s"] != keys1["enc_c2s"], "E5g rekey 后密钥随 H 改变")
ok(all(derive_all(K, H2, H1)[k] != keys1[k] for k in keys1), "E5h 六个方向全部改变")
ok(derive_key(K, H2, b"A", H1) != derive_key(K, H2, b"A", H2),
   "E5i 同样的 H 下 session_id 不同 -> 密钥不同（session_id 参与派生）")
ok(derive_key(K, H1, b"A", H1) == derive_key(K, H1, b"A", H1), "E5j 派生是确定性的")

# ---------------------------------------------------------------- E6 交换哈希

h = exchange_hash(b"SSH-2.0-C", b"SSH-2.0-S", b"I_C", b"I_S", b"K_S", 5, 7, 35)
expected = HASH(ssh_string(b"SSH-2.0-C") + ssh_string(b"SSH-2.0-S")
                + ssh_string(b"I_C") + ssh_string(b"I_S") + ssh_string(b"K_S")
                + mpint(5) + mpint(7) + mpint(35))
ok(h == expected, "E6a H 的拼接顺序与类型（string vs mpint）")
ok(exchange_hash(b"SSH-2.0-S", b"SSH-2.0-C", b"I_C", b"I_S", b"K_S", 5, 7, 35) != h,
   "E6b V_C 与 V_S 互换 -> H 不同（双方版本串都参与）")
ok(exchange_hash(b"SSH-2.0-C", b"SSH-2.0-S", b"I_S", b"I_C", b"K_S", 5, 7, 35) != h,
   "E6c I_C 与 I_S 互换 -> H 不同（KEXINIT 报文全量绑定）")
ok(exchange_hash(b"SSH-2.0-C", b"SSH-2.0-S", b"I_C", b"I_S", b"K_S", 7, 5, 35) != h,
   "E6d e 与 f 互换 -> H 不同")
ok(len(h) == 32, "E6e SHA-256 输出 32 字节")

# ---------------------------------------------------------------- E7 RFC 8308 扩展协商

ok(offers_ext_info(["ext-info-c", "curve25519-sha256"], "client"), "E7a 客户端放 ext-info-c")
ok(offers_ext_info(["ext-info-s", "curve25519-sha256"], "server"), "E7b 服务端放 ext-info-s")
ok(not offers_ext_info(["ext-info-c"], "server"), "E7c 服务端放 ext-info-c 不算数")
ok(ext_negotiation_error("curve25519-sha256", ["ext-info-c"], ["ext-info-s"]) == "",
   "E7d 双方 indicator 不同，不会匹配成 KEX 方法")
ok(ext_negotiation_error("ext-info-c", ["ext-info-c"], ["ext-info-c"]) != "",
   "E7e indicator 被协商为 KEX 方法 -> 必须断开")
ok("客户端" in ext_negotiation_error("x", ["ext-info-s"], []),
   "E7f 客户端发了服务端专用 indicator 是错误")
ok("服务端" in ext_negotiation_error("x", [], ["ext-info-c"]),
   "E7g 服务端发了客户端专用 indicator 是错误")
ok(ext_info_allowed("server", True) is True, "E7h 对端给了 indicator 才可以发 EXT_INFO")
ok(ext_info_allowed("server", False) is False, "E7i 对端没给就不能发")
# indicator 加进列表不改变协商结果（§2.1 末句）
base = select_first_match(["curve25519-sha256"], ["curve25519-sha256"])
withind = select_first_match(["ext-info-c", "curve25519-sha256"], ["curve25519-sha256"])
ok(base == withind == "curve25519-sha256", "E7j 加 indicator 不影响算法选择")

print("PASS %d assertions" % N_PASS)
sys.exit(0)
