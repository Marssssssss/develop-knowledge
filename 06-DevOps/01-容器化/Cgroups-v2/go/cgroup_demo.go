// Cgroups v2 资源限制最小演示 —— Linux 内核 cgroup-v2 文档实战(Go 版).
//
// 三个子 demo(sudo go run cgroup_demo.go <org|mem|cpu>):
//   org : 创建子 cgroup、启用控制器、自迁移进程、观察 memory.current
//   mem : 设 memory.max 后子进程超限分配 -> OOM kill(memory.events.oom_kill)
//   cpu : 设 cpu.max 配额后忙循环 -> 带宽限流(cpu.stat.nr_throttled)
//
// cgroupfs 是纯文本接口;Go 版跨进程部分用 exec 自我复制(os/exec
// 内部即 fork+exec,子进程继承父进程的 cgroup 成员关系)。
package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

const (
	cgRoot = "/sys/fs/cgroup"
	cgDemo = cgRoot + "/cgroup-demo"
)

func must(err error, what string) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", what, err)
		os.Exit(1)
	}
}

func read(path string) string {
	b, err := os.ReadFile(path)
	must(err, "read "+path)
	return strings.TrimSpace(string(b))
}

func write(path, content string) {
	must(os.WriteFile(path, []byte(content), 0), "write "+path)
}

// ensureControllers 确保 root 的 subtree_control 启用所需控制器(缺才补)。
// 注意"内部进程约束":非根 cgroup 含成员进程时不能再启用 domain 控制器。
func ensureControllers(needed string) {
	cur := read(cgRoot + "/cgroup.subtree_control")
	enabled := strings.Fields(cur)
	var add []string
	for _, t := range strings.Fields(needed) {
		found := false
		for _, e := range enabled {
			if e == t[1:] {
				found = true
				break
			}
		}
		if !found {
			add = append(add, t)
		}
	}
	if len(add) > 0 {
		write(cgRoot+"/cgroup.subtree_control", strings.Join(add, " "))
		fmt.Printf("  已在 root 启用控制器: %s\n", strings.Join(add, " "))
	}
}

func cleanup() {
	write(cgRoot+"/cgroup.procs", fmt.Sprintf("%d", os.Getpid()))
	must(os.Remove(cgDemo), "rmdir cgroup-demo")
	fmt.Println("  已清理 cgroup-demo")
}

func demoOrg() {
	fmt.Println("== cgroup 组织与迁移演示 ==")
	must(os.Mkdir(cgDemo, 0o755), "mkdir")
	ensureControllers("+memory +cpu")

	fmt.Printf("  迁移前 memory.current = %s B(空组)\n", read(cgDemo+"/memory.current"))

	// 自迁移:写自己的 PID 到子 cgroup 的 cgroup.procs
	write(cgDemo+"/cgroup.procs", fmt.Sprintf("%d", os.Getpid()))
	// v2 单一层级:/proc/self/cgroup 显示 "0::$PATH"
	fmt.Printf("  /proc/self/cgroup = %s\n", read("/proc/self/cgroup"))

	// 分配并逐页触碰 64MB(触碰才计入 memory.current)
	buf := make([]byte, 64<<20)
	for i := 0; i < len(buf); i += 4096 {
		buf[i] = 1
	}
	fmt.Printf("  触碰 64MB 后 memory.current = %s B\n", read(cgDemo+"/memory.current"))

	cleanup()
}

func demoMem() {
	fmt.Println("== memory.max 资源限制演示 ==")
	must(os.Mkdir(cgDemo, 0o755), "mkdir")
	ensureControllers("+memory")
	write(cgDemo+"/cgroup.procs", fmt.Sprintf("%d", os.Getpid()))

	// 硬上限 16MiB;子进程试图触碰 512MB
	write(cgDemo+"/memory.max", "16777216")

	// exec 自我复制(子进程在本 cgroup 内):超限分配直到被 SIGKILL
	cmd := exec.Command(os.Args[0], "mem-child")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	must(cmd.Start(), "spawn mem-child")
	err := cmd.Wait()

	var sig int
	if ee, ok := err.(*exec.ExitError); ok {
		if ws, ok := ee.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			sig = int(ws.Signal())
		}
	}
	fmt.Printf("  子进程被 OOM kill: %v(signal=%d, SIGKILL=9)\n", sig == 9, sig)

	fmt.Println("  memory.events:")
	for _, line := range strings.Split(read(cgDemo+"/memory.events"), "\n") {
		fmt.Printf("    %s\n", line)
	}

	write(cgDemo+"/memory.max", "max")
	cleanup()
}

// memChild: 新进程,触碰 512MB,预期在 ~16MiB 处被 cgroup OOM killer SIGKILL。
func memChild() {
	buf := make([]byte, 512<<20)
	for i := 0; i < len(buf); i += 4096 {
		buf[i] = 1
	}
	os.Exit(0) // 正常情况下到不了这里
}

func demoCpu() {
	fmt.Println("== cpu.max 带宽配额演示 ==")
	must(os.Mkdir(cgDemo, 0o755), "mkdir")
	ensureControllers("+cpu")
	write(cgDemo+"/cgroup.procs", fmt.Sprintf("%d", os.Getpid()))

	// "50000 100000" = 每 100ms 周期最多 50ms CPU(50% 单核)
	write(cgDemo+"/cpu.max", "50000 100000")

	cmd := exec.Command(os.Args[0], "cpu-child")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	must(cmd.Start(), "spawn cpu-child")
	must(cmd.Wait(), "cpu-child wait")

	// usage_usec 约为墙钟一半;nr_throttled > 0 表示发生过限流
	fmt.Println("  cpu.stat 关键字段:")
	for _, line := range strings.Split(read(cgDemo+"/cpu.stat"), "\n") {
		key := strings.Fields(line)[0]
		switch key {
		case "usage_usec", "nr_periods", "nr_throttled", "throttled_usec":
			fmt.Printf("    %s\n", line)
		}
	}

	write(cgDemo+"/cpu.max", "max 100000")
	cleanup()
}

// cpuChild: 满速忙循环 ~1.5s,期间被带宽限流。
func cpuChild() {
	t0 := time.Now()
	x := uint32(0)
	for time.Since(t0) < 1500*time.Millisecond {
		x = x*1664525 + 1013904223
	}
	os.Exit(0)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("用法: sudo go run cgroup_demo.go <org|mem|cpu|mem-child|cpu-child>")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "org":
		demoOrg()
	case "mem":
		demoMem()
	case "cpu":
		demoCpu()
	case "mem-child":
		memChild()
	case "cpu-child":
		cpuChild()
	default:
		fmt.Println("未知子命令:", os.Args[1])
		os.Exit(1)
	}
}
