// TCP 连接建立的两条队列：半连接（SYN）队列 与 全连接（accept）队列
//
// 用离散事件状态机复现 man7 listen(2) + 内核 ip-sysctl 文档描述的语义：
//   - Linux 2.2 起 backlog 限制【全连接队列】，不是半连接队列；
//   - 全连接队列上限 = min(listen backlog, net.core.somaxconn)（静默取小）；
//   - 半连接队列上限 = net.ipv4.tcp_max_syn_backlog（每监听器）；
//   - 全连接队列满时 tcp_abort_on_overflow=0（默认）→ 忽略最终 ACK 等客户端
//     重传（自愈）；=1 → 直接回 RST；
//   - 半连接队列满时 tcp_syncookies=1（默认）→ 改用 SYN Cookie，不占队列。
//
// 运行：go run main.go
package main

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
)

func selfCheck() {
	// 1. 有效上限 = min(backlog, somaxconn)
	a := newListener(4, 511, 128, 1024, true, false)
	check(a.acceptLimit() == 128, "somaxconn 更小时应取 somaxconn")
	b := newListener(4, 8, 4096, 1024, true, false)
	check(b.acceptLimit() == 8, "backlog 更小时应取 backlog")

	// 2. 常量与内核文档一致
	check(somaxconnModern == 4096, "Linux 5.4 起 somaxconn 默认 4096")
	check(synBacklogFloor == 128, "tcp_max_syn_backlog 低内存最小值 128")

	// 3. 正常场景：应用及时 accept → 不溢出、全部建连
	ok := simulate(40, 2, 4000, 2, 4, 1, 64, 4096, 1024, true, false)
	check(ok.st.synDropped == 0 && ok.st.overflow == 0, "应用及时消费时不应溢出")
	check(ok.st.accepted == 40, "正常场景应 40 个连接全部被 accept")

	// 4. 应用太慢 → 全连接队列溢出；默认不发 RST，靠重传自愈
	slow := simulate(60, 20, 4000, 2, 4, 1, 4, 4096, 1024, true, false)
	check(slow.st.overflow > 0, "应用太慢应触发全连接队列溢出")
	check(slow.st.rstSent == 0, "默认策略不应发 RST")
	check(slow.st.recovered > 0, "被忽略的 ACK 应能靠重传自愈")

	// 5. abort_on_overflow=1 → 每次溢出都回 RST，失去自愈机会
	hard := simulate(60, 20, 4000, 2, 4, 1, 4, 4096, 1024, true, true)
	check(hard.st.rstSent == hard.st.overflow, "abort_on_overflow=1 时每次溢出都应回 RST")
	check(hard.st.recovered == 0, "回 RST 的连接不可能自愈")
	check(hard.st.accepted < slow.st.accepted, "发 RST 的版本最终建连数应更少")

	// 6/7. SYN 洪泛：syncookies 关掉会丢 SYN，开着不丢且不占队列
	off := simulate(400, 1000, 300, 2, 4, 20, 8, 4096, 4, false, false)
	check(off.st.synDropped > 0, "syncookies 关闭时应丢 SYN")
	check(off.st.maxSynQ <= 4, "半连接队列不应超过 tcp_max_syn_backlog")
	check(off.st.syncookieIssued == 0, "关闭时不应发 SYN Cookie")
	on := simulate(400, 1000, 300, 2, 4, 20, 8, 4096, 4, true, false)
	check(on.st.synDropped == 0, "syncookies 开启时不应丢 SYN")
	check(on.st.syncookieIssued > 0, "溢出部分应改用 SYN Cookie")
	check(on.st.maxSynQ <= 4, "SYN Cookie 不占半连接队列")
	check(on.st.synDropped < off.st.synDropped, "开启 syncookies 后丢包应显著减少")

	// 8. 单调性：队列上限调大，溢出次数不增
	overflows := make([]int, 0, 4)
	for _, bl := range []int{4, 8, 16, 64} {
		overflows = append(overflows, simulate(60, 20, 4000, 2, 4, 1, bl, 4096, 1024, true, false).st.overflow)
	}
	monotone := true
	for i := 1; i < len(overflows); i++ {
		if overflows[i] > overflows[i-1] {
			monotone = false
		}
	}
	check(monotone, fmt.Sprintf("队列上限调大后溢出次数不应上升: %v", overflows))
	check(overflows[3] == 0 && overflows[0] > 0,
		fmt.Sprintf("backlog=64 应不再溢出，backlog=4 应明显溢出: %v", overflows))

	// 9. somaxconn 是隐形天花板
	capped := simulate(400, 500, 3000, 2, 4, 1, 1024, 128, 1024, true, false)
	big := simulate(400, 500, 3000, 2, 4, 1, 1024, 4096, 1024, true, false)
	check(capped.st.maxAcceptQ == 128, "somaxconn=128 应把队列卡在 128")
	check(capped.st.overflow > 0, "被卡住时应发生溢出")
	check(big.st.maxAcceptQ > 128 && big.st.overflow == 0,
		"somaxconn 放开后队列应能超过 128 且不再溢出")

	// 10. 半连接队列的内存代价 ≈ tcp_max_syn_backlog × 304 B
	mem := 65536.0 * bytesPerSynRecv / (1024 * 1024)
	check(mem > 18 && mem < 20, "65536 个 SYN_RECV 约 19 MiB")
	check(synBacklogFloor*bytesPerSynRecv/1024 == 38, "128 个 SYN_RECV 约 38 KiB")

	fmt.Printf("[self-check] %d 项断言, %d 项失败\n", checks, failed)
}

// -------------------------------------------- 平台相关的真实套接字检查
func liveCheck() {
	fmt.Println("\n=== 7) 真实环境检查 ===")
	if runtime.GOOS != "linux" {
		fmt.Printf("  当前平台 %s 非 Linux：somaxconn / sendfile / syncookies 都不适用，跳过\n",
			runtime.GOOS)
		return
	}
	for _, p := range []string{"/proc/sys/net/core/somaxconn",
		"/proc/sys/net/ipv4/tcp_max_syn_backlog"} {
		raw, err := os.ReadFile(p)
		if err != nil {
			fmt.Printf("  %s 读取失败: %v\n", p, err)
			continue
		}
		v, _ := strconv.Atoi(strings.TrimSpace(string(raw)))
		fmt.Printf("  %-40s = %d\n", p, v)
		check(v > 0, p+" 应大于 0")
	}
	// ss 的输出里 Recv-Q 是全连接队列当前长度、Send-Q 是 backlog 上限
	if out, err := exec.Command("ss", "-lnt").Output(); err == nil {
		lines := strings.Split(strings.TrimSpace(string(out)), "\n")
		if len(lines) > 1 {
			fmt.Println("  ss -lnt 前 3 行（Recv-Q = 队列当前长度，Send-Q = 队列上限）：")
			for i := 0; i < len(lines) && i < 4; i++ {
				fmt.Println("   ", lines[i])
			}
		}
	}
}

func main() {
	selfCheck()

	fmt.Println("\n=== 1) 两条队列的分工（man7 listen(2) + Linux 2.2 语义变更）===")
	fmt.Println("  半连接队列 (SYN queue)   : 存 SYN_RECV，上限 net.ipv4.tcp_max_syn_backlog")
	fmt.Println("  全连接队列 (accept queue): 存已 ESTABLISHED、等 accept() 的连接")
	fmt.Println("                            上限 min(listen backlog, net.core.somaxconn)")
	fmt.Printf("  单个 SYN_RECV 约 %d 字节（内核文档）\n", bytesPerSynRecv)

	fmt.Println("\n=== 2) 应用消费速度 vs 全连接队列溢出（backlog=4，60 个连接）===")
	fmt.Printf("  %12s %10s %16s %10s %6s %12s %6s\n", "accept 间隔", "建连成功",
		"溢出(ACK被忽略)", "自愈成功", "RST", "最终 accept", "放弃")
	for _, every := range []int{1, 5, 10, 25, 50} {
		r := simulate(60, every, 4000, 2, 4, 1, 4, 4096, 1024, true, false)
		fmt.Printf("  %12d %10d %16d %10d %6d %12d %6d\n", every,
			r.st.established, r.st.overflow, r.st.recovered, r.st.rstSent,
			r.st.accepted, r.st.gaveUp)
	}

	fmt.Println("\n=== 3) tcp_abort_on_overflow：丢弃 vs RST（backlog=4, accept 间隔=4）===")
	for _, abort := range []bool{false, true} {
		r := simulate(30, 4, 4000, 2, 4, 1, 4, 4096, 1024, true, abort)
		fmt.Printf("  abort_on_overflow=%d: 溢出 %3d 次, RST %3d 次, 自愈 %3d 个, 最终 accept %3d 个\n",
			boolToInt(abort), r.st.overflow, r.st.rstSent, r.st.recovered, r.st.accepted)
	}

	fmt.Println("\n=== 4) SYN 洪泛：syncookies 的挡板作用（syn_backlog=4, 20 SYN/tick）===")
	for _, ck := range []bool{false, true} {
		r := simulate(400, 1000, 300, 2, 4, 20, 8, 4096, 4, ck, false)
		fmt.Printf("  tcp_syncookies=%d: 处理 SYN %4d, 丢弃 %4d, SYN Cookie %3d, 半连接队列峰值 %d\n",
			boolToInt(ck), r.st.synReceived, r.st.synDropped, r.st.syncookieIssued, r.st.maxSynQ)
	}
	fmt.Println("  注意：内核文档明确 syncookies 只是回退机制，不能用它支撑高负载站点的")
	fmt.Println("        合法连接速率——它违反 TCP 协议、禁用 TCP 扩展，会拖累客户端与中继。")

	fmt.Println("\n=== 5) somaxconn 是隐形天花板（应用写 backlog=1024，400 个连接）===")
	for _, smc := range []int{128, 4096} {
		r := simulate(400, 500, 3000, 2, 4, 1, 1024, smc, 1024, true, false)
		fmt.Printf("  net.core.somaxconn=%5d: 有效上限 %5d, 队列峰值 %4d, 溢出 %5d\n",
			smc, r.acceptLimit(), r.st.maxAcceptQ, r.st.overflow)
	}

	liveCheck()
	if failed > 0 {
		fmt.Printf("\n有 %d 项自检失败\n", failed)
	}
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
