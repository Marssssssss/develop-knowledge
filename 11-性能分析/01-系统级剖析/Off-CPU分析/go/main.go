// Off-CPU 分析 demo:Go 版 —— goroutine 的 off-CPU 记账。
//
// 把 Gregg offcputime 的记账模型移植到 goroutine 世界:
//   - "goroutine park(离开运行队列)" 对应线程下 CPU;
//   - "goroutine unpark/唤醒" 对应线程上 CPU;
//   - 在 park 侧记时刻,在唤醒侧结算 delta 并聚合到 folded;
//   - 最后输出 folded 计数,演示多 goroutine 聚合等待(时间膨胀)。
//
// Go 生态的现实对照:Go 的阻塞分析不是靠 ptrace,而是 runtime 内建 ——
// runtime/pprof 的 block profile(GODEBUG=... 或 http pprof 的 block)
// 记录的就是"channel/锁等同步原语的等待时长",即 off-CPU 思想的 runtime 内建版。
package main

import (
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

// offCPUTracker:totaltime[key] += delta,key = "栈;路径"。
type offCPUTracker struct {
	mu       sync.Mutex
	total    map[string]float64 // 秒
	sleept   map[int64]float64   // goroutine id -> park 时刻(对应 sleeptime[])
	nextID   int64
}

func newTracker() *offCPUTracker {
	return &offCPUTracker{total: map[string]float64{}, sleept: map[int64]float64{}}
}

// reg 注册一个"将被 park 的 goroutine",返回其记账 id。
func (t *offCPUTracker) reg(stack string) int64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.nextID++
	return t.nextID
}

// park 对应"线程离开 CPU":记时刻。state 参数记录睡眠类型。
func (t *offCPUTracker) park(id int64, ts float64, state string) {
	t.mu.Lock()
	t.sleept[id] = ts
	t.mu.Unlock()
	_ = state // 真实工具里与 --state=2 过滤对应:R(被抢)或 D(真阻塞)
}

// unpark 对应"线程回到 CPU":结算 delta 并聚合。
func (t *offCPUTracker) unpark(id int64, ts float64, stack string) {
	t.mu.Lock()
	start, ok := t.sleept[id]
	if ok {
		delete(t.sleept, id)
		t.total[stack] += ts - start
	}
	t.mu.Unlock()
}

// folded 输出:"frame;frame;... us 计数"(对齐 bcc offcputime -f)。
func (t *offCPUTracker) folded() []string {
	t.mu.Lock()
	defer t.mu.Unlock()
	out := make([]string, 0, len(t.total))
	for k, v := range t.total {
		out = append(out, fmt.Sprintf("%s %d", k, int64(v*1e6)))
	}
	sort.Strings(out)
	return out
}

func now() float64 { return float64(time.Now().UnixNano()) / 1e9 }

// worker 模拟"干活 goroutine":周期性阻塞在 IO(同步 pread 语义)。
func worker(t *offCPUTracker, id int64, stack string, wg *sync.WaitGroup) {
	defer wg.Done()
	deadline := now() + 3.0
	for now() < deadline {
		t.park(id, now(), "D")
		time.Sleep(30 * time.Millisecond) // 模拟 pread 阻塞(off-CPU)
		t.unpark(id, now(), stack)
		time.Sleep(70 * time.Millisecond) // 模拟干活(on-CPU)
	}
}

// poolIdle 模拟"线程池空闲 goroutine":长睡眠等任务,聚合后时间膨胀。
func poolIdle(t *offCPUTracker, id int64, stack string, wg *sync.WaitGroup) {
	defer wg.Done()
	deadline := now() + 3.0
	for now() < deadline {
		t.park(id, now(), "D")
		time.Sleep(900 * time.Millisecond) // 睡掉窗口的 90%
		t.unpark(id, now(), stack)
		time.Sleep(1 * time.Millisecond) // 醒来即又睡
	}
}

func main() {
	t := newTracker()
	var wg sync.WaitGroup

	workerStack := "main;handle_connection;do_command;read_row;pread"
	poolStack := "main;io_handler_thread;os_event_wait;epoll_wait"

	// 2 个干活 goroutine + 8 个空闲池 goroutine(对齐 Python 版设定)
	for i := 0; i < 2; i++ {
		id := t.reg(workerStack)
		wg.Add(1)
		go worker(t, id, workerStack, &wg)
	}
	for i := 0; i < 8; i++ {
		id := t.reg(poolStack)
		wg.Add(1)
		go poolIdle(t, id, poolStack, &wg)
	}
	wg.Wait()

	wall := 3.0
	total := 0.0
	for _, v := range t.total {
		total += v
	}
	fmt.Printf("== goroutine off-CPU 记账(%.0fs 窗口)==\n", wall)
	fmt.Printf("聚合 off-CPU = %.2fs | 墙钟 = %.0fs | 膨胀比 = %.2fx\n\n",
		total, wall, total/wall)

	fmt.Println("---- folded(countname=us,可喂 flamegraph.pl --color=io)----")
	for _, line := range t.folded() {
		if !strings.HasPrefix(line, "main;") {
			continue
		}
		fmt.Println(line)
	}

	fmt.Println()
	fmt.Println("对照: Go runtime 内建同类能力 = pprof block profile")
	fmt.Println("  import _ \"net/http/pprof\" 后访问 /debug/pprof/block,")
	fmt.Println("  记录 channel/锁等待时长 —— off-CPU 思想的 runtime 内建版。")
}
