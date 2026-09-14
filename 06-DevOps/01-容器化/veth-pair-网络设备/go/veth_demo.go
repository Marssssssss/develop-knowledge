// veth_demo.go — Linux veth pair 与 bridge 演示(Go 版,无 root 走命令模板)
//
// 参考资料:
//   man7 veth(4):               https://man7.org/linux/man-pages/man4/veth.4.html
//   man7 ip-link(8):            https://man7.org/linux/man-pages/man8/ip-link.8.html
//   kernel-internals:           https://kernel-internals.org/net/container-networking/
//
// 用法:
//   go run veth_demo.go topology    # 打印拓扑示意
//   go run veth_demo.go commands    # 列出可粘贴运行的命令

//go:build linux

package main

import (
	"fmt"
	"os"
)

func cmdTopology() {
	fmt.Println(`veth pair + Linux bridge(Docker/cni 经典拓扑):

  ┌──────────── host netns ─────────────┐  ┌─── container netns ───┐
  │  eth0 (physical, e.g. 10.0.0.5)      │  │ eth0 (veth1 的 peer)  │
  │  docker0 (bridge, 172.17.0.1/16)     │  │ 172.17.0.2/16         │
  │   ├── veth0 ← pair peer → veth1     │  │ ↑                     │
  │   └── veth2 ← pair peer → veth3     │  │ ip route default via  │
  │  iptables MASQUERADE 172.17/16       │  │   172.17.0.1 dev eth0 │
  └─────────────────────────────────────┘  └─────────────────────────┘

  关键事实(man4 veth 原文):
    "Packets transmitted on one device in the pair are immediately
     received on the other device." 两端任一 down → 都 down。
`)
}

func cmdCommands() {
	fmt.Println(`# 粘贴运行的命令序列(需 root):

# 1. 创建 veth pair
ip link add veth0 type veth peer name veth1

# 2. 创建 netns(模仿容器 namespace)
ip netns add demo_ns

# 3. 把 veth1 移进 demo_ns
ip link set veth1 netns demo_ns

# 4. 配置两端 IP,up 接口
ip addr add 10.200.0.1/24 dev veth0
ip link set veth0 up
ip netns exec demo_ns ip addr add 10.200.0.2/24 dev veth1
ip netns exec demo_ns ip link set veth1 up
ip netns exec demo_ns ip link set lo up
ip netns exec demo_ns ip route add default via 10.200.0.1

# 5. 测试连通
ip netns exec demo_ns ping -c 1 10.200.0.1

# 6. 加 bridge(L2 交换机)
ip link add docker0 type bridge
ip addr add 172.17.0.1/16 dev docker0
ip link set docker0 up
ip link set veth0 master docker0

# 7. NAT 网关(让容器借 host 出公网)
iptables -t nat -A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE
sysctl -w net.ipv4.ip_forward=1

# 8. 清理
ip netns del demo_ns
ip link del docker0
ip link del veth0
`)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: veth_demo [topology|commands]")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "topology":
		cmdTopology()
	case "commands":
		cmdCommands()
	default:
		fmt.Println("unknown:", os.Args[1])
		os.Exit(1)
	}
}
