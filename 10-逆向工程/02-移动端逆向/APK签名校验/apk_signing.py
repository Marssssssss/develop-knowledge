# -*- coding: utf-8 -*-
"""APK 签名方案 v1/v2/v3 的结构与校验(依据 source.android.com 官方规格)。

实现内容:
  v1: MANIFEST.MF(逐条目摘要) -> .SF(整文件+逐节摘要) -> .RSA(对 .SF 签名) 的保护链
  v2: APK Signing Block 布局(uint64 size x2 + magic "APK Sig Block 42" + ID 0x7109871a)
      分块摘要: chunk = sha256(0xa5||uint32 len||data), top = sha256(0x5a||uint32 count||concat)
      EOCD 摘要口径: CD 偏移字段按"签名分块偏移"取值
      防回滚: .SF 主节 X-Android-APK-Signed: 2
  v3: ID 0xf05368c0 + minSDK/maxSDK(签名数据内外两份须一致) + proof-of-rotation(ID 0x3ba06f8c 单链表)
口径声明: 真实签名是 RSA-PSS/PKCS1/ECDSA(ASN.1);本模型用"键控摘要"替代
          (sign=kPriv,data 的 sha256, verify 用配对公钥),聚焦结构与校验流程。
跑法: python apk_signing.py -> 14 项断言
"""
import io, sys, struct, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SIG_BLOCK_MAGIC = b"APK Sig Block 42"
V2_ID, V3_ID, POR_ID = 0x7109871a, 0xf05368c0, 0x3ba06f8c
CHUNK = 1024 * 1024

def sha(b): return hashlib.sha256(b).digest()
def keypair(seed):                   # 键控摘要的"非对称"配对
    priv, pub = seed, sha(b"pub-" + seed)
    return priv, pub
def sign(priv, data): return sha(b"sig|" + priv + b"|" + data)
def verify(pub, data, sig, priv):    # pub 与 priv 配对才验证通过
    return sig == sign(priv, data) and sha(b"pub-" + priv) == pub

# ---------------- v2 分块摘要(官方两级 Merkle 口径) ----------------
def chunk_digests(data):
    return [sha(bytes([0xa5]) + struct.pack("<I", len(c)) + c)
            for c in (data[i:i + CHUNK] for i in range(0, len(data), CHUNK))]
def top_digest(data):
    chunks = chunk_digests(data)
    return sha(bytes([0x5a]) + struct.pack("<I", len(chunks)) + b"".join(chunks))

def apk_digest(content, cd, eocd_bytes, block_offset):
    """官方: 第 1/3/4 部分参与; 第 4 部分(EOCD)计算时 CD 偏移字段视为签名分块偏移。
    eocd_bytes 布局按真实 EOCD: 偏移 16 处 uint32 是 CD 起始偏移。"""
    eocd = bytearray(eocd_bytes)
    struct.pack_into("<I", eocd, 16, block_offset)
    return top_digest(content + cd + bytes(eocd))

# ---------------- APK Signing Block 布局 ----------------
def u32(x): return struct.pack("<I", x)
def u64(x): return struct.pack("<Q", x)
def lp(data): return u32(len(data)) + data          # uint32 长度前缀

def build_signing_block(pairs):
    body = b"".join(u64(4 + len(v)) + u32(k) + v for k, v in pairs)
    size = 8 + len(body) + 8                        # 不含首个 size 字段本身
    return u64(size) + body + u64(size) + SIG_BLOCK_MAGIC

def parse_signing_block(block):
    assert len(block) >= 8 + 16, "block too short"
    size1 = struct.unpack_from("<Q", block, 0)[0]
    assert block[-16:] == SIG_BLOCK_MAGIC, "magic mismatch"
    size2 = struct.unpack_from("<Q", block, len(block) - 24)[0]
    assert size1 == size2, "two size fields differ"   # 官方验证步骤 1.1
    pairs, off = {}, 8
    end = len(block) - 24
    while off < end:
        plen = struct.unpack_from("<Q", block, off)[0]; off += 8
        pid = struct.unpack_from("<I", block, off)[0]
        pairs[pid] = block[off + 4: off + plen]; off += plen
    return pairs

def build_eocd(cd_offset, cd_size):
    # 真实 EOCD: sig(I) disk(H) cd_disk(H) entries_disk(H) entries_total(H) cd_size(I) cd_offset(I) comment_len(H)
    return struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, 1, 1,
                       cd_size & 0xFFFFFFFF, cd_offset, 0)

# ---------------- v1 JAR 签名保护链 ----------------
def build_v1(entries):
    mf = "".join("Name: %s\r\nSHA-256-Digest: %s\r\n\r\n" % (n, sha(d).hex())
                 for n, d in entries).encode()
    sf = ("X-Android-APK-Signed: 2\r\n\r\n" +       # 防回滚属性(v2 存在时必须)
          "SHA-256-Digest-Manifest: %s\r\n\r\n" % sha(mf).hex()).encode()
    return mf, sf
def verify_v1(entries, mf, sf, sig, pub, priv):
    for n, d in entries:                            # 链尾: MF 逐条目摘要 vs 内容
        want = "Name: %s\r\nSHA-256-Digest: %s\r\n\r\n" % (n, sha(d).hex())
        if want.encode() not in mf: return False, "entry digest"
    if ("SHA-256-Digest-Manifest: %s" % sha(mf).hex()).encode() not in sf:
        return False, "manifest digest"             # 链中: SF 摘要 vs MF
    if not verify(pub, sf, sig, priv):
        return False, "signature"                   # 链头: 签名 vs SF
    return True, "ok"
def rollback_protected(sf):
    return b"X-Android-APK-Signed: 2" in sf         # 官方: 有 v2 却无该属性 -> 拒绝

# ---------------- v2/v3 signer 组装与校验 ----------------
def build_v2_signer(digest, pub, priv, min_sdk=0, max_sdk=0x7FFFFFFF, por=None):
    sd = (lp(lp(u32(0x0101) + lp(digest))) +        # digests(算法 ID 0x0101 = SHA2-256 RSA-PSS 档)
          lp(lp(b"CERT-" + pub)) +                  # X.509 证书链(此处以 pub 为桩)
          u32(min_sdk) + u32(max_sdk))              # v3 的 SDK 范围(v2 忽略)
    if por: sd += lp(u32(POR_ID) + por)
    sig = sign(priv, sd)
    return lp(lp(sd) + u32(min_sdk) + u32(max_sdk) +
              lp(lp(u32(0x0101) + lp(sig))) + lp(pub))

def verify_v2_block(v2_bytes, content, cd, eocd_bytes, block_offset, pub, priv):
    """校验单个 v2/v3 signer(简化为单 signer)。v2_bytes = lp( lp(sd)+sdk+sdk+sigs+pub )。"""
    tot = struct.unpack_from("<I", v2_bytes, 0)[0]
    if tot + 4 != len(v2_bytes): return False, "signer length"
    off = 4
    sd_len = struct.unpack_from("<I", v2_bytes, off)[0]; off += 4
    sd = v2_bytes[off: off + sd_len]
    calc = apk_digest(content, cd, eocd_bytes, block_offset)
    if lp(u32(0x0101) + lp(calc)) not in sd:          # 官方步骤 3.6: 摘要一致
        return False, "content digest mismatch"
    if sign(priv, sd) not in v2_bytes:                # 官方步骤 3.2: 签名验 signed data
        return False, "signature mismatch"
    if pub not in v2_bytes:                           # 官方步骤 3.7: 证书与公钥一致
        return False, "public key mismatch"
    return True, "ok"

def build_por(chain):                               # 官方: 单链表,最旧证书为根,逐级签名
    levels = b""
    for i, (cert_pub, cert_priv, flags) in enumerate(chain):
        sd = lp(b"CERT-" + cert_pub) + u32(0x0101)
        sig = sign(chain[i - 1][1], sd) if i else b""   # 根节点无上一级
        levels += lp(lp(sd) + u32(flags) + u32(0x0101) + lp(sig))
    return lp(levels)

def verify_por(por_bytes, signer_cert):
    """官方口径: 单链表逐级"上一级证书为本级背书"; 最后一级必须等于 signer 证书。
    POR_PRIVS 充当验证方的"证书->私钥"钥匙环(真实实现用上一级证书的公钥验签)。"""
    lv_len = struct.unpack_from("<I", por_bytes, 0)[0]
    off, end = 4, min(4 + lv_len, len(por_bytes))
    certs, prev_priv, ok = [], None, True
    while off < end:
        n_len = struct.unpack_from("<I", por_bytes, off)[0]; off += 4
        node = por_bytes[off: off + n_len]; off += n_len
        sd_len = struct.unpack_from("<I", node, 0)[0]
        sd = node[4: 4 + sd_len]
        cert_len = struct.unpack_from("<I", sd, 0)[0]
        cert = sd[4: 4 + cert_len]
        certs.append(cert)
        sig_lp = 4 + sd_len + 8                       # lp(sd)+flags+alg 之后是 lp(sig)
        sig_len = struct.unpack_from("<I", node, sig_lp)[0]
        sig = node[sig_lp + 4: sig_lp + 4 + sig_len]
        if prev_priv is not None and sig != sign(prev_priv, sd):
            ok = False                                # 上一级证书没有为本级背书
        prev_priv = POR_PRIVS.get(cert)
    last_ok = bool(certs) and certs[-1] == b"CERT-" + signer_cert
    return ok and last_ok, certs

# ---------------- 断言 ----------------
def check(label, cond):
    print(("PASS" if cond else "FAIL"), "-", label)
    assert cond, label

POR_PRIVS = {}

def main():
    priv, pub = keypair(b"developer-key")
    content = bytes(range(256)) * 4096              # 1 MiB 整 -> 1 个 chunk
    content2 = content + b"\x00" * 500              # 1 MiB + 500 -> 2 个 chunk
    cd, eocd = b"CENTRAL-DIR", build_eocd(0, len(b"CENTRAL-DIR"))

    # 1. 分块摘要官方口径(手算对照)
    c = content[:CHUNK]
    want = sha(bytes([0xa5]) + struct.pack("<I", CHUNK) + c)
    check("1 单块摘要 = sha256(0xa5||len||data)", chunk_digests(content) == [want])
    two = chunk_digests(content2)
    check("2 超界拆 2 块且末块短", len(two) == 2 and
          two[1] == sha(bytes([0xa5]) + struct.pack("<I", 500) + content2[CHUNK:]))
    check("3 顶级摘要 = sha256(0x5a||count||concat)",
          top_digest(content) == sha(bytes([0x5a]) + struct.pack("<I", 1) + want))

    # 2. signing block 布局
    v2 = build_v2_signer(sha(b"placeholder"), pub, priv)
    blk = build_signing_block([(V2_ID, v2), (0xdeadbeef, b"unknown")])
    pairs = parse_signing_block(blk)
    check("4 布局解析: magic/双 size/ID-值对", V2_ID in pairs and 0xdeadbeef in pairs)
    check("5 未知识别 ID 应忽略仍可解析", pairs[0xdeadbeef] == b"unknown")

    # 3. 完整 v2 流程: 签名 -> 校验通过; 篡改 -> 拒绝
    blk_off = len(content)
    digest = apk_digest(content, cd, eocd, blk_off)
    v2 = build_v2_signer(digest, pub, priv)
    blk = build_signing_block([(V2_ID, v2)])
    pairs = parse_signing_block(blk)
    ok, why = verify_v2_block(pairs[V2_ID], content, cd, eocd, blk_off, pub, priv)
    check("6 v2 完整校验通过", ok)
    tampered = content[:100] + bytes([content[100] ^ 0xFF]) + content[101:]
    ok2, why2 = verify_v2_block(pairs[V2_ID], tampered, cd, eocd, blk_off, pub, priv)
    check("7 单字节篡改 -> 摘要不匹配", not ok2)

    # 4. EOCD 口径: CD 偏移字段按签名分块偏移取
    d_real_inserted = apk_digest(content, cd, eocd, blk_off)
    d_wrong = top_digest(content + cd + eocd)       # 没做偏移替换的错口径
    check("8 EOCD 偏移替换改变摘要(口径敏感)", d_real_inserted != d_wrong)

    # 5. v1 保护链 + 防回滚
    entries = [("classes.dex", b"DEX-BODY"), ("res.png", b"PNG-BODY")]
    mf, sf = build_v1(entries)
    sig = sign(priv, sf)
    ok3, _ = verify_v1(entries, mf, sf, sig, pub, priv)
    check("9 v1 保护链验证通过", ok3)
    bad = [("classes.dex", b"DEX-TAMPERED")]
    ok4, why4 = verify_v1(bad, mf, sf, sig, pub, priv)
    check("10 v1 条目篡改 -> 拒绝", not ok4)
    check("11 X-Android-APK-Signed 防回滚属性在 .SF", rollback_protected(sf))
    check("12 有 v2 无防回滚属性 -> 降级攻击风险", not rollback_protected(b"plain-sf"))

    # 6. v3 proof-of-rotation
    k1p, k1u = keypair(b"old-key"); k2p, k2u = keypair(b"new-key")
    POR_PRIVS[b"CERT-" + k1u] = k1p; POR_PRIVS[b"CERT-" + k2u] = k2p
    chain = [(k1u, k1p, 0), (k2u, k2p, 1)]          # 旧密钥(根) -> 新密钥
    por = build_por(chain)
    ok_por, certs = verify_por(por, k2u)
    check("13 合法 PoR 链(根->新,逐级背书)通过", ok_por and
          certs == [b"CERT-" + k1u, b"CERT-" + k2u])
    # 攻击者伪造: 自造根节点(其证书没有被旧密钥背书), 末级证书 != 签名者证书
    kXp, kXu = keypair(b"attacker")
    POR_PRIVS[b"CERT-" + kXu] = kXp
    sd_x = lp(b"CERT-" + kXu) + u32(0x0101)
    fake_por = lp(lp(lp(sd_x) + u32(0) + u32(0x0101) + lp(b"")))
    ok_fake, fake_certs = verify_por(fake_por, k2u)
    check("14 攻击者自造根节点 -> 末级非签名者证书,拒绝", not ok_fake and
          fake_certs == [b"CERT-" + kXu])
    # v3 的 minSDK/maxSDK 两份一致性已在 build_v2_signer 中双写(u32 两处)
    s = build_v2_signer(digest, pub, priv, min_sdk=24, max_sdk=33)
    check("15 v3 signer 内外 SDK 范围双写一致", s.count(u32(24)) >= 2 and s.count(u32(33)) >= 2)
    print("ALL 15 CHECKS PASSED")

if __name__ == "__main__":
    main()
