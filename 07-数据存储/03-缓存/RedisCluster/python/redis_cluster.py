"""
redis_cluster.py — Redis Cluster 核心机制最小仿真(纯标准库)

演示:
  1) CRC16/XMODEM → HASH_SLOT = CRC16(key) & 16383
  2) Hash Tag "{tag}" 把多键映射到同槽
  3) 16384 槽均分到 N master
  4) Gossip ping 包(头部 + 1/10 已知节点)
  5) Replica 故障转移:多数票 + configEpoch

运行: python3 redis_cluster.py
"""

from __future__ import annotations

import random
import struct
import sys
import time
from dataclasses import dataclass, field

# ---------- CRC16/XMODEM 表(与 Redis 源码同源) ----------

_CRC16_TAB = [
    0x0000,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7,
    0x8108,0x9129,0xa14a,0xb16b,0xc18c,0xd1ad,0xe1ce,0xf1ef,
    0x1231,0x0210,0x3273,0x2252,0x52b5,0x4294,0x72f7,0x62d6,
    0x9339,0x8318,0xb37b,0xa35a,0xd3bd,0xc39c,0xf3ff,0xe3de,
    0x2462,0x3443,0x0420,0x1401,0x64e6,0x74c7,0x44a4,0x5485,
    0xa56a,0xb54b,0x8528,0x9509,0xe5ee,0xf5cf,0xc5ac,0xd58d,
    0x3653,0x2672,0x1611,0x0630,0x76d7,0x66f6,0x5695,0x46b4,
    0xb75b,0xa77a,0x9719,0x8738,0xf7df,0xe7fe,0xd79d,0xc7bc,
    0x48c4,0x58e5,0x6886,0x78a7,0x0840,0x1861,0x2802,0x3823,
    0xc9cc,0xd9ed,0xe98e,0xf9af,0x8948,0x9969,0xa90a,0xb92b,
    0x5af5,0x4ad4,0x7ab7,0x6a96,0x1a71,0x0a50,0x3a33,0x2a12,
    0xdbfd,0xcbdc,0xfbbf,0xeb9e,0x9b79,0x8b58,0xbb3b,0xab1a,
    0x6ca6,0x7c87,0x4ce4,0x5dc5,0x2c22,0x3c03,0x0c60,0x1c41,
    0xedae,0xfd8f,0xcdec,0xddcd,0xad2a,0xbd0b,0x8d68,0x9d49,
    0x7e97,0x6eb6,0x5ed5,0x4ef4,0x3e13,0x2e32,0x1e51,0x0e70,
    0xff9f,0xefbe,0xdfdd,0xcffc,0xbf1b,0xaf3a,0x9f59,0x8f78,
    0x9188,0x81a9,0xb1ca,0xa1eb,0xd10c,0xc12d,0xf14e,0xe16f,
    0x1080,0x00a1,0x30c2,0x20e3,0x5004,0x4025,0x7046,0x6067,
    0x83b9,0x9398,0xa3fb,0xb3da,0xc33d,0xd31c,0xe37f,0xf35e,
    0x02b1,0x1290,0x22f3,0x32d2,0x4235,0x5214,0x6277,0x7256,
    0xb5ea,0xa5cb,0x95a8,0x8589,0xf56e,0xe54f,0xd52c,0xc50d,
    0x34e2,0x24c3,0x14a0,0x0481,0x7466,0x6447,0x5424,0x4405,
    0xa7db,0xb7fa,0x8799,0x97b8,0xe75f,0xf77e,0xc71d,0xd73c,
    0x26d3,0x36f2,0x0691,0x16b0,0x6657,0x7676,0x4615,0x5634,
    0xd94c,0xc96d,0xf90e,0xe92f,0x99c8,0x89e9,0xb98a,0xa9ab,
    0x5844,0x4865,0x7806,0x6827,0x18c0,0x08e1,0x3882,0x28a3,
    0xcb7d,0xdb5c,0xeb3f,0xfb1e,0x8bf9,0x9dd8,0xabbb,0xbb9a,
    0x4a75,0x5a54,0x6a37,0x7a16,0x0af1,0x1ad0,0x2ab3,0x3a92,
    0xfd2e,0xed0f,0xdd6c,0xcd4d,0xbdaa,0xad8b,0x9de8,0x8dc9,
    0x7c26,0x6c07,0x5c64,0x4c45,0x3ca2,0x2c83,0x1ce0,0x0cc1,
    0xef1f,0xff3e,0xcf5d,0xdf7c,0xaf9b,0xbfba,0x8fd9,0x9ff8,
    0x6e17,0x7e36,0x4e55,0x5e74,0x2e93,0x3eb2,0x0ed1,0x1ef0,
]


def crc16(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_TAB[((crc >> 8) ^ b) & 0xFF]
    return crc


# ---------- HASH_SLOT + Hash Tag ----------

SLOTS = 16384


def hash_slot(key: str) -> int:
    """支持 {tag}: 仅对花括号内非空内容计算 CRC16。"""
    b = key.encode()
    s = b.find(b"{")
    if s >= 0:
        e = b.find(b"}", s + 1)
        if e > s + 1:                       # {...} 至少 1 字符
            return crc16(b[s + 1:e]) & (SLOTS - 1)
    return crc16(b) & (SLOTS - 1)


# ---------- 16384 槽分配 ----------

def assign_slots(masters: list[str]) -> dict[int, str]:
    n = len(masters)
    per, rem = divmod(SLOTS, n)
    owner: dict[int, str] = {}
    cursor = 0
    for i, m in enumerate(masters):
        span = per + (1 if i < rem else 0)
        for s in range(cursor, cursor + span):
            owner[s] = m
        cursor += span
        print(f"Master '{m}' serves slots [{cursor - span}, {cursor})")
    return owner


# ---------- Gossip ----------

@dataclass
class GossipNode:
    node_id: str
    ip: str
    port: int
    flags: int = 0


@dataclass
class PingPacket:
    sender_id: str
    current_epoch: int = 0
    config_epoch: int = 0
    flags: int = 0
    gossips: list[GossipNode] = field(default_factory=list)


def gossip_tick(self_id: str, known: list[str], seed: int = 0) -> PingPacket:
    """生成 ping 包:取 max(1, n//10) 个 gossip 项。"""
    rng = random.Random(seed) if seed else random
    ng = max(1, len(known) // 10)
    ng = min(ng, 16)
    sample = rng.sample(known, ng) if len(known) >= ng else known
    return PingPacket(
        sender_id=self_id,
        current_epoch=1,
        config_epoch=1,
        gossips=[GossipNode(nid, f"10.0.0.{i+2}", 7000 + i)
                 for i, nid in enumerate(sample)],
    )


# ---------- 故障转移 ----------

@dataclass
class Replica:
    master_id: str
    config_epoch: int = 0
    current_epoch: int = 0


def try_failover(r: Replica, masters: list[str]) -> bool:
    r.current_epoch += 1
    votes = len(masters)                 # demo:全部同意首个请求
    if votes > len(masters) // 2:
        r.config_epoch += 1
        return True
    return False


# ---------- 入口 ----------

def main() -> int:
    print("=== Redis Cluster Demo (Python) ===\n")

    # 1) 槽分配
    print("[Slot assignment - 3 masters]\n")
    masters = ["master:A", "master:B", "master:C"]
    owner = assign_slots(masters)

    # 2) key → slot
    print("\n[Key → slot mapping]\n")
    keys = [
        "user:1001",
        "{user1000}.following",
        "{user1000}.followers",
        "foo",
        "bar",
    ]
    for k in keys:
        s = hash_slot(k)
        print(f"  {k!r:25s} → slot {s:5d}  (owner: {owner[s]})")

    # 3) Gossip ping
    print("\n[Gossip ping packet]\n")
    known = ["node-B-id", "node-C-id", "node-D-id", "node-E-id",
             "node-F-id", "node-G-id", "node-H-id"]
    pkt = gossip_tick("node-A-id", known, seed=42)
    print(f"  sender    = {pkt.sender_id}")
    print(f"  epoch     = {pkt.current_epoch}")
    print(f"  gossips   = {len(pkt.gossips)} items")
    for i, g in enumerate(pkt.gossips):
        print(f"    [{i}] {g.node_id} @ {g.ip}:{g.port}")

    # 4) 故障转移
    print("\n[Replica failover]\n")
    r = Replica(master_id="master:A")
    if try_failover(r, masters):
        print(f"  replica of {r.master_id} won election, "
              f"new configEpoch = {r.config_epoch}")

    print(f"\nDemo finished at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())