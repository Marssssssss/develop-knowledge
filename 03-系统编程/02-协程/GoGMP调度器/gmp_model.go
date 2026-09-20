// gmp_model.go —— Go 运行时调度骨架的 Go 复刻（本机无 Go 工具链，人工审查，不实跑）
//
// 语义全部对齐官方源码：
//   runtime2.go —— P 的 runq [256] + runnext、G 状态枚举
//   proc.go     —— runqput / runqputslow / globrunqgetbatch / runqgrab / runqsteal / sysmon / retake
//   design/24543 —— 异步抢占与 unsafe-point
//
// 关键常量：runq 容量 256；forcePreemptNS = 10ms；sysmon 20us 起步、idle>50 翻倍、封顶 10ms；
// 全局队列每 61 个 schedtick 查一次，批量恒为 len(runq)/2 = 128；
// runqgrab 的 n = n - n/2（偷靠头的一半，ceil）。
package main

import "fmt"

const (
	runqCap        = 256
	forcePreemptNS = 10 * 1000 * 1000 // 10ms
	sysmonMaxUS    = 10 * 1000
	globalEvery    = 61
	globalBatch    = runqCap / 2 // 128

	// G 状态
	gIdle      = 0
	gRunnable  = 1
	gRunning   = 2
	gSyscall   = 3
	gWaiting   = 4
	gDead      = 6
	gCopystack = 8
	gPreempted = 9
	gScan      = 0x1000
)

func statusOf(v int) int { return v & ^gScan }

type G struct {
	goid   int
	name   string
	status int
	unsafe bool // 处于 unsafe-point：不能被异步抢占
}

type P struct {
	idx         int
	runq        []*G
	runnext     *G
	schedtick   uint32
	syscalltick uint32
	m           *M
	// sysmon 记账
	smonSched   int32
	smonWhen    int64
	smonSyscall int32
	syscallWhen int64
}

type M struct {
	mid       int
	p         *P
	curg      *G
	spinning  bool
	inSyscall bool
}

type Scheduler struct {
	gomaxprocs int
	allp       []*P
	globalq    []*G
	haveSysmon bool
	nextMid    int
	handoffs   int
}

func NewScheduler(n int) *Scheduler {
	s := &Scheduler{gomaxprocs: n, haveSysmon: true}
	for i := 0; i < n; i++ {
		s.allp = append(s.allp, &P{idx: i, smonSched: -1, smonSyscall: -1})
	}
	return s
}

// RunqPut：next=true 先占 runnext，把原来那个踢进本地队列
func (s *Scheduler) RunqPut(p *P, g *G, next bool) string {
	if next {
		if !s.haveSysmon {
			next = false // 没有 sysmon 就别用 runnext，否则一对互相唤醒的 G 会饿死别人
		} else {
			old := p.runnext
			p.runnext = g
			if old == nil {
				return "runnext"
			}
			g = old
		}
	}
	if len(p.runq) < runqCap {
		p.runq = append(p.runq, g)
		return "local"
	}
	return s.runqPutSlow(p, g)
}

// runqPutSlow：本地满了，搬走一半（128）+ 新来的这个一起进全局队列
func (s *Scheduler) runqPutSlow(p *P, g *G) string {
	n := len(p.runq) / 2
	batch := append([]*G{}, p.runq[:n]...)
	p.runq = append([]*G{}, p.runq[n:]...)
	batch = append(batch, g)
	s.globalq = append(s.globalq, batch...)
	return fmt.Sprintf("global(%d)", len(batch)) // 129
}

// RunqGet：先 runnext（继承当前时间片），再本地 FIFO
func (s *Scheduler) RunqGet(p *P) *G {
	if p.runnext != nil {
		g := p.runnext
		p.runnext = nil
		return g
	}
	if len(p.runq) > 0 {
		g := p.runq[0]
		p.runq = p.runq[1:]
		return g
	}
	return nil
}

// FindRunnable：runnext/local → 每 61 tick 查全局 → 窃取
func (s *Scheduler) FindRunnable(p *P) (*G, string) {
	if g := s.RunqGet(p); g != nil {
		return g, "local"
	}
	p.schedtick++
	if p.schedtick%globalEvery == 0 && len(s.globalq) > 0 {
		n := globalBatch
		if n > len(s.globalq) {
			n = len(s.globalq)
		}
		batch := append([]*G{}, s.globalq[:n]...)
		s.globalq = s.globalq[n:]
		p.runq = append(p.runq, batch[1:]...)
		return batch[0], "global"
	}
	if g := s.Steal(p); g != nil {
		return g, "steal"
	}
	return nil, "none"
}

// Steal：偷靠头的一半，跑其中最新的那个
func (s *Scheduler) Steal(p *P) *G {
	for _, p2 := range s.allp {
		if p2 == p || (len(p2.runq) == 0 && p2.runnext == nil) {
			continue
		}
		n := len(p2.runq)
		take := n - n/2
		taken := append([]*G{}, p2.runq[:take]...)
		p2.runq = append([]*G{}, p2.runq[take:]...)
		if len(taken) == 0 {
			if p2.runnext != nil { // 最后手段：偷 runnext
				g := p2.runnext
				p2.runnext = nil
				return g
			}
			continue
		}
		g := taken[len(taken)-1] // 这批里最新的先跑
		p.runq = append(p.runq, taken[:len(taken)-1]...)
		return g
	}
	return nil
}

// Sysmon：idle==0 置 20us，idle>50 后翻倍，封顶 10ms
type Sysmon struct {
	delay int
	idle  int
}

func (sy *Sysmon) Tick() int {
	if sy.idle == 0 {
		sy.delay = 20
	} else if sy.idle > 50 {
		sy.delay *= 2
	}
	if sy.delay > sysmonMaxUS {
		sy.delay = sysmonMaxUS
	}
	sy.idle++
	return sy.delay
}

// RetakePreempt：schedtick 没变且超过 10ms 才抢占
func RetakePreempt(p *P, now int64) bool {
	if int32(p.schedtick) != p.smonSched {
		p.smonSched = int32(p.schedtick)
		p.smonWhen = now
		return false
	}
	return p.smonWhen+forcePreemptNS <= now
}

// RetakeSyscall：超过 1 个 sysmon tick 就夺回 P；但没活 + 还有空闲线程 + 不到 10ms 就先不夺
func RetakeSyscall(p *P, now int64, runqEmpty, idleOrSpinning bool) bool {
	if int32(p.syscalltick) != p.smonSyscall {
		p.smonSyscall = int32(p.syscalltick)
		p.syscallWhen = now
		return false
	}
	if runqEmpty && idleOrSpinning && p.syscallWhen+forcePreemptNS > now {
		return false
	}
	return true
}

// AsyncPreempt：任意点都能停，除了 unsafe-point（uintptr 存活 / write barrier 中）
func AsyncPreempt(g *G, now, since int64) bool {
	if g.unsafe {
		return false
	}
	return now-since >= forcePreemptNS
}

// EnterSyscall：解绑 P 并 handoff 给新的 M，并行度不变
func (s *Scheduler) EnterSyscall(m *M) *M {
	p := m.p
	m.inSyscall = true
	m.curg = nil
	if p == nil {
		return nil
	}
	p.m = nil
	p.syscalltick++
	m.p = nil
	nm := &M{mid: s.nextMid, p: p}
	s.nextMid++
	p.m = nm
	s.handoffs++
	return nm
}

func main() {
	s := NewScheduler(2)
	p0, p1 := s.allp[0], s.allp[1]
	for i := 0; i < 8; i++ {
		s.RunqPut(p0, &G{goid: i, name: fmt.Sprintf("v%d", i)}, false)
	}
	g, src := s.FindRunnable(p1)
	fmt.Printf("P1 偷到 %v（来源 %s），本地剩 %d，P0 剩 %d\n",
		g.name, src, len(p1.runq), len(p0.runq))

	sy := &Sysmon{}
	seq := []int{}
	for i := 0; i < 60; i++ {
		seq = append(seq, sy.Tick())
	}
	fmt.Printf("sysmon: 首=%d 第51次=%d 第52次=%d 第60次=%d\n", seq[0], seq[50], seq[51], seq[59])

	for i := 0; i < 256; i++ {
		s.RunqPut(p0, &G{goid: 1000 + i}, false)
	}
	fmt.Println("填满后:", len(p0.runq), "再放一个 ->", s.RunqPut(p0, &G{goid: 9999}, false), "全局", len(s.globalq))

	p := &P{smonSched: 7, schedtick: 7}
	fmt.Println("9.999ms 抢占?", RetakePreempt(p, forcePreemptNS-1), "10ms 抢占?", RetakePreempt(p, forcePreemptNS))
}
