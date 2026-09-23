"""HPKE（RFC 9180）演示。跑法：`python main.py`"""

import hpke as H
import hpke_aes as A
import hpke_x25519 as X

SKE = bytes.fromhex("52c4a758a802cd8b936eceea314432798d5baf2d7e9235dc084ab1b9cfa2f736")
SKR = bytes.fromhex("4612c550263fc8ad58375df3f557aac531d26850903e55a9f23f21d8534e8ac8")
PKR = bytes.fromhex("3948cfe0ad1ddb695d780e59077195da6c56506b027329794ab02bca80815c4d")
INFO = bytes.fromhex("4f6465206f6e2061204772656369616e2055726e")
PT = bytes.fromhex("4265617574792069732074727574682c20747275746820626561757479")


def show_suite():
    print("== 1. 三件套与两个 suite_id ==")
    print(f"  DHKEM(X25519, HKDF-SHA256)  kem_id = 0x{H.KEM_ID:04x}")
    print(f"  HKDF-SHA256                 kdf_id = 0x{H.KDF_ID:04x}")
    print(f"  AES-128-GCM                 aead_id = 0x{H.AEAD_ID:04x}")
    print(f"  KEM  侧 suite_id = {H.KEM_SUITE_ID!r}")
    print(f"  HPKE 侧 suite_id = {H.HPKE_SUITE_ID!r}")
    print("  同一个 KDF 在两处用的 suite_id 不同，这是 §4 里最容易被忽略的一点。")


def show_kem():
    print("\n== 2. KEM：X25519 + ExtractAndExpand ==")
    print(f"  pkRm = {PKR.hex()[:32]}...")
    ss, enc = H.encap(PKR, SKE)
    print(f"  enc（= pkEm）   = {enc.hex()[:32]}...")
    print(f"  shared_secret   = {ss.hex()}")
    print(f"  Decap 结果一致？{H.decap(enc, SKR) == ss}")
    print("  kem_context = enc ‖ pkRm —— 两个公钥都进 KDF，顺序不能反。")


def show_sched():
    print("\n== 3. KeySchedule：五个派生值 ==")
    ss, _ = H.encap(PKR, SKE)
    ks = H.key_schedule(H.MODE_BASE, ss, INFO)
    print(f"  key_schedule_context = {ks['key_schedule_context'].hex()[:40]}...")
    print(f"    结构 = mode(1B) ‖ psk_id_hash(32B) ‖ info_hash(32B) = "
          f"{len(ks['key_schedule_context'])} 字节")
    print(f"  secret          = {ks['secret'].hex()}")
    print(f"  key             = {ks['key'].hex()}  ({H.Nk} 字节)")
    print(f"  base_nonce      = {ks['base_nonce'].hex()}  ({H.Nn} 字节)")
    print(f"  exporter_secret = {ks['exporter_secret'].hex()}")


def show_seq():
    print("\n== 4. 序号与 nonce：XOR 而不是拼接 ==")
    ss, _ = H.encap(PKR, SKE)
    ctx = H.ContextS(H.key_schedule(H.MODE_BASE, ss, INFO))
    for s in (0, 1, 2, 255, 256):
        print(f"  seq {s:>3} -> nonce {ctx.compute_nonce(s).hex()}")
    print("  seq=256 时翻的是**倒数第二**字节（I2OSP 是大端），"
          "这点写反了只有在第 256 条消息上才会暴露。")
    print("\n  实际 Seal / Open：")
    ss2, enc2 = H.encap(PKR, SKE)
    s = H.ContextS(H.key_schedule(H.MODE_BASE, ss2, INFO))
    r = H.ContextR(H.key_schedule(H.MODE_BASE, ss2, INFO))
    for i in range(3):
        c = s.seal(f"Count-{i}".encode(), PT)
        ok = r.open(f"Count-{i}".encode(), c) == PT
        print(f"    第 {i} 条：ct {len(c)} 字节，接收端解开 {'成功' if ok else '失败'}"
              f"（seq 现为 {r.seq}）")


def show_export():
    print("\n== 5. Secret Export：把 HPKE 当 KDF 用 ==")
    ss, _ = H.encap(PKR, SKE)
    ctx = H.ContextS(H.key_schedule(H.MODE_BASE, ss, INFO))
    for c in (b"", bytes.fromhex("00"), b"TestContext"):
        print(f"  export({c.hex() or 'empty':<24}) = {ctx.export(c, 32).hex()}")
    print("  标签是 'sec'，输入是 exporter_secret 而不是 key —— "
          "导出的秘密与加密密钥**单向隔离**。")


def show_aes():
    print("\n== 6. 底座：AES-128-GCM 自证 ==")
    rk = A.key_expansion(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
    ct = A.encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff"))
    print(f"  FIPS 197 C.1: {ct.hex()}")
    print(f"  期望         : 69c4e0d86a7b0430d8cdb78070b4c55a  "
          f"{'✓' if ct.hex() == '69c4e0d86a7b0430d8cdb78070b4c55a' else '✗'}")


def main():
    show_suite()
    show_kem()
    show_sched()
    show_seq()
    show_export()
    show_aes()
    print("\n全部演示完成。断言版见 `python selfcheck_hpke.py`（55 条，对齐 RFC 9180 A.1）。")


if __name__ == "__main__":
    main()
