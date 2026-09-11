// Namespace 隔离最小演示 —— Linux namespaces(7) / unshare(2) 实战(Go 版).
//
// Go 标准库 syscall 包在 Linux 上提供 Unshare;sethostname(2) 无封装,
// 通过 syscall.Syscall(SYS_SETHOSTNAME, ...) 直调。
// Go 没有 fork(2) 封装,fork+观测用 exec 自我复制实现:
// 父进程先 spawn 观察者(留在旧 namespace),再 unshare 改状态,
// 两者各自打印视角 => 演示隔离性。
//
// 运行(仅 Linux):
//   go run ns_demo.go uts   # sudo
//   go run ns_demo.go user  # 无需 root
//   go run ns_demo.go pid   # sudo
package main

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"unsafe"
)

const (
	cloneNewNS   = 0x00020000 // CLONE_NEWNS
	cloneNewUTS  = 0x04000000 // CLONE_NEWUTS
	cloneNewUser = 0x10000000 // CLONE_NEWUSER
	cloneNewPID  = 0x20000000 // CLONE_NEWPID
)

func must(err error, what string) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", what, err)
		os.Exit(1)
	}
}

// hostname 返回当前 UTS namespace 的主机名(经 /proc/sys/kernel/hostname,
// 该文件就是 UTS namespace 隔离资源在 procfs 的投影)。
func hostname() string {
	b, err := os.ReadFile("/proc/sys/kernel/hostname")
	must(err, "read hostname")
	return strings.TrimSpace(string(b))
}

func nsHandle(name string) string {
	target, err := os.Readlink("/proc/self/ns/" + name)
	must(err, "readlink ns")
	return target
}

func setHostname(name string) {
	b := []byte(name)
	_, _, errno := syscall.Syscall(
		syscall.SYS_SETHOSTNAME,
		uintptr(unsafe.Pointer(&b[0])), uintptr(len(b)), 0)
	if errno != 0 {
		must(errno, "sethostname")
	}
}

// observer 在另一个进程(旧 namespace)里等管道信号后回报主机名,
// 用于与 unshare 之后的父进程对照。
func spawnObserver(r *os.File) *exec.Cmd {
	cmd := exec.Command(os.Args[0], "uts-observer", r.Name())
	cmd.Stdout = os.Stdout
	cmd.ExtraFiles = []*os.File{r} // 子进程内是 fd 3
	must(cmd.Start(), "spawn observer")
	return cmd
}

func demoUTS() {
	fmt.Println("== UTS namespace 演示 ==")
	fmt.Printf("  [父] hostname = %s\n", hostname())

	r, w, err := os.Pipe()
	must(err, "pipe")
	cmd := spawnObserver(r)

	// 调用进程自身进入新 UTS namespace,主机名修改不影响观察者
	must(syscall.Unshare(cloneNewUTS), "unshare(CLONE_NEWUTS)")
	fmt.Printf("  [父] 新 uts ns handle: %s\n", nsHandle("uts"))
	setHostname("container-ns")
	fmt.Printf("  [父(新 UTS ns)] hostname = %s\n", hostname())

	w.Close() // 通知观察者读主机名
	must(cmd.Wait(), "observer wait")
	fmt.Println("  => 观察者(旧 ns)与父进程(新 ns)互不可见对方的修改")
}

func writeMap(file, content string) {
	must(os.WriteFile("/proc/self/"+file, []byte(content), 0), "write "+file)
}

func demoUser() {
	fmt.Println("== user namespace 演示(无需 root) ==")
	fmt.Printf("  真实身份: uid=%d gid=%d\n", os.Getuid(), os.Getgid())

	// 唯一无需 CAP_SYS_ADMIN 的 namespace;要求进程单线程
	must(syscall.Unshare(cloneNewUser), "unshare(CLONE_NEWUSER)")

	// Linux 3.19 起必须先 setgroups=deny 再写 gid_map
	writeMap("setgroups", "deny")
	writeMap("uid_map", fmt.Sprintf("0 %d 1\n", os.Getuid()))
	writeMap("gid_map", fmt.Sprintf("0 %d 1\n", os.Getgid()))
	fmt.Printf("  映射后身份: uid=%d gid=%d (新 ns 内解释为 0)\n",
		os.Getuid(), os.Getgid())

	// CapEff:新 user ns 内应为全套 capabilities(全 1 位图)
	status, err := os.ReadFile("/proc/self/status")
	must(err, "read status")
	for _, line := range strings.Split(string(status), "\n") {
		if strings.HasPrefix(line, "CapEff:") {
			fmt.Printf("  %s\n", line)
			break
		}
	}

	// 依赖新 ns 内的全套 capabilities,继续无特权创建 UTS namespace
	must(syscall.Unshare(cloneNewUTS), "无特权 unshare(CLONE_NEWUTS)")
	fmt.Println("  无特权创建 UTS namespace 成功(依赖新 user ns 的 capabilities)")
}

func demoPID() {
	fmt.Println("== PID namespace 演示 ==")
	fmt.Printf("  [父] unshare 前 getpid() = %d\n", os.Getpid())

	// 只让后续子进程进入新 PID ns;同时建 mount ns 以挂新 procfs
	must(syscall.Unshare(cloneNewPID|cloneNewNS), "unshare(NEWPID|NEWNS)")

	// exec.Command 内部 fork+exec:子进程是新 PID ns 的第一个进程(PID 1)
	cmd := exec.Command(os.Args[0], "pid-child")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	must(cmd.Start(), "spawn pid-child")
	must(cmd.Wait(), "pid-child wait")

	fmt.Printf("  [父] getpid() = %d (仍在旧 ns,值不变)\n", os.Getpid())
	fmt.Println("  => PID namespace 只对子进程生效,单向不可回退")
}

// pidChild: 新 PID ns 内 PID=1 的进程,挂载新 procfs 后枚举可见进程。
func pidChild() {
	fmt.Printf("  [子] getpid() = %d (新 ns 内 PID 1)\n", os.Getpid())
	fmt.Printf("  [子] getppid() = %d (ns 外父进程在新 ns 视角不可见)\n",
		os.Getppid())

	// MS_REC|MS_PRIVATE 断开共享传播;再挂新 procfs(pid_namespaces(7))
	const msRec = 16384
	const msPrivate = 1 << 18
	if err := syscall.Mount("", "/", "", msRec|msPrivate, ""); err != nil {
		must(err, "mount private")
	}
	if err := syscall.Mount("proc", "/proc", "proc", 0, ""); err != nil {
		must(err, "mount proc")
	}
	fmt.Println("  [子] 新 /proc 下可见进程:")
	entries, err := os.ReadDir("/proc")
	must(err, "read /proc")
	for _, e := range entries {
		if _, err := strconv.Atoi(e.Name()); err == nil {
			fmt.Printf("    pid=%s\n", e.Name())
		}
	}
}

// utsObserver: 等 fd3 关闭(EOF)后打印自己(旧 namespace)视角的主机名。
func utsObserver() {
	f := os.NewFile(3, "pipe")
	buf := make([]byte, 1)
	for {
		if _, err := f.Read(buf); err != nil { // 等父进程关闭写端
			break
		}
	}
	fmt.Printf("  [观察者(旧 UTS ns)] hostname = %s\n", hostname())
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("用法: go run ns_demo.go <uts|user|pid|uts-observer|pid-child>")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "uts":
		demoUTS()
	case "user":
		demoUser()
	case "pid":
		demoPID()
	case "uts-observer":
		utsObserver()
	case "pid-child":
		pidChild()
	default:
		fmt.Println("未知子命令:", os.Args[1])
		os.Exit(1)
	}
}
