"""证书透明化演示：加入 → 审计路径 → 一致性证明 → SCT/STH。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ct_merkle as M
import ct_struct as S

BAR = "-" * 62


def main():
    print("1. 一棵 7 叶的 Merkle 树（RFC 6962 §2.1.3 的示例结构）")
    print(BAR)
    ds = [("cert-%d" % i).encode() for i in range(7)]
    root = M.mth(ds)
    print("   根:", root.hex()[:32], "…")
    for m in (0, 3, 4, 6):
        p = M.audit_path(m, ds)
        print("   d%d 的审计路径有 %d 个节点，复算根 %s"
              % (m, len(p), "一致" if M.root_from_audit_path(
                  M.leaf_hash(ds[m]), m, 7, p) == root else "不一致"))
    print("   —— d6 只要 2 个节点：树不是满的，右子树只有一片叶子")
    print("      流行的「看叶子索引的比特决定左右」口诀在这里会拼反")

    print()
    print("2. 审计路径：证明「这个证书在日志里」")
    print(BAR)
    fake = b"evil.example.com cert"
    p = M.audit_path(2, ds)
    print("   真 d2  ->", M.verify_inclusion(ds[2], 2, 7, p, root))
    print("   伪造的 ->", M.verify_inclusion(fake, 2, 7, p, root))

    print()
    print("3. 一致性证明：证明「日志只追加、没改写」")
    print(BAR)
    for old in (3, 5, 7):
        proof = M.consistency_proof(old, ds)
        ok = M.verify_consistency(old, 7, proof, M.mth(ds[:old]), M.mth(ds))
        print("   旧树 %d 叶 -> 新树 7 叶：%d 个节点，验证 %s"
              % (old, len(proof), "通过" if ok else "失败"))
    print("   节点数上界是 ceil(log2(n)) + 1 —— 与树的大小无关")

    print()
    print("4. SCT 与 STH 的编码（RFC 6962 §3.2 / §3.5）")
    print(BAR)
    lid = S.log_id(bytes(range(64)))
    entry = S.signed_entry(S.ENTRY_TYPE_X509, cert_der=b"\x30\x82\x01\x02")
    sct = S.build_sct(lid, 1700000000000, entry, b"\xAA" * 71)
    print("   LogID    :", lid.hex()[:32], "…（日志公钥 DER 的 SHA-256）")
    print("   SCT      : %d 字节 = 版本1 + LogID32 + 时间8 + 扩展2 + 算法2 + 签名2+71"
          % len(sct))
    print("   SCT 签的是: version ‖ sig_type ‖ timestamp ‖ entry ‖ extensions")
    sth_in = S.sth_signing_input(1700000000000, 7, root)
    print("   STH 签的是: version ‖ tree_hash ‖ timestamp ‖ tree_size ‖ root = %d 字节"
          % len(sth_in))
    print("   两者的 signature_type 不同（0 与 1），这是又一层域分隔")

    print()
    print("5. 为什么这些设计能防住恶意日志")
    print(BAR)
    print("   * 给了 SCT 就必须给得出**包含证明** —— 否则被客户端/监视器抓包")
    print("   * 树只增不改：一致性证明让「给 A 看一棵树、给 B 看另一棵」必被检出")
    print("   * 叶子/节点前缀不同（0x00/0x01）→ 不能把内部节点冒充成叶子（抗第二原像）")


if __name__ == "__main__":
    main()
