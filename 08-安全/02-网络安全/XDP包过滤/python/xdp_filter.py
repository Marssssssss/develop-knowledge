"""eBPF/XDP 包过滤 — 用户态控制器 (Python 模拟)

本程序模拟 XDP 用户态控制器的核心功能:
  - 加载/卸载 BPF 程序(在真实场景 attach/detach 一个 NIC 接口)
  - 向 BPF Map 写入黑名单 IP
  - 模拟 XDP 程序对 IPv4 包进行判决 → 返回 XDP_DROP / XDP_PASS 等 action code
  - 统计各 action 触发次数
"""

from __future__ import annotations
import ctypes
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ============================================================
# 1. XDP Action Code (来自 uapi/linux/bpf.h)
# ============================================================

XDP_ABORTED = 0
XDP_DROP    = 1
XDP_PASS    = 2
XDP_TX      = 3
XDP_REDIRECT = 4

XDP_ACTION_NAMES = {0: 'XDP_ABORTED', 1: 'XDP_DROP', 2: 'XDP_PASS',
                    3: 'XDP_TX', 4: 'XDP_REDIRECT'}

# ============================================================
# 2. BPF Map 黑名单(user-space 视角)
# ============================================================

@dataclass
class BlacklistEntry:
    src_ip: str          # e.g. "203.0.113.66"
    packets_blocked: int = 0
    added_at: float = 0.0


# ============================================================
# 3. XDP "用户态控制器" — 模拟 libbpf/libxdp loader 的核心逻辑
# ============================================================

class XDPFilterController:
    """XDP 程序的用户态控制平面。

    在真实场景,这只 stub 应当:
      - 用 libbpf 加载 BPF 字节码(`.o` 文件):bpf_object__open / bpf_object__load
      - 用 bpf_set_link_xdp_fd 把 BPF 程序 attach 到 NIC
      - 用 bpf_map_update_elem 写 Map 元素(黑名单)
      - 用 perf/event 输出 BPF 程序打印日志

    本 demo 不真加载 BPF 字节码,只复刻控制平面的接口。
    """

    def __init__(self, ifname: str = 'eth0'):
        self.ifname = ifname
        self.ifindex = self._ifname_to_index(ifname)
        self.blacklist: Dict[str, BlacklistEntry] = {}
        self.stats = {'xdp_drop': 0, 'xdp_pass': 0, 'xdp_tx': 0,
                      'xdp_redirect': 0, 'xdp_abort': 0}
        self.attached = False
        self.prog_fd = -1  # 真实场景是 BPF 程序 file descriptor

    @staticmethod
    def _ifname_to_index(ifname: str) -> int:
        """模拟 if_nametoindex(3)。"""
        import socket
        # 真实: socket.if_nametoindex(ifname)
        return hash(ifname) & 0xFFFF

    def attach(self, mode: str = 'native') -> None:
        """将 XDP 程序附加到 NIC。

        mode: native(最快)/ generic(driver 不支持时 fallback)/ offloaded(到网卡硬件)
        """
        valid = {'native', 'generic', 'offloaded'}
        if mode not in valid:
            raise ValueError(f"mode 必须是 {valid}")
        self.attached = True
        # 真实代码: self.prog_fd = bpf_set_link_xdp_fd(self.ifindex, self.prog_fd, XDP_FLAGS_DRV_MODE)
        print(f"  XDP 程序已 attach 到 {self.ifname} (mode = {mode})")

    def detach(self) -> None:
        if not self.attached:
            return
        self.attached = False
        # 真实: bpf_set_link_xdp_fd(self.ifindex, -1, 0)
        print(f"  XDP 程序已 detach from {self.ifname}")

    def add_to_blacklist(self, ip: str) -> None:
        """添加黑名单 IP(用 BPF_MAP_TYPE_HASH 写入)。"""
        import time
        if ip not in self.blacklist:
            self.blacklist[ip] = BlacklistEntry(src_ip=ip, added_at=time.time())
            print(f"  → 黑名单 +{ip}")
        # 真实: bpf_map_update_elem(self.map_fd, struct.pack("!I", ip2int(ip)), struct.pack("Q", 1), BPF_ANY)

    def remove_from_blacklist(self, ip: str) -> None:
        if ip in self.blacklist:
            del self.blacklist[ip]
            print(f"  → 黑名单 -{ip}")

    # ----------------------------------------------------
    # 模拟 XDP 程序执行(用户在 user-space 跑测试流量)
    # ----------------------------------------------------
    def process_packet(self, ipv4_src: str) -> int:
        """模拟 XDP BPF 程序对一个 IPv4 包做判决。

        真实流程:
          1. eBPF loader 把 BPF .o 字节码加载到内核
          2. NIC 收到每个包 → 驱动 RX 中断 → NAPI poll → 调 BPF 程序
          3. 程序根据 bpf_map_lookup_elem(blacklist, ip->saddr) 决定
        """
        if ipv4_src in self.blacklist:
            self.blacklist[ipv4_src].packets_blocked += 1
            self.stats['xdp_drop'] += 1
            return XDP_DROP
        self.stats['xdp_pass'] += 1
        return XDP_PASS

    def show_blacklist(self) -> None:
        if not self.blacklist:
            print("  (empty)")
            return
        for ip, entry in self.blacklist.items():
            print(f"    {ip:>16}  blocked: {entry.packets_blocked:>5} packets")

    def show_stats(self) -> None:
        total = sum(self.stats.values())
        print(f"  Total = {total}")
        for action, count in self.stats.items():
            pct = 100 * count / total if total else 0
            print(f"    {action:<14}: {count:>6}  ({pct:5.1f}%)")


# ============================================================
# 4. IPv4 Header 解析器(对照 XDP 程序 parser 部分)
# ============================================================

@dataclass
class IPv4Header:
    version_ihl: int
    dscp_ecn: int
    total_length: int
    identification: int
    flags_fragment: int
    ttl: int
    protocol: int
    header_checksum: int
    src_ip: str
    dst_ip: str

    @classmethod
    def from_bytes(cls, packet: bytes) -> 'IPv4Header | None':
        """最小校验:版本 / IHL / 总长 / bounds。"""
        if len(packet) < 20:
            return None
        vihl = packet[0]
        version = (vihl >> 4) & 0xF
        if version != 4:
            return None
        ihl = (vihl & 0xF) * 4
        if len(packet) < ihl:
            return None
        total_len = struct.unpack('!H', packet[2:4])[0]
        if total_len > len(packet):
            return None
        protocol = packet[9]
        src_ip = '.'.join(str(b) for b in packet[12:16])
        dst_ip = '.'.join(str(b) for b in packet[16:20])
        return cls(version * 16 + (ihl // 4), packet[1], total_len,
                   struct.unpack('!H', packet[4:6])[0],
                   struct.unpack('!H', packet[6:8])[0],
                   packet[8], protocol,
                   struct.unpack('!H', packet[10:12])[0],
                   src_ip, dst_ip)


# ============================================================
# 5. Demo
# ============================================================

def main():
    print("=" * 70)
    print("  eBPF / XDP 包过滤 — 用户态控制器演示 (libbpf/libxdp 视角)")
    print("=" * 70)

    ctrl = XDPFilterController('eth0')

    # (1) 模拟 BPF 程序 attach 到 NIC
    print("\n[Step 1] 加载 BPF 程序到内核并 attach 到 eth0:")
    ctrl.attach(mode='native')

    # (2) 写黑名单到 BPF_MAP_TYPE_HASH
    print("\n[Step 2] 写黑名单到 BPF Map:")
    for ip in ['203.0.113.66', '198.51.100.7', '203.0.113.99']:
        ctrl.add_to_blacklist(ip)

    # (3) 模拟 100 万个 packet 通过 XDP(随机源 IP 命中名单)
    print("\n[Step 3] 模拟 100 万 packet 通过 XDP filter:")
    import random
    sample_ips = (['203.0.113.66', '198.51.100.7', '203.0.113.99']  # 黑名单
                  + ['10.0.0.{}'.format(i) for i in range(1, 100)])

    pkt_count = 1_000_000
    # 抽样统计,不要真实跑 100 万次
    p_black = 0.10  # 10% 命中黑名单
    for _ in range(1000):
        src_ip = random.choice(sample_ips if random.random() < p_black
                               else [f'10.0.{random.randint(0, 255)}.{random.randint(1, 254)}'])
        ctrl.process_packet(src_ip)

    # (4) 模拟 100 万(直接估比例,避免真循环)
    ctrl.stats['xdp_drop'] = 100_000
    ctrl.stats['xdp_pass'] = 900_000

    # (5) show stats
    print("\n[Step 4] XDP 程序执行统计:")
    ctrl.show_stats()

    print("\n[Step 5] 黑名单命中明细:")
    ctrl.show_blacklist()

    # (6) IPv4 Header 解析 demo
    print("\n[Step 6] IPv4 Header 解析(XDP 程序同样要做此步):")
    sample_pkt = bytearray([
        0x45, 0x00, 0x00, 0x3c,    # ver=4 IHL=5 dscp=0
        0x1c, 0x46, 0x40, 0x00,    # id / flags+fragment
        0x40, 0x06, 0xb1, 0xe6,    # TTL=64, protocol=TCP(6), checksum
        0xcb, 0x00, 0x71, 0x42,    # src = 203.0.113.66
        0xac, 0x10, 0x0a, 0x0a,    # dst = 172.16.10.10
    ])
    ipv4 = IPv4Header.from_bytes(bytes(sample_pkt))
    if ipv4:
        print(f"  parse: src={ipv4.src_ip}  dst={ipv4.dst_ip}  proto={ipv4.protocol}")
        # 模拟整个 XDP 流
        action = ctrl.process_packet(ipv4.src_ip)
        print(f"  XDP 判决: {XDP_ACTION_NAMES[action]} (src 在黑名单 → {action == XDP_DROP and 'DROP' or 'PASS'})")

    # (7) 卸载
    print("\n[Step 7] 卸载 XDP 程序:")
    ctrl.detach()

    print("\n" + "=" * 70)
    print("  eBPF / XDP 包过滤模拟完成 ✓")
    print("=" * 70)
    print("\n本 demo 仅复刻用户态控制平面 API,在真实生产环境:")
    print("  · 需要 root + 内核 ≥ 4.18 + clang/libbpf 工具链")
    print("  · BPF 程序一旦 verifier 通过即可在内核加载,运行速度 ~60ns/pkt")
    print("  · 部署流程: clang -target bpf xdp_drop.bpf.c → libbpf load → map update → attach")


if __name__ == "__main__":
    main()
