package main

// Dynamo 风格 key-value 存储 — Go 实现核心机制:
//   1. 一致性哈希 + 虚节点(virtual nodes)
//   2. 偏好列表(preference list) — 从 key 选 N 个目标节点
//   3. 用 channel + map 模拟 hinted handoff 队列
//
// 用法:
//   go run dynamo.go
//   按提示输入命令:
//     put  <key> <value>
//     get  <key>
//     pref <key>           — 打印 preference list
//     hint                — 打印当前 hint 队列
//     replay <node>       — 模拟 node 上线,重放它的 hints
//     quit
//
// Go 标准库无 CRC32 多项式表,改用 FNV-1a 32-bit(常见替代,hash 分布也很好)。

import (
	"bufio"
	"fmt"
	"hash/fnv"
	"os"
	"sort"
	"strings"
)

const (
	ringSize     = 256
	physNodes    = 6
	replicaN     = 3
	hintQueueMax = 32
)

type vnode struct {
	pos     uint32
	vnodeID int
	nodeID  int
}

type physNode struct {
	id      int
	name    string
	nVnodes int
}

// ring + nodes
var (
	ring  [ringSize]vnode
	nodes [physNodes]physNode
)

type hint struct {
	Owner string // 本该送到哪个节点
	Key   string
	Value string
}

var (
	hintQueue []hint
)

// FNV-1a 32-bit hash (等价于 Go 标准库 hash/fnv 的 New32a)
func hash32(s string) uint32 {
	h := fnv.New32a()
	_, _ = h.Write([]byte(s))
	return h.Sum32()
}

func buildRing() {
	names := []string{"node-A", "node-B", "node-C", "node-D", "node-E", "node-F"}
	vPerNode := ringSize / physNodes
	for i := 0; i < physNodes; i++ {
		nodes[i] = physNode{id: i, name: names[i], nVnodes: vPerNode}
	}
	v := 0
	for i := 0; i < physNodes; i++ {
		for k := 0; k < nodes[i].nVnodes; k++ {
			name := fmt.Sprintf("%s#vnode-%d", nodes[i].name, k)
			ring[v] = vnode{
				pos:     hash32(name),
				vnodeID: k,
				nodeID:  i,
			}
			v++
		}
	}
	sort.Slice(ring[:], func(i, j int) bool { return ring[i].pos < ring[j].pos })
}

// preference list:沿 ring 顺时针收集 N 个不同物理节点
func preferenceList(key string) []physNode {
	h := hash32(key)
	seen := make(map[int]bool)
	out := make([]physNode, 0, replicaN)
	// 起点:第一个 pos > h 的 vnode,绕回则取 ring[0]
	idx := sort.Search(len(ring), func(i int) bool { return ring[i].pos > h })
	if idx == len(ring) {
		idx = 0
	}
	for step := 0; step < ringSize && len(out) < replicaN; step++ {
		v := ring[idx]
		if !seen[v.nodeID] {
			out = append(out, nodes[v.nodeID])
			seen[v.nodeID] = true
		}
		idx++
		if idx == len(ring) {
			idx = 0
		}
	}
	return out
}

// hinted handoff:把数据写到 ring 上下一个健康节点并加 hint
func hintPush(owner, key, value string) {
	if len(hintQueue) >= hintQueueMax {
		fmt.Println("hint queue full, dropping")
		return
	}
	hintQueue = append(hintQueue, hint{Owner: owner, Key: key, Value: value})
	fmt.Printf("[HINT] saved for %s key=%s\n", owner, key)
}

// 模拟节点恢复,扫描 hint 队列投递属于它的 hints
func hintReplay(recovered string) {
	fmt.Printf("[HINT-REPLAY] scanning hints for %s\n", recovered)
	kept := hintQueue[:0]
	for _, h := range hintQueue {
		if h.Owner == recovered {
			fmt.Printf("  -> delivering key=%s value=%s\n", h.Key, h.Value)
		} else {
			kept = append(kept, h)
		}
	}
	hintQueue = kept
}

func printPref(key string) {
	pl := preferenceList(key)
	names := make([]string, 0, len(pl))
	for _, n := range pl {
		names = append(names, n.name)
	}
	fmt.Printf("key=%-20q hash=%08x preference_list=[%s]\n",
		key, hash32(key), strings.Join(names, ", "))
}

func main() {
	buildRing()

	fmt.Println("=== Dynamo 风格存储演示 (Go 版核心机制) ===")
	fmt.Printf("ring=%d vnodes, nodes=%d, replica N=%d\n\n",
		ringSize, physNodes, replicaN)

	scanner := bufio.NewScanner(os.Stdin)
	fmt.Println("输入命令: put/get/pref/hint/replay/quit; 或直接回车跑默认 demo")

	ranDemo := false
	for {
		fmt.Print("> ")
		if !scanner.Scan() {
			break
		}
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			if ranDemo {
				continue
			}
			// 默认演示
			fmt.Println("\n--- preference list 演示 ---")
			for _, k := range []string{
				"user:42:cart", "product:sku-9001", "order:2026-09-12",
				"session:abc123", "hot:key_always_here",
			} {
				printPref(k)
			}
			fmt.Println("\n--- 同 key 路由确定性 ---")
			for i := 0; i < 3; i++ {
				printPref("user:42:cart")
			}
			fmt.Println("\n--- hinted handoff ---")
			pl := preferenceList("user:42:cart")
			if len(pl) >= replicaN {
				down := pl[replicaN-1].name // 第 3 副本 down
				hintPush(down, "user:42:cart", "book")
				hintPush(down, "user:42:cart:v2", "pen")
				hintReplay(down)
			}
			ranDemo = true
			continue
		}

		fields := strings.Fields(line)
		switch fields[0] {
		case "put":
			if len(fields) < 3 {
				fmt.Println("usage: put <key> <value>")
				continue
			}
			// 不实际分发到网络节点,只演示路由
			pl := preferenceList(fields[1])
			fmt.Printf("put(%q, %q) would route to:", fields[1], fields[2])
			for _, n := range pl {
				fmt.Printf(" %s", n.name)
			}
			fmt.Println()
		case "get":
			if len(fields) < 2 {
				fmt.Println("usage: get <key>")
				continue
			}
			pl := preferenceList(fields[1])
			fmt.Printf("get(%q) would read from:", fields[1])
			for _, n := range pl {
				fmt.Printf(" %s", n.name)
			}
			fmt.Println()
		case "pref":
			if len(fields) < 2 {
				fmt.Println("usage: pref <key>")
				continue
			}
			printPref(fields[1])
		case "hint":
			fmt.Printf("hint queue (%d):\n", len(hintQueue))
			for _, h := range hintQueue {
				fmt.Printf("  owner=%s key=%s\n", h.Owner, h.Key)
			}
		case "replay":
			if len(fields) < 2 {
				fmt.Println("usage: replay <node>")
				continue
			}
			hintReplay(fields[1])
		case "quit":
			return
		default:
			fmt.Println("unknown command")
		}
	}
}
