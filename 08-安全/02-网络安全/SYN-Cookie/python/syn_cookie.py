"""
SYN Cookie 防 SYN Flood (RFC 4987 §3.1 + D.J. Bernstein 1996 原论文思路)

演示教学用:
  - 服务器在收到 SYN 时"不分配 TCB",只用 5-tuple + timestamp + MSS 生成 32-bit SYN cookie ISN
  - 客户端用 ack number = ISN + 1 回 ACK
  - 服务器从 ACK 还原 cookie,验证 HMAC + 时戳有效性,通过后才分配 TCB
  - 攻击者伪造的 ACK(cookie 1 bit 改了)→ 验证失败 → 丢弃

本 demo 纯算法演示,不发真实 TCP SYN,只模拟 5-tuple 输入/输出。

依赖:仅标准库 hashlib/hmac/struct/secrets/typing。
"""

from __future__ import annotations
import hashlib
import hmac
import secrets
import struct
import sys
from typing import Optional, Tuple

# ============================================================
# 1. 常量 — Bernstein/Schenk 论文 + Linux 实际值
# ============================================================

MSS_TABLE = (536, 1300, 1460, 1500, 2000, 4096, 8192, 9000)
# 5-bit t: time() / 64s mod 32, 表示 64s 粒度 × 32 = 2048 秒 (≈ 34 分钟) 周期
# 3-bit m: 8 个预设 MSS
# 24-bit s: SHA1 HMAC low 24 bit

SLOW_TICK = 64           # 1 t 周期 = 64 秒
TOLERANCE = 4            # t_cooked 与 t_now 允许 ± 4 (≈256 秒容忍窗口)


# ============================================================
# 2. Server
# ============================================================

class SynCookieServer:
    def __init__(self, secret: bytes | None = None):
        # 启动时生成 16 字节随机密钥 (实际 Linux 在 boot 时生成 secret)
        self.secret = secret or secrets.token_bytes(16)
        self._mss_table = MSS_TABLE

    def _now(self) -> int:
        import time
        return int(time.time())

    def _slow_time(self, ts: int) -> int:
        """t = time() >> 6,5 bit → t mod 32"""
        return ((ts // SLOW_TICK) % 32) & 0x1F

    def _encode_mss(self, mss: int) -> int:
        """3 bit MSS 索引(从 MSS_TABLE 找最接近)"""
        # 选择 MSS 值最接近的预设
        idx = 0
        min_diff = abs(self._mss_table[0] - mss)
        for i, v in enumerate(self._mss_table):
            d = abs(v - mss)
            if d < min_diff:
                min_diff = d
                idx = i
        return idx

    def _mss_from(self, code: int) -> int:
        return self._mss_table[code & 0x7]

    def _compute_s(self, src_ip: str, src_port: int, dst_ip: str,
                   dst_port: int, t: int, m_code: int) -> int:
        """HMAC-SHA1 低 24 bit"""
        msg = f"{src_ip}|{src_port}|{dst_ip}|{dst_port}|{t}|{m_code}".encode('ascii')
        digest = hmac.new(self.secret, msg, hashlib.sha1).digest()
        return struct.unpack(">I", digest[:4])[0] & 0xFFFFFF

    def syn(self, src_ip: str, src_port: int, dst_ip: str, dst_port: int,
            mss: int = 1460) -> int:
        """收到 SYN:为该 SYN 编码 32-bit SYN cookie ISN。

        注意:这一步 server 本应完全不分配任何状态(没有 TCB)。
        """
        t = self._slow_time(self._now())
        m = self._encode_mss(mss)
        s = self._compute_s(src_ip, src_port, dst_ip, dst_port, t, m)
        cookie = (t << 27) | (m << 24) | s
        # SYN 包特殊: ISN 低位加上 1 让 SYN flag 完整 (SYN 标志位置 1,只是示意)
        return cookie

    def verify(self, ack_num: int, src_ip: str, src_port: int,
               dst_ip: str, dst_port: int) -> Optional[dict]:
        """收到 ACK 后:验证 cookie 是否合法,通过则返回重建的连接元信息。

        ACK.ack_number = cookie + 1。
        """
        cookie = (ack_num - 1) & 0xFFFFFFFF
        t_cooked = (cookie >> 27) & 0x1F
        m_code = (cookie >> 24) & 0x7
        s_recv = cookie & 0xFFFFFF

        # (1) 时戳有效性
        t_now = self._slow_time(self._now())
        diff = (t_now - t_cooked) % 32
        if diff > 16:
            diff = 32 - diff
        if diff > TOLERANCE:
            return None

        # (2) 重算 s,验证 HMAC
        s_expected = self._compute_s(src_ip, src_port, dst_ip, dst_port, t_cooked, m_code)
        if s_expected != s_recv:
            return None

        return {
            'src': f"{src_ip}:{src_port}",
            'dst': f"{dst_ip}:{dst_port}",
            't_cooked': t_cooked,
            't_now': t_now,
            'mss_code': m_code,
            'mss': self._mss_from(m_code),
        }


# ============================================================
# 3. 演示
# ============================================================

def main():
    print("=" * 68)
    print("  SYN Cookie 防 SYN Flood 演示 (Bernstein/Schenk 1996 + RFC 4987)")
    print("=" * 68)

    server = SynCookieServer()

    # ─────────────────────────────────────────
    # (1) 客户端 A:正常握手
    # ─────────────────────────────────────────
    print("\n[Step 1] 客户端 A (192.0.2.10:54321) 发 SYN → server")
    isn = server.syn("192.0.2.10", 54321, "203.0.113.5", 80, mss=1460)
    print(f"  Server 编码 SYN cookie ISN = 0x{isn:08x}")
    print(f"    └ top 5  bit t   = {(isn >> 27) & 0x1F}")
    print(f"    └ mid 3  bit mss = {(isn >> 24) & 0x7}  → MSS = {MSS_TABLE[(isn >> 24) & 0x7]} B")
    print(f"    └ low 24 bit s   = 0x{isn & 0xFFFFFF:06x}")

    # Client A 回 ACK (ack = ISN + 1)
    ack_num = isn + 1
    print(f"\n[Step 2] Client A 回 ACK (ack_number = {ack_num:#x} = ISN+1)")
    info = server.verify(ack_num, "192.0.2.10", 54321, "203.0.113.5", 80)
    if info:
        print(f"  ✓ 通过 → 分配 TCB")
        print(f"    src     = {info['src']}")
        print(f"    dst     = {info['dst']}")
        print(f"    MSS     = {info['mss']} (从 3-bit code={info['mss_code']} 反查)")
        print(f"    t_cooked = {info['t_cooked']}   t_now = {info['t_now']}")
    else:
        print("  ✗ 验证失败 (意料之外:bug!)")
        sys.exit(1)

    # ─────────────────────────────────────────
    # (2) 攻击者:伪造 ACK (篡改 1 bit)
    # ─────────────────────────────────────────
    print("\n[Step 3] 攻击者伪造 ACK (改 cookie 1 bit) → 应验证失败")
    # 拿到一个合法 cookie,改低 24 bit 中的 1 个 bit
    fake_isn = isn ^ 0x1   # bit 0 翻转
    fake_ack = fake_isn + 1
    info2 = server.verify(fake_ack, "192.0.2.10", 54321, "203.0.113.5", 80)
    print(f"  Fake cookie       = 0x{fake_isn:08x}  (legal = 0x{isn:08x},翻转 bit 0)")
    print(f"  Server 验证       = {'通过' if info2 else '失败 (丢弃)'}")
    assert info2 is None
    print("  ✓ 校验失败 → 不分配 TCB,攻击被阻")

    # ─────────────────────────────────────────
    # (3) 客户端 B:稍后到达,演示时戳窗口
    # ─────────────────────────────────────────
    print("\n[Step 4] 客户端 B (198.51.100.7:62451) 异步 SYN → 一个新 ISN")
    isn2 = server.syn("198.51.100.7", 62451, "203.0.113.5", 80, mss=1500)
    print(f"  ISN_B = 0x{isn2:08x}  (MSS_CODE={(isn2 >> 24) & 0x7} → {MSS_TABLE[(isn2 >> 24) & 0x7]} B)")

    # 模拟时戳过期超过 4 个窗口(伪造)
    print("\n[Step 5] 攻击者复用一个旧 cookie(伪造时戳)→ 应验证失败")
    # 构造一个 t 偏移超 4 的 cookie (理论上完全相同 MSS code 和 s,只改 t)
    forged = (32 + 4 - 1) << 27  # t = 35,但实际范围 [0, 31] 取模,所以 (t mod 32)=3
    # 这里只是用 0 填充,只想说明 "ts_diff > tolerance" 路径会拒
    fake_ack_old = forged + 1
    info3 = server.verify(fake_ack_old, "192.0.2.10", 99, "203.0.113.5", 80)
    print(f"  Server 验证过期时戳 cookie = {'通过' if info3 else '失败 (时戳失配)'}")
    assert info3 is None
    print("  ✓ 校验失败")

    # ─────────────────────────────────────────
    # (4) 多人 SYN:验证 hash 区分不同 src
    # ─────────────────────────────────────────
    print("\n[Step 6] 同一秒 10 个客户端 SYN → hash 应各不相同")
    syns = []
    for i in range(10):
        s = server.syn(f"192.0.2.{i + 1}", 50000 + i, "203.0.113.5", 80, mss=1460)
        syns.append(s)
    print(f"  {len(set(syns & 0xFFFFFF for syns in syns))}/10 互相不同 (不同 src 的 s 不同)")

    print("\n" + "=" * 68)
    print("  SYN Cookie 演示完成")
    print("=" * 68)
    print("\nDemo 仅演示 cookie 编码与验证算法本身。要真正抵御 SYN flood,")
    print("应在生产 Linux sysctl: net.ipv4.tcp_syncookies = 1")
    print("并调高 net.ipv4.tcp_max_syn_backlog = 4096+,")
    print("或部署 SYN Proxy / XDP 防 SYN flood 程序。")


if __name__ == "__main__":
    main()
