"""SPAKE2 演示（RFC 9382，P256-SHA256-HKDF-HMAC 套件）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p256 import G, N, mul, encode_uncompressed, decode
from spake2 import (M, N_POINT, Party, run, build_tt, hkdf_extract,
                    hkdf_expand, mac, impersonate_guess)
from spake2_vectors import SPAKE2_VECTORS


def hx(b):
    return b.hex()


def line(t):
    print("\n" + t)
    print("-" * max(len(t) * 2, 60))


def main():
    line("1. P-256 参数与 M / N")
    print("M = %s" % encode_uncompressed(M).hex()[:32] + "...")
    print("N = %s" % encode_uncompressed(N_POINT).hex()[:32] + "...")
    print("G = %s" % encode_uncompressed(G).hex())

    line("2. RFC 9382 附录 B 第一组向量复算")
    v = SPAKE2_VECTORS[0]
    f = v["f"]
    w = int(f["w"], 16)
    ke, ke_b, cA, cB, ok = run(v["A"], v["B"], w, int(f["x"], 16), int(f["y"], 16))
    print("A=%r B=%r w=%s" % (v["A"], v["B"], f["w"]))
    print("Ke     = %s  期望 %s" % (ke.hex(), f["Ke"]))
    print("A conf = %s  期望 %s" % (cA.hex(), f["A conf"]))
    print("B conf = %s  期望 %s" % (cB.hex(), f["B conf"]))
    print("双向确认：%s" % ok)

    line("3. transcript TT 的编码（len 是 8 字节小端）")
    tt = build_tt(b"server", b"client", b"\x04" + b"\xaa" * 64,
                  b"\x04" + b"\xbb" * 64, b"\x04" + b"\xcc" * 64, 0x2ee5)
    print("len(A)||A       = %s" % tt[:14].hex())
    print("len(B)||B       = %s" % tt[14:28].hex())
    print("len(w)||w（末尾）= %s" % tt[-40:].hex())
    print("总长 = %d = 8+6 + 8+6 + (8+65)*3 + 8+32" % len(tt))

    line("4. 口令错位的代价")
    wt = int(f["w"], 16)
    a = Party("A", "server", "client", wt, int(f["x"], 16))
    b = Party("B", "client", "server", (wt + 1) % N, int(f["y"], 16))
    pA = a.start()
    pB = b.start()
    a.finish(pB)
    b.finish(pA)
    print("A 用真口令、B 用 w+1：")
    print("  A 的 Ke = %s" % a.Ke.hex())
    print("  B 的 Ke = %s" % b.Ke.hex())
    print("  确认通过 = %s（应为 False）" % (a.verify(b.confirm),))

    line("5. 离线字典攻击：冒充 B 可离线枚举口令")
    print("拿到 pA 与 A 的确认消息后，攻击者对每个候选 w' 重放 B 侧计算：")
    for d, label in ((0, "真口令"), (1, "w+1"), (2, "w+2")):
        hit = impersonate_guess("server", "client",
                                bytes.fromhex(f["pA"]), int(f["y"], 16),
                                (wt + d) % N, bytes.fromhex(f["A conf"]))
        print("  候选 %-6s -> 命中 = %s" % (label, hit))
    print("这正是 PAKE 安全性只能用「每轮一次在线猜测」来刻画的原因。")

    line("6. 安全考虑（RFC 9382 §7）")
    print("* 收到的群元素 MUST 做群成员检查，否则有攻击")
    print("* x / y 不可复用；复用时 pA(w1)-pA(w2) = (w1-w2)*M，随机量被消掉")
    print("* SPAKE2 不支持 augmentation，服务端必须存口令等价物")
    print("* M、N 的取法对安全性证明是关键的（不能有已知离散对数）")


if __name__ == "__main__":
    main()
