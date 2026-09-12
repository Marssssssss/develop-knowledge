// redis_cluster.go — Redis Cluster 核心机制最小仿真
//
// 演示:
//   1) CRC16/XMODEM → HASH_SLOT = CRC16(key) % 16384
//   2) Hash Tag "{tag}" → 同槽
//   3) 16384 槽均分到 N master
//   4) Gossip ping 包(头部 + N 个 gossip 项)
//   5) Replica 故障转移:多数票 + configEpoch 单调递增
//
// 运行: go run redis_cluster.go

package main

import (
	"fmt"
	"math/rand"
	"time"
)

const slotsTotal = 16384

// ---- CRC16/XMODEM ----

var crc16tab = [256]uint16{
	0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
	0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef,
	0x1231, 0x0210, 0x3273, 0x2252, 0x52b5, 0x4294, 0x72f7, 0x62d6,
	0x9339, 0x8318, 0xb37b, 0xa35a, 0xd3bd, 0xc39c, 0xf3ff, 0xe3de,
	0x2462, 0x3443, 0x0420, 0x1401, 0x64e6, 0x74c7, 0x44a4, 0x5485,
	0xa56a, 0xb54b, 0x8528, 0x9509, 0xe5ee, 0xf5cf, 0xc5ac, 0xd58d,
	0x3653, 0x2672, 0x1611, 0x0630, 0x76d7, 0x66f6, 0x5695, 0x46b4,
	0xb75b, 0xa77a, 0x9719, 0x8738, 0xf7df, 0xe7fe, 0xd79d, 0xc7bc,
	0x48c4, 0x58e5, 0x6886, 0x78a7, 0x0840, 0x1861, 0x2802, 0x3823,
	0xc9cc, 0xd9ed, 0xe98e, 0xf9af, 0x8948, 0x9969, 0xa90a, 0xb92b,
	0x5af5, 0x4ad4, 0x7ab7, 0x6a96, 0x1a71, 0x0a50, 0x3a33, 0x2a12,
	0xdbfd, 0xcbdc, 0xfbbf, 0xeb9e, 0x9b79, 0x8b58, 0xbb3b, 0xab1a,
	0x6ca6, 0x7c87, 0x4ce4, 0x5dc5, 0x2c22, 0x3c03, 0x0c60, 0x1c41,
	0xedae, 0xfd8f, 0xcdec, 0xddcd, 0xad2a, 0xbd0b, 0x8d68, 0x9d49,
	0x7e97, 0x6eb6, 0x5ed5, 0x4ef4, 0x3e13, 0x2e32, 0x1e51, 0x0e70,
	0xff9f, 0xefbe, 0xdfdd, 0xcffc, 0xbf1b, 0xaf3a, 0x9f59, 0x8f78,
	0x9188, 0x81a9, 0xb1ca, 0xa1eb, 0xd10c, 0xc12d, 0xf14e, 0xe16f,
	0x1080, 0x00a1, 0x30c2, 0x20e3, 0x5004, 0x4025, 0x7046, 0x6067,
	0x83b9, 0x9398, 0xa3fb, 0xb3da, 0xc33d, 0xd31c, 0xe37f, 0xf35e,
	0x02b1, 0x1290, 0x22f3, 0x32d2, 0x4235, 0x5214, 0x6277, 0x7256,
	0xb5ea, 0xa5cb, 0x95a8, 0x8589, 0xf56e, 0xe54f, 0xd52c, 0xc50d,
	0x34e2, 0x24c3, 0x14a0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
	0xa7db, 0xb7fa, 0x8799, 0x97b8, 0xe75f, 0xf77e, 0xc71d, 0xd73c,
	0x26d3, 0x36f2, 0x0691, 0x16b0, 0x6657, 0x7676, 0x4615, 0x5634,
	0xd94c, 0xc96d, 0xf90e, 0xe92f, 0x99c8, 0x89e9, 0xb98a, 0xa9ab,
	0x5844, 0x4865, 0x7806, 0x6827, 0x18c0, 0x08e1, 0x3882, 0x28a3,
	0xcb7d, 0xdb5c, 0xeb3f, 0xfb1e, 0x8bf9, 0x9dd8, 0xabbb, 0xbb9a,
	0x4a75, 0x5a54, 0x6a37, 0x7a16, 0x0af1, 0x1ad0, 0x2ab3, 0x3a92,
	0xfd2e, 0xed0f, 0xdd6c, 0xcd4d, 0xbdaa, 0xad8b, 0x9de8, 0x8dc9,
	0x7c26, 0x6c07, 0x5c64, 0x4c45, 0x3ca2, 0x2c83, 0x1ce0, 0x0cc1,
	0xef1f, 0xff3e, 0xcf5d, 0xdf7c, 0xaf9b, 0xbfba, 0x8fd9, 0x9ff8,
	0x6e17, 0x7e36, 0x4e55, 0x5e74, 0x2e93, 0x3eb2, 0x0ed1, 0x1ef0,
}

func crc16(buf []byte) uint16 {
	crc := uint16(0)
	for _, b := range buf {
		crc = (crc << 8) ^ crc16tab[((crc>>8)^uint16(b))&0xFF]
	}
	return crc
}

// ---- HASH_SLOT ----

func hashSlot(key string) int {
	b := []byte(key)
	s := -1
	for i, c := range b {
		if c == '{' {
			s = i
			break
		}
	}
	if s >= 0 {
		e := -1
		for j := s + 1; j < len(b); j++ {
			if b[j] == '}' {
				e = j
				break
			}
		}
		if e > s+1 {
			return int(crc16(b[s+1 : e])) & (slotsTotal - 1)
		}
	}
	return int(crc16(b)) & (slotsTotal - 1)
}

// ---- 16384 槽分配 ----

func assignSlots(masters []string) []string {
	owner := make([]string, slotsTotal)
	n := len(masters)
	per := slotsTotal / n
	rem := slotsTotal % n
	cursor := 0
	for i, m := range masters {
		span := per
		if i < rem {
			span++
		}
		for s := cursor; s < cursor+span; s++ {
			owner[s] = m
		}
		fmt.Printf("Master '%s' serves slots [%d, %d)\n",
			m, cursor, cursor+span)
		cursor += span
	}
	return owner
}

// ---- Gossip ----

type GossipNode struct {
	NodeID string
	IP     string
	Port   uint16
	Flags  uint8
}

type PingPacket struct {
	SenderID      string
	CurrentEpoch  uint64
	ConfigEpoch   uint64
	Flags         uint8
	Gossips       []GossipNode
}

func gossipTick(selfID string, known []string, seed int64) PingPacket {
	rng := rand.New(rand.NewSource(seed))
	ng := len(known) / 10
	if ng < 1 {
		ng = 1
	}
	if ng > len(known) {
		ng = len(known)
	}
	perm := rng.Perm(len(known))
	gossips := make([]GossipNode, ng)
	for i := 0; i < ng; i++ {
		gossips[i] = GossipNode{
			NodeID: known[perm[i]],
			IP:     fmt.Sprintf("10.0.0.%d", i+2),
			Port:   uint16(7000 + i),
			Flags:  0,
		}
	}
	return PingPacket{
		SenderID:     selfID,
		CurrentEpoch: 1,
		ConfigEpoch:  1,
		Gossips:      gossips,
	}
}

// ---- 故障转移 ----

type Replica struct {
	MasterID     string
	ConfigEpoch  uint64
	CurrentEpoch uint64
}

func tryFailover(r *Replica, masters []string) bool {
	r.CurrentEpoch++
	// demo 简化为全票通过
	if len(masters) > len(masters)/2 {
		r.ConfigEpoch++
		return true
	}
	return false
}

// ---- main ----

func main() {
	fmt.Println("=== Redis Cluster Demo (Go) ===\n")

	// 1) 槽分配
	fmt.Println("[Slot assignment - 3 masters]\n")
	masters := []string{"master:A", "master:B", "master:C"}
	owner := assignSlots(masters)

	// 2) key → slot
	fmt.Println("\n[Key → slot mapping]\n")
	keys := []string{
		"user:1001",
		"{user1000}.following",
		"{user1000}.followers",
		"foo",
		"bar",
	}
	for _, k := range keys {
		s := hashSlot(k)
		fmt.Printf("  %-25s → slot %5d  (owner: %s)\n",
			k, s, owner[s])
	}

	// 3) Gossip ping
	fmt.Println("\n[Gossip ping packet]\n")
	known := []string{
		"node-B-id", "node-C-id", "node-D-id", "node-E-id",
		"node-F-id", "node-G-id", "node-H-id",
	}
	pkt := gossipTick("node-A-id", known, time.Now().UnixNano())
	fmt.Printf("  sender  = %s\n", pkt.SenderID)
	fmt.Printf("  epoch   = %d\n", pkt.CurrentEpoch)
	fmt.Printf("  gossips = %d items\n", len(pkt.Gossips))
	for i, g := range pkt.Gossips {
		fmt.Printf("    [%d] %s @ %s:%d\n", i, g.NodeID, g.IP, g.Port)
	}

	// 4) 故障转移
	fmt.Println("\n[Replica failover]\n")
	r := Replica{MasterID: "master:A"}
	if tryFailover(&r, masters) {
		fmt.Printf("  replica of %s won election, new configEpoch = %d\n",
			r.MasterID, r.ConfigEpoch)
	}
	fmt.Printf("\nDemo finished at %s\n",
		time.Now().Format("2006-01-02 15:04:05"))
}