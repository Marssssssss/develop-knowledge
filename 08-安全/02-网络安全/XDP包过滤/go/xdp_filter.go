// eBPF / XDP 包过滤 — Go 用户态 loader 模拟
//
// 演示控制平面的接口:
//   - 加载"BPF"程序(实际我们在 Go 里直接模拟判决函数)
//   - 写黑名单进"Map"
//   - 模拟 packet 流过 XDP 程序
//
// 真实场景用 cilium/ebpf(github.com/cilium/ebpf)库加载 BPF 字节码,
//   本 demo 着重演示 control plane 流程,不涉及 verifier 加载。
//
// 运行: go run xdp_filter.go

package main

import (
	"encoding/binary"
	"fmt"
	"math/rand"
	"net"
	"os"
	"time"
)

const (
	XDPActionDrop    = 1
	XDPActionPass    = 2
	XDPActionTx      = 3
	XDPActionRedirect = 4
)

var xdpActionNames = map[int]string{
	XDPActionDrop: "XDP_DROP",
	XDPActionPass: "XDP_PASS",
	XDPActionTx:   "XDP_TX",
	XDPActionRedirect: "XDP_REDIRECT",
}

// ============================================================
// 模拟 BPF Map Type: HASH (key = u32 IPv4 src BE, value = hit counter)
// ============================================================

type BlacklistEntry struct {
	IP    uint32 // network byte order
	Hits  uint64
}

type XDPController struct {
	ifname     string
	blacklist  map[uint32]*BlacklistEntry
	stats      map[string]uint64
	attached   bool
}

// IPv4 字符串 → network byte order u32
func ip2int(ip net.IP) uint32 {
	return binary.BigEndian.Uint32(ip.To4())
}

func newXDPController(ifname string) *XDPController {
	return &XDPController{
		ifname:    ifname,
		blacklist: make(map[uint32]*BlacklistEntry),
		stats: map[string]uint64{
			"xdp_drop": 0, "xdp_pass": 0,
			"xdp_tx": 0, "xdp_redirect": 0, "xdp_abort": 0,
		},
	}
}

// 模拟 bpf_set_link_xdp_fd
func (c *XDPController) Attach(mode string) {
	if mode != "native" && mode != "generic" && mode != "offloaded" {
		fmt.Println("  invalid mode (native/generic/offloaded)")
		os.Exit(1)
	}
	c.attached = true
	fmt.Printf("  → attached to %s (mode=%s) [simulated; real → bpf_set_link_xdp_fd]\n",
		c.ifname, mode)
}

// 模拟 bpf_map_lookup_elem + bpf_map_update_elem (BPF_ANY)
func (c *XDPController) AddBlacklist(ipStr string) {
	ip := net.ParseIP(ipStr).To4()
	if ip == nil {
		fmt.Printf("  invalid IP: %s\n", ipStr)
		return
	}
	key := ip2int(ip)
	if _, exists := c.blacklist[key]; exists {
		return
	}
	c.blacklist[key] = &BlacklistEntry{IP: key, Hits: 0}
	fmt.Printf("  → blacklisted %s\n", ipStr)
}

func (c *XDPController) RemoveBlacklist(ipStr string) {
	ip := net.ParseIP(ipStr).To4()
	if ip == nil {
		return
	}
	delete(c.blacklist, ip2int(ip))
}

func (c *XDPController) Detach() {
	if !c.attached {
		return
	}
	c.attached = false
	fmt.Printf("  → detached from %s\n", c.ifname)
}

// 模拟 XDP 程序做判决:对 IPv4 src IP 查 blacklist_map
func (c *XDPController) ProcessPacket(srcIPStr string) int {
	ip := net.ParseIP(srcIPStr).To4()
	if ip == nil {
		c.stats["xdp_pass"]++
		return XDPActionPass
	}
	key := ip2int(ip)
	if entry, exists := c.blacklist[key]; exists {
		entry.Hits++
		c.stats["xdp_drop"]++
		return XDPActionDrop
	}
	c.stats["xdp_pass"]++
	return XDPActionPass
}

func (c *XDPController) Show() {
	total := uint64(0)
	for _, v := range c.stats {
		total += v
	}
	fmt.Printf("    Total = %d\n", total)
	for action, count := range c.stats {
		pct := 0.0
		if total > 0 {
			pct = 100 * float64(count) / float64(total)
		}
		fmt.Printf("    %-15s: %6d (%5.1f%%)\n", action, count, pct)
	}
	fmt.Println("    Hit detail:")
	for ip, entry := range c.blacklist {
		ipByte := make([]byte, 4)
		binary.BigEndian.PutUint32(ipByte, ip)
		fmt.Printf("      %d.%d.%d.%d  blocked: %d packets\n",
			ipByte[0], ipByte[1], ipByte[2], ipByte[3], entry.Hits)
	}
}

// ============================================================
// 模拟 BPF 程序:解析 IPv4 头 + 查 map → 判决
// ============================================================

// runXDPFilterProgram 等价于 BPF .o 字节码加载后在内核执行的逻辑:
//   1) bounds check → 2) parse IPv4 src → 3) bpf_map_lookup_elem → 4) return action
//
// 真实场景这一步在网卡 RX 路径,~60ns/pkt
func runXDPFilterProgram(ctrl *XDPController, packet []byte) int {
	if len(packet) < 20 {
		return XDPActionPass
	}
	if (packet[0] >> 4) != 4 { // version != 4
		return XDPActionPass
	}
	srcIP := fmt.Sprintf("%d.%d.%d.%d", packet[12], packet[13], packet[14], packet[15])
	return ctrl.ProcessPacket(srcIP)
}

// ============================================================
// Demo
// ============================================================

func main() {
	fmt.Println("================================================================")
	fmt.Println(" eBPF / XDP 包过滤 — Go 用户态 controller 演示")
	fmt.Println("================================================================")

	ctrl := newXDPController("eth0")

	// (1) 加载 BPF .o + attach (模拟)
	fmt.Println("\n[Step 1] 加载 xdp_filter.bpf.o (cilium/ebpf 视角):")
	fmt.Println("  → collection := ebpf.LoadCollectionSpec(\"xdp_filter.bpf.o\")")
	fmt.Println("  → coll.Assign() — load BPF program(s) to kernel")
	fmt.Println("  → iface.LinkXDP(coll.Programs[\"xdp_drop_blacklist\"])")
	ctrl.Attach("native")

	// (2) 写黑名单 (bpf_map_update_elem)
	fmt.Println("\n[Step 2] 写黑名单到 BPF Hash Map:")
	for _, ip := range []string{"203.0.113.66", "198.51.100.7", "203.0.113.99"} {
		ctrl.AddBlacklist(ip)
	}

	// (3) 模拟 100 万 packet
	fmt.Println("\n[Step 3] 100 万 packet 流过 XDP filter(模拟,10% 命中黑名单):")
	ctrl.stats["xdp_drop"] = 100_000
	ctrl.stats["xdp_pass"] = 900_000
	per := ctrl.stats["xdp_drop"] / uint64(len(ctrl.blacklist))
	for _, entry := range ctrl.blacklist {
		entry.Hits = per
	}

	// (4) 抽样跑 5 个 IPv4 packet 走 BPF 程序
	fmt.Println("\n[Step 4] 抽样跑 5 个 IPv4 packet 走 BPF 程序:")
	samples := [][]byte{
		// IPv4 src 198.51.100.7 → 命中黑名单 → DROP
		{0x45, 0x00, 0x00, 0x3c, 0x1c, 0x46, 0x40, 0x00,
			0x40, 0x06, 0xb1, 0xe6, 0xc6, 0x33, 0x64, 0x07,
			0xac, 0x10, 0x0a, 0x0a},
		// IPv4 src 10.0.0.1 → 不命中 → PASS
		{0x45, 0x00, 0x00, 0x3c, 0x1c, 0x46, 0x40, 0x00,
			0x40, 0x06, 0xb1, 0xe6, 0x0a, 0x00, 0x00, 0x01,
			0xac, 0x10, 0x0a, 0x0a},
		// IPv4 src 203.0.113.66 → 命中 → DROP
		{0x45, 0x00, 0x00, 0x3c, 0x1c, 0x46, 0x40, 0x00,
			0x40, 0x06, 0xb1, 0xe6, 0xcb, 0x00, 0x71, 0x42,
			0xac, 0x10, 0x0a, 0x0a},
	}
	for i, pkt := range samples {
		action := runXDPFilterProgram(ctrl, pkt)
		fmt.Printf("  Pkt %d: src = %d.%d.%d.%d → %s\n",
			i, pkt[12], pkt[13], pkt[14], pkt[15],
			xdpActionNames[action])
	}

	fmt.Println("\n[Step 5] XDP 程序运行统计:")
	ctrl.Show()

	// (5) 卸载
	fmt.Println("\n[Step 6] 卸载 XDP 程序:")
	fmt.Println("  → iface.UnlinkXDP()")
	ctrl.Detach()

	fmt.Println("\n================================================================")
	fmt.Println("  eBPF / XDP 演示完成 ✓")
	fmt.Println("================================================================")
	_ = rand.New(rand.NewSource(time.Now().UnixNano()))
}
