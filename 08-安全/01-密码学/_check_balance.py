# -*- coding: utf-8 -*-
"""C/Go 源文件括号配平 + 行数检查(本机无 C/Go 工具链时的人工审查辅助)。"""
import re
import sys

FILES = [
    r"哈希/SHA-3-Keccak/sha3.go", r"哈希/SHA-3-Keccak/sha3.c",
    r"密钥派生/HKDF/hkdf.go", r"密钥派生/HKDF/hkdf.c",
    r"签名/ECDSA-P256/ecdsa.go", r"签名/ECDSA-P256/ecdsa.c",
    r"签名/ECDSA-P256/ecdsa.h", r"签名/ECDSA-P256/ecdsa_test.c",
    r"签名/ECDSA-P256/bn256.c", r"签名/ECDSA-P256/bn256.h",
    r"签名/ECDSA-P256/hmacsha256.c", r"签名/ECDSA-P256/hmacsha256.h",
    r"消息认证/HOTP-TOTP/hotp_totp.go", r"消息认证/HOTP-TOTP/hotp_totp.c",
    r"密码派生/PBKDF2/pbkdf2.go", r"密码派生/PBKDF2/pbkdf2.c",
]

def strip_literals(src):
    s = re.sub(r'"(?:[^"\\]|\\.)*"', '""', src)
    s = re.sub(r"'(?:[^'\\]|\\.)'", "''", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s

def main():
    bad = 0
    for f in FILES:
        src = open(f, encoding="utf-8").read()
        s = strip_literals(src)
        for name, o, c in (("curly", "{", "}"), ("paren", "(", ")"), ("square", "[", "]")):
            if s.count(o) != s.count(c):
                print("UNBALANCED %s %s: %d vs %d" % (f, name, s.count(o), s.count(c)))
                bad += 1
        lines = len(src.splitlines())
        print("%s: %d 行%s" % (f, lines, " <-- 超300行!" if lines > 300 else ""))
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
