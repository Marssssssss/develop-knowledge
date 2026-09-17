// py-spy 采样剖析复刻：dump/top/record 三模式、GIL 过滤、nonblocking 撕裂读、attach 权限。
//
// 口径（py-spy 官方 README，benfred/py-spy，本轮实读）：
//   - 采样剖析器：不重启、不修改目标；Rust 独立进程直接读目标内存
//     （process_vm_readv / vm_read / ReadProcessMemory）
//   - 栈重建：PyInterpreterState → 全部线程 → 迭代 PyFrameObject 的 back 链
//   - top 实时视图（%own/%total/%GIL）；dump 一次性各线程栈（--locals）；
//     record 折叠栈（火焰图数据）
//   - --gil 只采样持锁线程（会漏掉释放 GIL 仍在干活的原生扩展）
//   - --nonblocking 不暂停目标：读非原子 → 偶发残缺栈/采样错误
//   - 权限：Linux attach 非子进程通常要 root（ptrace_scope）；macOS 恒需 root；
//     Docker/K8s 需 SYS_PTRACE
package main

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

// Frame 模拟 PyFrameObject：func 名 + back 链 + 局部变量。
type Frame struct {
	Func  string
	Back  *Frame
	Locs  map[string]string
}

// Thread 模拟线程状态。
type Thread struct {
	Tid       int
	Name      string
	Current   *Frame // 最内层（执行中）帧
	HoldsGIL  bool
}

// Interp 模拟 PyInterpreterState：全部线程。
type Interp struct {
	Threads []*Thread
}

// Process 目标进程。
type Process struct {
	PID   int
	Argv  []string
	Interp *Interp
	PyVer [2]int
}

var errTorn = errors.New("torn read")

var readCall = map[string]string{
	"Linux": "process_vm_readv", "Darwin": "vm_read", "Windows": "ReadProcessMemory",
}

func readCallFor(system string) string { return readCall[system] }

// findInterpAddress：有符号 deref interp_head/_PyRuntime；无符号扫 BSS 段。
func findInterpAddress(hasSymbols bool) string {
	if hasSymbols {
		return "deref:interp_head_or__PyRuntime"
	}
	return "scan:bss_section"
}

// gilOwnerMethod：≤3.6 → _PyThreadState_Current；≥3.7 → _PyRuntime 推导。
func gilOwnerMethod(major, minor int) string {
	if major < 3 || (major == 3 && minor <= 6) {
		return "_PyThreadState_Current"
	}
	return "_PyRuntime"
}

// Spy 独立采样器进程：只读目标内存，不注入任何代码。
type Spy struct {
	Nonblocking  bool
	SampleErrors int
}

// readStack 遍历 back 链（执行中帧→根），返回根在前的栈。
// torn=true 模拟非阻塞读的撕裂：只读到最内层帧；空栈返回错误。
func (s *Spy) readStack(t *Thread, torn bool) ([]*Frame, error) {
	var stack []*Frame
	f := t.Current
	for f != nil {
		stack = append(stack, f)
		if torn && len(stack) == 1 {
			break
		}
		f = f.Back
	}
	// 反转成根在前
	for i, j := 0, len(stack)-1; i < j; i, j = i+1, j-1 {
		stack[i], stack[j] = stack[j], stack[i]
	}
	if torn && len(stack) == 0 {
		s.SampleErrors++
		return nil, errTorn
	}
	return stack, nil
}

// dump 复刻 py-spy dump：进程信息 + 各线程当前栈（--locals 带局部变量）。
func (s *Spy) dump(p *Process, showLocals bool) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Process %d: %s\n", p.PID, strings.Join(p.Argv, " "))
	fmt.Fprintf(&b, "Python v%d.%d.x\n", p.PyVer[0], p.PyVer[1])
	for _, t := range p.Interp.Threads {
		state := "active"
		if t.Current == nil {
			state = "idle"
		}
		fmt.Fprintf(&b, "Thread %d %q (native): %s\n", t.Tid, t.Name, state)
		stack, _ := s.readStack(t, false)
		for _, f := range stack {
			loc := ""
			if showLocals && len(f.Locs) > 0 {
				keys := make([]string, 0, len(f.Locs))
				for k := range f.Locs {
					keys = append(keys, k)
				}
				sort.Strings(keys)
				kv := make([]string, len(keys))
				for i, k := range keys {
					kv[i] = k + "=" + f.Locs[k]
				}
				loc = " " + strings.Join(kv, ", ")
			}
			fmt.Fprintf(&b, "    %s%s\n", f.Func, loc)
		}
	}
	return b.String()
}

// top 复刻 py-spy top：聚合 own（叶子）与 total（在栈上）与 gil 持有次数。
func (s *Spy) top(p *Process, ticks int, gilOnly bool) map[string]map[string]int {
	res := map[string]map[string]int{"own": {}, "total": {}, "gil": {}}
	for i := 0; i < ticks; i++ {
		for _, t := range p.Interp.Threads {
			if gilOnly && !t.HoldsGIL {
				continue
			}
			if t.Current == nil {
				continue
			}
			stack, _ := s.readStack(t, false)
			for _, f := range stack {
				res["total"][f.Func]++
			}
			res["own"][stack[len(stack)-1].Func]++
			if t.HoldsGIL {
				res["gil"][t.Name]++
			}
		}
	}
	return res
}

// record 复刻 py-spy record：折叠栈（火焰图数据形态）。
func (s *Spy) record(p *Process, ticks int) map[string]int {
	collapsed := map[string]int{}
	for i := 0; i < ticks; i++ {
		for _, t := range p.Interp.Threads {
			if t.Current == nil {
				continue
			}
			stack, _ := s.readStack(t, false)
			names := make([]string, len(stack))
			for j, f := range stack {
				names[j] = f.Func
			}
			collapsed[strings.Join(names, ";")]++
		}
	}
	return collapsed
}

// attachAllowed 权限模型：Linux ptrace_scope=1 时 attach 非子进程需 root；macOS 恒需 root。
func attachAllowed(system string, isChild, isRoot bool, ptraceScope int) bool {
	if system == "Darwin" {
		return isRoot
	}
	if system == "Linux" {
		if ptraceScope == 0 {
			return true
		}
		return isChild || isRoot
	}
	return true
}

func dockerOK(capSysPtrace bool) bool { return capSysPtrace }

func makeDemoProcess() *Process {
	root := &Frame{Func: "main.main", Locs: map[string]string{"url": "'/api'"}}
	work := &Frame{Func: "app.work", Back: root, Locs: map[string]string{"n": "42"}}
	leaf := &Frame{Func: "app.parse", Back: work}
	worker := &Thread{Tid: 101, Name: "MainThread", Current: leaf, HoldsGIL: true}
	native := &Thread{Tid: 102, Name: "CythonThread",
		Current: &Frame{Func: "ext.native_loop"}, HoldsGIL: false} // 释放 GIL 的原生扩展
	return &Process{PID: 1234, Argv: []string{"python", "app.py"},
		Interp: &Interp{Threads: []*Thread{worker, native}}, PyVer: [2]int{3, 11}}
}

func check(label string, cond bool) {
	if !cond {
		fmt.Println("FAIL:", label)
		panic("assertion failed: " + label)
	}
}

func main() {
	proc := makeDemoProcess()
	spy := &Spy{}

	// 1. 栈重建：back 链 → 根在前
	t := proc.Interp.Threads[0]
	stack, _ := spy.readStack(t, false)
	check("stack order", len(stack) == 3 && stack[0].Func == "main.main" &&
		stack[1].Func == "app.work" && stack[2].Func == "app.parse")

	// 2. dump：进程信息 + 各线程栈；--locals 带局部变量
	text := spy.dump(proc, false)
	check("dump process", strings.Contains(text, "Process 1234"))
	check("dump threads", strings.Contains(text, "Thread 101") && strings.Contains(text, "Thread 102"))
	check("dump frames", strings.Contains(text, "app.parse") && strings.Contains(text, "ext.native_loop"))
	text = spy.dump(proc, true)
	check("dump locals", strings.Contains(text, "url='/api'") && strings.Contains(text, "n=42"))
	check("locals beside frame", strings.Contains(text, "main.main url='/api'"))

	// 3. top：own=叶子、total=在栈上
	r := spy.top(proc, 10, false)
	check("own leaf only", r["own"]["app.parse"] == 10 && r["own"]["ext.native_loop"] == 10)
	check("total presence", r["total"]["main.main"] == 10 && r["total"]["app.work"] == 10 &&
		r["total"]["app.parse"] == 10 && r["total"]["ext.native_loop"] == 10)

	// 4. %GIL：MainThread 每个采样期都持锁
	check("gil main", r["gil"]["MainThread"] == 10 && len(r["gil"]) == 1)

	// 5. --gil 过滤：原生线程被排除（会漏掉仍在干活的原生扩展）
	r = spy.top(proc, 5, true)
	_, hasNative := r["own"]["ext.native_loop"]
	_, hasNativeT := r["total"]["ext.native_loop"]
	check("gil filter", !hasNative && !hasNativeT && r["own"]["app.parse"] == 5)

	// 6. --nonblocking：撕裂读残缺栈（叶子侧保留）并计错误
	nb := &Spy{Nonblocking: true}
	st, err := nb.readStack(t, true)
	check("torn partial", err == nil && len(st) == 1 && st[0].Func == "app.parse")
	idle := &Thread{Tid: 103, Name: "Idle"}
	_, err = nb.readStack(idle, true)
	check("torn empty errors", err == errTorn && nb.SampleErrors == 1)

	// 7. record：折叠栈
	c := spy.record(proc, 4)
	check("collapsed", c["main.main;app.work;app.parse"] == 4 && c["ext.native_loop"] == 4)

	// 8. 读内存系统调用映射
	check("readcall linux", readCallFor("Linux") == "process_vm_readv")
	check("readcall darwin", readCallFor("Darwin") == "vm_read")
	check("readcall windows", readCallFor("Windows") == "ReadProcessMemory")

	// 9. 解释器地址发现：有符号 vs 无符号
	check("sym deref", strings.HasPrefix(findInterpAddress(true), "deref"))
	check("nosym bss scan", strings.HasPrefix(findInterpAddress(false), "scan"))

	// 10. GIL 判定入口随版本切换
	check("gil 3.6", gilOwnerMethod(3, 6) == "_PyThreadState_Current")
	check("gil 3.7", gilOwnerMethod(3, 7) == "_PyRuntime")
	check("gil 3.11", gilOwnerMethod(3, 11) == "_PyRuntime")

	// 11. attach 权限模型
	check("linux non-child no root", !attachAllowed("Linux", false, false, 1))
	check("linux child ok", attachAllowed("Linux", true, false, 1))
	check("linux root ok", attachAllowed("Linux", false, true, 1))
	check("linux scope0", attachAllowed("Linux", false, false, 0))
	check("darwin needs root", !attachAllowed("Darwin", true, false, 1))
	check("darwin root ok", attachAllowed("Darwin", true, true, 1))

	// 12. Docker/K8s：SYS_PTRACE 能力位
	check("docker cap", !dockerOK(false) && dockerOK(true))

	fmt.Println("pyspy_sample: 12 组断言全部通过")
}
