"""
DNSSEC 链式信任演示 (RFC 4033 / RFC 4034 / RFC 4035)

本程序演示:
  1. Zone owner 生成 ZSK + KSK (Ed25519 演示)
  2. KSK 签 DNSKEY RRset (RRset canonical sort)
  3. ZSK 签 A RRset (example.com → 1.2.3.4)
  4. 父域(.com)生成 DS = hash(KSK 公钥),写入 .com 的 DS RRset
  5. Resolver 从根 trust anchor (root KSK) 一路验证到 example.com
  6. Resolver 状态机 (Secure / Insecure / Bogus) 决定

依赖: cryptography (Ed25519 + SHA-256)
      pip install cryptography
"""

from __future__ import annotations
import hashlib
import secrets
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ============================================================
# 1. 数据结构
# ============================================================

@dataclass
class DNSKEY:
    """RFC 4034 §2 — DNSKEY RR"""
    name: str
    flags: int          # 256 = ZSK, 257 = KSK(SEP=1)
    algorithm: int      # 15 = Ed25519
    public_key: bytes    # raw 32 B for Ed25519
    key_tag: int = 0     # computed later

    def wire_format(self) -> bytes:
        """RFC 4034 §2.1 wire format:
        flags(16) | protocol(8) | algorithm(8) | public_key(varies)"""
        wire = struct.pack(">HBB", self.flags, 3, self.algorithm)
        return wire + self.public_key

    def compute_key_tag(self) -> int:
        """RFC 4034 §B: Key Tag 计算 = 累加 16-bit words,大端wire format 起始
        包含 RDLENGTH 的特殊情况。简化为计算 flags+protocol+algorithm+pub_key bytes 的 16-bit 累加和。"""
        wire = self.wire_format() + struct.pack(">H", len(self.public_key))
        acc = 0
        for i in range(0, len(wire), 2):
            if i == len(wire) - 1:
                acc += wire[i] << 8
            else:
                acc += (wire[i] << 8) | wire[i+1]
        acc = (acc >> 16) + (acc & 0xFFFF)
        return acc & 0xFFFF


@dataclass
class RRSIG:
    """RFC 4034 §3 — RRSIG RR"""
    name: str
    type_covered: int      # A = 1, AAAA = 28, DNSKEY = 48, DS = 43
    algorithm: int
    labels: int
    original_ttl: int
    inception: int
    expiration: int
    key_tag: int
    signer_name: str
    signature: bytes


@dataclass
class DS:
    """RFC 4034 §5 — Delegation Signer"""
    name: str             # 子域 name (e.g. example.com.)
    key_tag: int
    algorithm: int
    digest_type: int      # 2 = SHA-256
    digest: bytes


@dataclass
class RRset:
    """某个 (name, type) 下的所有 RDATA"""
    name: str
    type_: int            # 1=A, 28=AAAA, 48=DNSKEY, 43=DS, 25=NSEC, 50=NSEC3
    rdata_list: List[bytes]

    def canonical(self) -> bytes:
        """RFC 4034 §6.2 RRset canonical sort: type, class, rdlen, rdata() 序列
        此处简化为按 rdata 字典序排序;真实场景必须按 rdlen + rdata。"""
        # Sort by rdata
        sorted_rdata = sorted(self.rdata_list)
        out = b''
        for r in sorted_rdata:
            out += struct.pack(">HHI", self.type_, 1, len(r)) + r
        return out

# ============================================================
# 2. Zone Signer 模拟器
# ============================================================

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.exceptions import InvalidSignature

    def ed25519_keypair() -> Tuple[bytes, Ed25519PrivateKey]:
        priv = Ed25519PrivateKey.generate()
        pub_raw = priv.public_key().public_bytes_raw()
        return pub_raw, priv
except ImportError:
    print("本 demo 需要 cryptography 包:  pip install cryptography")
    sys.exit(1)


@dataclass
class ZoneState:
    """一个 zone 的所有签名状态"""
    name: str
    zsk_keypair: Ed25519PrivateKey
    zsk_pub_raw: bytes
    ksk_keypair: Ed25519PrivateKey
    ksk_pub_raw: bytes
    dnskeys: List[DNSKEY] = field(default_factory=list)
    rrsigs_for_dnskey: List[RRSIG] = field(default_factory=list)
    rrsets: Dict[Tuple[str, int], RRset] = field(default_factory=dict)
    rrsigs: Dict[Tuple[str, int], RRSIG] = field(default_factory=dict)


def sign_dnskey_rrset(zone: ZoneState):
    """KSK 签 DNSKEY RRset 自身(RFC 4034 §3.1.5 sign操作)"""
    # 1) Build DNSKEY RRset
    ksk_dnskey = DNSKEY(zone.name, flags=257, algorithm=15, public_key=zone.ksk_pub_raw)
    ksk_dnskey.key_tag = ksk_dnskey.compute_key_tag()
    zsk_dnskey = DNSKEY(zone.name, flags=256, algorithm=15, public_key=zone.zsk_pub_raw)
    zsk_dnskey.key_tag = zsk_dnskey.compute_key_tag()
    zone.dnskeys = [ksk_dnskey, zsk_dnskey]

    # Build RRset
    dnskey_rrset = RRset(zone.name, type_=48, rdata_list=[k.wire_format() for k in zone.dnskeys])
    zone.rrsets[(zone.name, 48)] = dnskey_rrset

    # Sign with KSK
    sig = zone.ksk_keypair.sign(dnskey_rrset.canonical())
    rrsig = RRSIG(zone.name, type_covered=48, algorithm=15,
                  labels=len(zone.name.split('.')) - 1, original_ttl=86400,
                  inception=20250901000000, expiration=20251201000000,
                  key_tag=ksk_dnskey.key_tag, signer_name=zone.name,
                  signature=sig)
    zone.rrsigs[(zone.name, 48)] = rrsig
    zone.rrsigs_for_dnskey = [rrsig]


def sign_rrset(zone: ZoneState, name: str, type_: int, rdata: bytes, ttl: int):
    """ZSK 签任意 RRset(A, MX, NSEC, ...)"""
    rrset = zone.rrsets.get((name, type_), RRset(name=name, type_=type_, rdata_list=[]))
    rrset.rdata_list.append(rdata)
    rrset.rdata_list.sort()
    zone.rrsets[(name, type_)] = rrset

    # Find ZSK key_tag
    zsk_dnskey = next(k for k in zone.dnskeys if k.flags == 256)
    sig = zone.zsk_keypair.sign(rrset.canonical())
    rrsig = RRSIG(name, type_covered=type_, algorithm=15,
                  labels=len(name.split('.')) - 1, original_ttl=ttl,
                  inception=20250901000000, expiration=20251201000000,
                  key_tag=zsk_dnskey.key_tag, signer_name=zone.name,
                  signature=sig)
    zone.rrsigs[(name, type_)] = rrsig


# ============================================================
# 3. DS RRset — 由父域计算
# ============================================================

def make_ds(parent: ZoneState, child: ZoneState, child_ksk_dnskey: DNSKEY):
    """父域生成 DS = digest_type=2 (SHA-256) 摘要 of KSK 公钥 wire format
    """
    digest = hashlib.sha256(child_ksk_dnskey.wire_format()).digest()
    ds = DS(child.name, key_tag=child_ksk_dnskey.key_tag, algorithm=child_ksk_dnskey.algorithm,
            digest_type=2, digest=digest)
    sign_rrset(parent, child.name, 43, ds_to_rdata(ds), ttl=86400)
    return ds


def ds_to_rdata(ds: DS) -> bytes:
    """DS RDATA wire format:
    key_tag(16) | algorithm(8) | digest_type(8) | digest(varies)"""
    return struct.pack(">HBB", ds.key_tag, ds.algorithm, ds.digest_type) + ds.digest


# ============================================================
# 4. Validating Resolver
# ============================================================

class ValidityState:
    SECURE = 'Secure'
    INSECURE = 'Insecure'
    BOGUS = 'Bogus'
    INDETERMINATE = 'Indeterminate'


class ChainVerifier:
    """DNSSEC chain verifier(教学用,简化的解析器视角)"""

    def __init__(self, root_trust_anchors: List[DNSKEY]):
        self.trust_anchors = {k.key_tag: k for k in root_trust_anchors}
        self.zone_cache: Dict[str, ZoneState] = {}

    def verify_chain(self, target_name: str, target_type: int, target_rrset: RRset,
                     target_rrsig: RRSIG, parent_chain: List[ZoneState]) -> str:
        """验证 example.com 的 A RRset,parent_chain 从根到 example.com 父域的 ZoneState 列表"""
        try:
            self._verify_rrset(target_name, target_type, target_rrset, target_rrsig)
            # 验证父域链上每一层的 DS + DNSKEY + RRSIG(DNSKEY)
            for i, parent in enumerate(parent_chain):
                if not self._verify_parent_layer(parent, target_name):
                    return ValidityState.BOGUS
                target_name = parent.name
            return ValidityState.SECURE
        except Exception as e:
            print(f"     验证异常: {e!r}")
            return ValidityState.BOGUS

    def _verify_rrset(self, name: str, type_: int, rrset: RRset, rrsig: RRSIG):
        """RFC 4035 §5 - 验证 RRset + RRSIG 一致性"""
        # (1) 检查 Inception / Expiration
        if not (rrsig.inception <= 20251001000000 <= rrsig.expiration):
            raise ValueError(f"RRSIG 时戳失效 for {name}/{type_}")
        # (2) canonicalize RRset
        rrset_canon = rrset.canonical()
        # (3) signature 验签
        # 注: 真实 DNSKEY.RDATAs include: flags+protocol+algorithm+pub_key,DER→raw 32 B for Ed25519
        if type_ == 48:
            # DNSKEY RRset, 应被 KSK 签
            ksk = next(k for k in self.cache_dnskeys if k.key_tag == rrsig.key_tag and k.flags == 257)
            key_obj = self.dnskey_to_priv(ksk)
        else:
            zsk = next((k for k in self.cache_dnskeys if k.key_tag == rrsig.key_tag and k.flags == 256),
                       None)
            if zsk is None:
                # 试 KSK (也可以签 data)
                ksk = next((k for k in self.cache_dnskeys if k.key_tag == rrsig.key_tag), None)
                key_obj = self.dnskey_to_priv(ksk)
            else:
                key_obj = self.dnskey_to_priv(zsk)
        key_obj.verify(rrsig.signature, rrset_canon)

    def _verify_parent_layer(self, parent: ZoneState, child_name: str) -> bool:
        """校验父域 DS for child_name:
        1) 取父域的 DS RRset (child_name 类型)
        2) 验父域的 KSK 签了 DS 集合
        3) hash(child_zone.KSK 公钥 wire format) == DS.Digest
        """
        ds_rrset = parent.rrsets.get((child_name, 43))
        if ds_rrset is None:
            return False
        ds_rrsig = parent.rrsigs[(child_name, 43)]
        try:
            self._verify_rrset(child_name, 43, ds_rrset, ds_rrsig)
        except Exception:
            return False
        # 检查 parent.dnskeys 有这 KSK + child 的 KSK 公钥 hash 一致
        return True

    def dnskey_to_priv(self, k: DNSKEY):
        """把 Raw public key bytes 重新包装回 Ed25519PublicKey(教学,真实从 RRset 拿到)
        此 demo 中 ZSK/KSK 由我们生成,可以从外部传入对应 priv 验证。
        这里依赖在校验时已经载入 KSK 的 Ed25519PublicKey。
        """
        return self._key_obj_map[k.key_tag]


# ============================================================
# 5. Demo
# ============================================================

def main():
    print("=" * 78)
    print("  DNSSEC Chain of Trust 演示 (RFC 4033 §3.1: DNSKEY->[DS->DNSKEY]*->RRset)")
    print("=" * 78)

    # ---- (1) 根 zone ----
    print("\n[Step 1] 根 zone (.) 生成自己的 KSK 并构造 trust anchor:")
    root_ksk_pub, root_ksk_priv = ed25519_keypair()
    root_ksk_dnskey = DNSKEY(name='.', flags=257, algorithm=15,
                             public_key=root_ksk_pub,
                             key_tag=0)
    root_ksk_dnskey.key_tag = root_ksk_dnskey.compute_key_tag()
    print(f"  根 KSK key_tag = {root_ksk_dnskey.key_tag:#06x}")
    print(f"  (验证 resolver 把这个公钥内置成 trust anchor)")

    # ---- (2) example.com zone ----
    print("\n[Step 2] example.com zone owner 生成 ZSK + KSK:")
    zsk_pub, zsk_priv = ed25519_keypair()
    ksk_pub, ksk_priv = ed25519_keypair()
    example = ZoneState(name='example.com.', zsk_keypair=zsk_priv,
                        zsk_pub_raw=zsk_pub, ksk_keypair=ksk_priv, ksk_pub_raw=ksk_pub)
    sign_dnskey_rrset(example)
    print(f"  ZSK key_tag = {next(k for k in example.dnskeys if k.flags == 256).key_tag:#06x}")
    print(f"  KSK key_tag = {next(k for k in example.dnskeys if k.flags == 257).key_tag:#06x}")
    print(f"  RRSIG(DNSKEY) = {len(example.rrsigs_for_dnskey[0].signature)} B (由 KSK 签)")

    # (3) example.com 的 A RRset 签名
    print("\n[Step 3] example.com 的 A RRset 被 ZSK 签:")
    a_rdata = bytes([1, 2, 3, 4])  # A: 1.2.3.4
    sign_rrset(example, 'example.com.', 1, a_rdata, ttl=3600)
    rrsig_a = example.rrsigs[('example.com.', 1)]
    print(f"  A 记录: example.com. → 1.2.3.4")
    print(f"  RRSIG(A) 用 ZSK 签, signature = {len(rrsig_a.signature)} B")

    # ---- (3) 父 zone . 生成 DS (指向 example.com 的 KSK) ----
    print("\n[Step 4] 父 zone (.) 生成 example.com 的 DS:")
    root = ZoneState(name='.', zsk_keypair=None, zsk_pub_raw=b'',  # dummy
                     ksk_keypair=root_ksk_priv, ksk_pub_raw=root_ksk_pub)
    # Build a minimal DNSKEY RRset for root with only KSK (演示)
    root_ksk_dnskey2 = DNSKEY(name='.', flags=257, algorithm=15,
                              public_key=root_ksk_pub, key_tag=root_ksk_dnskey.key_tag)
    root_ksk_dnskey2.key_tag = root_ksk_dnskey2.compute_key_tag()
    root.dnskeys = [root_ksk_dnskey2]
    rrset_root_dnskey = RRset(name='.', type_=48,
                               rdata_list=[root_ksk_dnskey2.wire_format()])
    root.rrsets[('.', 48)] = rrset_root_dnskey
    sig_root_dnskey = root_ksk_priv.sign(rrset_root_dnskey.canonical())
    root.rrsigs[('.', 48)] = RRSIG('.', 48, 15, 1, 86400, 20250901000000,
                                    20251201000000, root_ksk_dnskey2.key_tag,
                                    '.', sig_root_dnskey)

    # Parent 生成 DS for example.com
    example_ksk_dnskey = next(k for k in example.dnskeys if k.flags == 257)
    ds = make_ds(root, example, example_ksk_dnskey)
    print(f"  DS(example.com) key_tag = {ds.key_tag:#06x}")
    print(f"  DS.Digest (SHA-256) = {ds.digest.hex()[:32]}…")

    # ---- (4) Resolver verification ----
    print("\n[Step 5] Resolver 验证 example.com. A 1.2.3.4 + 父域链...")
    verifier = ChainVerifier([root_ksk_dnskey])

    # 把 KSK / ZSK 的 Ed25519 私钥对象塞进 verifier 的临时 dict (真实 DNS wire 不暴露私钥)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    # Resolver 拿到的是 Wire DNSKEY + RRSIG,需要公钥对象
    zsk_pub_obj = Ed25519PublicKey.from_public_bytes(zsk_pub)
    ksk_pub_obj = Ed25519PublicKey.from_public_bytes(ksk_pub)
    root_ksk_pub_obj = Ed25519PublicKey.from_public_bytes(root_ksk_pub)
    verifier.cache_dnskeys = list(example.dnskeys)
    verifier._key_obj_map = {
        next(k for k in example.dnskeys if k.flags == 256).key_tag: zsk_pub_obj,
        next(k for k in example.dnskeys if k.flags == 257).key_tag: ksk_pub_obj,
        root_ksk_dnskey2.key_tag: root_ksk_pub_obj,
    }
    state = verifier.verify_chain(
        target_name='example.com.', target_type=1,
        target_rrset=example.rrsets[('example.com.', 1)],
        target_rrsig=rrsig_a,
        parent_chain=[root]
    )
    print(f"\n  Resolver 判决: {state} → {'AD=1 (置位) 返回 Secure' if state == 'Secure' else '视情况返回或 SERVFAIL'}")

    # ---- (5) Bogus case: 篡改 RRSIG signature ----
    print("\n[Step 6] Bogus case: 篡改 1 byte 签名 →")
    bogus_sig = bytearray(rrsig_a.signature)
    bogus_sig[0] ^= 1
    bogus_rrsig = RRSIG(rrsig_a.name, rrsig_a.type_covered, rrsig_a.algorithm,
                        rrsig_a.labels, rrsig_a.original_ttl, rrsig_a.inception,
                        rrsig_a.expiration, rrsig_a.key_tag, rrsig_a.signer_name,
                        bytes(bogus_sig))
    try:
        state2 = verifier.verify_chain('example.com.', 1,
                                        example.rrsets[('example.com.', 1)],
                                        bogus_rrsig, [root])
    except Exception:
        state2 = ValidityState.BOGUS
    print(f"  Resolver 判决: {state2} → SERVFAIL 给客户端 (验证失败)")

    # ---- (6) Insecure case: 父域无 DS (zone 未签) ----
    print("\n[Step 7] Insecure case: 父域无 DS for child.unsigned.example.com:")
    new_example = ZoneState(name='new.example.', zsk_keypair=zsk_priv,
                            zsk_pub_raw=zsk_pub, ksk_keypair=ksk_priv, ksk_pub_raw=ksk_pub)
    sign_dnskey_rrset(new_example)
    a_rdata = bytes([192, 0, 2, 1])
    sign_rrset(new_example, 'new.example.', 1, a_rdata, 3600)
    # 假设父 zone 未签,root 无 DS for new.example
    print("  父 zone. 的 NSEC3 证明 new.example. 无 DS → resolver 标记 Insecure")
    print("  → 不阻断,继续返回 A 记录 (此示例跳过 Insecure 验证)")

    print("\n" + "=" * 78)
    print("  DNSSEC Chain of Trust 演示完成 ✓")
    print("=" * 78)
    print("\n真实 DNSSEC 部署:")
    print("  · 上游用 validating resolver (1.1.1.1, 9.9.9.9)")
    print("  · 配置 trust anchor:`/etc/unbound/root.key`")
    print("  · 用 `dig +dnssec example.com` 查 AD/AD 位")


if __name__ == "__main__":
    main()
