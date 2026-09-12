"""AES-GCM demo 入口。

按 NIST SP 800-38D Appendix B / 测试用例 1-4 验证自实现与官方测试向量一致。

运行:
    python3 main.py
"""

import sys
from pathlib import Path

# 允许 `python main.py` 直接运行(把当前目录加到 sys.path)
sys.path.insert(0, str(Path(__file__).parent))

from aes128 import Aes128  # noqa: E402
from gcm import AesGcm  # noqa: E402


def hex2b(h: str) -> bytes:
    return bytes.fromhex(h)


def assert_eq(name: str, got: bytes, want: bytes) -> None:
    if got != want:
        raise AssertionError(f"{name} 失配:\n  got  = {got.hex()}\n  want = {want.hex()}")
    print(f"  ✓ {name}")


def test_aes128_kat() -> None:
    """FIPS 197 Appendix B 的 AES-128 已知答案测试。"""
    print("[1] AES-128 KAT (FIPS 197 Appendix B)")
    # 全部为零的 key + pt,期望 ct = 66 e9 4b d4 ef 8a 2c 3b 88 4c fa 59 ca 34 2b 2e
    cipher = Aes128(b"\x00" * 16)
    pt = b"\x00" * 16
    ct = cipher.encrypt_block(pt)
    assert_eq("FIPS197 B.1 加密",
              ct, hex2b("66e94bd4ef8a2c3b884cfa59ca342b2e"))
    # 解密还原
    pt2 = cipher.decrypt_block(ct)
    assert_eq("FIPS197 B.1 解密", pt2, pt)


def test_aes_gcm_kat() -> None:
    """NIST SP 800-38D 推荐的 AES-GCM 测试用例(Appendix B Test Case 1-4)。"""
    print("\n[2] AES-128-GCM KAT (NIST SP 800-38D Appendix B)")

    # Test Case 1: 全零 key/iv,空 pt + 空 aad
    gcm = AesGcm(b"\x00" * 16)
    ct, tag = gcm.encrypt(b"\x00" * 12, b"", b"")
    assert_eq("TC1 ct (空)", ct, b"")
    assert_eq("TC1 tag", tag, hex2b("58e2fccefa7e3061367f1d57a4e7455a"))

    # Test Case 2: 全零 key/iv,16 字节零 pt,空 aad
    gcm = AesGcm(b"\x00" * 16)
    ct, tag = gcm.encrypt(b"\x00" * 12, b"\x00" * 16, b"")
    assert_eq("TC2 ct", ct, hex2b("0388dace60b6a392f328c2b971b2fe78"))
    assert_eq("TC2 tag", tag, hex2b("ab6e47d42cec13bdf53a67b21257bddf"))

    # Test Case 3: 非零 key/iv,64 字节 pt,空 aad
    gcm = AesGcm(hex2b("feffe9928665731c6d6a8f9467308308"))
    pt = hex2b("d9313225f88406e5a55909c5aff5269a"
               "86a7a9531534f7da2e4c303d8a318a72"
               "1c3c0c95956809532fcf0e2449a6b525"
               "b16aedf5aa0de657ba637b391aafd255")
    expected_ct = hex2b("42831ec2217774244b7221b784d0d49c"
                        "e3aa212f2c02a4e035c17e2329aca12e"
                        "21d514b25466931c7d8f6a5aac84aa05"
                        "1ba30b396a0aac973d58e091473f5985")
    expected_tag = hex2b("4d5c2af327cd64a62cf35abd2ba6fab4")
    ct, tag = gcm.encrypt(hex2b("cafebabefacedbaddecaf888"), pt, b"")
    assert_eq("TC3 ct", ct, expected_ct)
    assert_eq("TC3 tag", tag, expected_tag)

    # Test Case 4: 同 TC3 但带 20 字节 AAD,pt 砍到 60 字节
    aad = hex2b("feedfacedeadbeeffeedfacedeadbeefabaddad2")
    pt4 = hex2b("d9313225f88406e5a55909c5aff5269a"
                "86a7a9531534f7da2e4c303d8a318a72"
                "1c3c0c95956809532fcf0e2449a6b525"
                "b16aedf5aa0de657ba637b39")
    expected_ct4 = hex2b("42831ec2217774244b7221b784d0d49c"
                         "e3aa212f2c02a4e035c17e2329aca12e"
                         "21d514b25466931c7d8f6a5aac84aa05"
                         "1ba30b396a0aac973d58e091")
    expected_tag4 = hex2b("5bc94fbc3221a5db94fae95ae7121a47")
    ct, tag = gcm.encrypt(hex2b("cafebabefacedbaddecaf888"), pt4, aad)
    assert_eq("TC4 ct", ct, expected_ct4)
    assert_eq("TC4 tag", tag, expected_tag4)

    # 解密回环测试
    pt4_back = gcm.decrypt(hex2b("cafebabefacedbaddecaf888"), ct, aad, tag)
    assert_eq("TC4 decrypt round-trip", pt4_back, pt4)


def test_aes_gcm_tamper() -> None:
    """篡改 ct / aad / tag 任一项,解密应抛 ValueError。"""
    print("\n[3] AES-GCM 篡改检测")
    gcm = AesGcm(hex2b("feffe9928665731c6d6a8f9467308308"))
    iv = hex2b("cafebabefacedbaddecaf888")
    pt = b"hello world" * 4
    aad = b"meta"
    ct, tag = gcm.encrypt(iv, pt, aad)
    # 改一个密文字节
    bad_ct = bytearray(ct); bad_ct[0] ^= 0x01
    try:
        gcm.decrypt(iv, bytes(bad_ct), aad, tag)
    except ValueError:
        print("  ✓ 密文位翻转被检测")
    else:
        raise AssertionError("篡改密文未触发校验")
    # 改 aad
    try:
        gcm.decrypt(iv, ct, b"metA", tag)
    except ValueError:
        print("  ✓ AAD 改动被检测")
    else:
        raise AssertionError("篡改 AAD 未触发校验")
    # 改 tag
    bad_tag = bytearray(tag); bad_tag[-1] ^= 0x01
    try:
        gcm.decrypt(iv, ct, aad, bytes(bad_tag))
    except ValueError:
        print("  ✓ tag 改动被检测")
    else:
        raise AssertionError("篡改 tag 未触发校验")


def main() -> None:
    test_aes128_kat()
    test_aes_gcm_kat()
    test_aes_gcm_tamper()
    print("\n全部 AES-GCM 测试通过 ✓")


if __name__ == "__main__":
    main()