// 采样剖析原理 demo:Go 版。
//
// 模型:与 C/Python 版同一原理的 Go 变体 —— Go runtime 的 CPU profile
// 本质上也是"定时信号 + 栈回溯"驱动的采样(见 README)。本 demo 用
// time.Ticker 周期触发 + runtime.Stack(buf, true) 抓全部 goroutine 栈,
// 折叠计数后输出 folded 样本,模拟采样器的完整链路。
package main

import (
	"bufio"
	"fmt"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	samplePeriod = 10 * time.Millisecond // 100Hz(奇数频率思想见 README)
	duration     = 3 * time.Second
	maxStackKB   = 64 // 每个 goroutine 栈快照的缓冲上限
)

type sampler struct {
	mu     sync.Mutex
	counts map[string]int
	total  int
}

func newSampler() *sampler { return &sampler{counts: make(map[string]int)} }

// fold 把 runtime.Stack 的文本块折叠成 "g;main;work;spin" 单行。
// 输入形如:
//
//	goroutine 7 [running]:
//	main.work(0x...)
//		main.go:42 +0x1f
//	main.main()
//		main.go:60 +0x3a
func (s *sampler) fold(block string) string {
	lines := strings.Split(strings.TrimSpace(block), "\n")
	var frames []string
	for _, ln := range lines {
		// 每个栈帧行形如 "main.work(0xc000...)" 或 "\tmain.go:42 +0x1f"
		// 只取函数行(含 '(' 或不含 ':' 行号标记的头部行),跳过文件行与首行
		if strings.HasPrefix(ln, "goroutine ") || strings.HasPrefix(ln, "\t") {
			continue
		}
		fn := strings.TrimSpace(ln)
		if i := strings.Index(fn, "("); i > 0 {
			fn = fn[:i]
		}
		if fn == "" || strings.HasPrefix(fn, "runtime.Stack") || strings.HasPrefix(fn, "sampler.sample") {
			continue // 跳过采样器自身帧
		}
		frames = append(frames, fn)
	}
	// runtime.Stack 输出是叶在前、根在后;folded 要求根在前
	for i, j := 0, len(frames)-1; i < j; i, j = i+1, j-1 {
		frames[i], frames[j] = frames[j], frames[i]
	}
	return strings.Join(frames, ";")
}

// sample 触发一次全 goroutine 栈抓取并记账。
func (s *sampler) sample() {
	buf := make([]byte, 16*1024)
	for {
		n := runtime.Stack(buf, true) // true = 抓全部 goroutine
		if n < len(buf) || len(buf) >= maxStackKB*1024 {
			raw := string(buf[:n])
			s.mu.Lock()
			for _, block := range strings.Split(raw, "\n\n") {
				if !strings.HasPrefix(block, "goroutine ") {
					continue
				}
				key := s.fold(block)
				if key != "" {
					s.counts[key]++
				}
			}
			s.total++
			s.mu.Unlock()
			return
		}
		buf = make([]byte, len(buf)*2) // 栈太深则加倍缓冲重试
	}
}

// 被剖析的负载:两个 goroutine 分别跑重/轻 CPU 路径。
func hotLeaf(n int) int {
	total := 0
	for i := 0; i < n; i++ {
		total += (i * i) % 7
	}
	return total
}

func loadHeavy(stop <-chan struct{}, wg *sync.WaitGroup) {
	defer wg.Done()
	for {
		select {
		case <-stop:
			return
		default:
			for i := 0; i < 200000; i++ {
				hotLeaf(200)
			}
		}
	}
}

func loadLight(stop <-chan struct{}, wg *sync.WaitGroup) {
	defer wg.Done()
	for {
		select {
		case <-stop:
			return
		default:
			hotLeaf(50000)
			time.Sleep(2 * time.Millisecond) // 模拟周期性让出,拉开采样占比
		}
	}
}

func main() {
	s := newSampler()
	stop := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(2)
	go loadHeavy(stop, &wg)
	go loadLight(stop, &wg)

	ticker := time.NewTicker(samplePeriod)
	deadline := time.After(duration)
sampling:
	for {
		select {
		case <-deadline:
			break sampling
		case <-ticker.C:
			s.sample()
		}
	}
	ticker.Stop()
	close(stop)
	wg.Wait()

	fmt.Printf("[sampler-go] %d samples, %d distinct stacks\n\n", s.total, len(s.counts))
	fmt.Println("---- folded 输出(可直接喂火焰图生成器)----")
	keys := make([]string, 0, len(s.counts))
	for k := range s.counts {
		keys = append(keys, k)
	}
	sort.Strings(keys) // 与 flamegraph.pl 一致:按栈字符串字母排序
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	for _, k := range keys {
		fmt.Fprintf(w, "%s %d\n", k, s.counts[k])
	}
	fmt.Println("---- top 5(按样本数)----")
	type kv struct {
		k string
		v int
	}
	top := make([]kv, 0, len(s.counts))
	for k, v := range s.counts {
		top = append(top, kv{k, v})
	}
	sort.Slice(top, func(i, j int) bool { return top[i].v > top[j].v })
	for i := 0; i < len(top) && i < 5; i++ {
		fmt.Printf("%5.1f%%  %s\n", 100*float64(top[i].v)/float64(s.total), top[i].k)
	}
}
