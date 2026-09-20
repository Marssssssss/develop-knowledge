// TCP 流量控制与零窗口探测 —— Go 版模型
//
// 与 tcpwin_model.py 同题：把 RFC 9293 §3.8.6 的发送/接收 SWS 判据、
// §3.8.6.1 的零窗口探测退避、RFC 7323 §2.2/§2.3/§2.4 的窗口扩大与回缩，
// 用 Go 的整数类型重写一遍，重点验证位移在 64 位下的行为。
package main

import "fmt"

const (
	mss        = 1460
	fsNum      = 1 // 发送端 Fs = 1/2
	fsDen      = 2
	frNum      = 1 // 接收端 Fr = 1/2
	frDen      = 2
	maxShift   = 14
	unscaledMax = (1 << 16) - 1
)

// Receiver 建模 RCV.BUFF / RCV.USER / RCV.NXT / RCV.WND
type Receiver struct {
	Buff   int64
	MSS    int64
	Shift  int
	User   int64
	Nxt    int64
	Wnd    int64
	Updates int
	Acks   int
}

func NewReceiver(buff int64, shift int) *Receiver {
	return &Receiver{Buff: buff, MSS: mss, Shift: shift, Nxt: 1000, Wnd: buff}
}

// Reduction 是「可用但尚未通告」的第 3 段空间
func (r *Receiver) Reduction() int64 { return r.Buff - r.User - r.Wnd }

func (r *Receiver) maybeUpdate() bool {
	thr := frNum * r.Buff / frDen
	if r.MSS < thr {
		thr = r.MSS
	}
	if r.Reduction() >= thr {
		r.Wnd = r.Buff - r.User
		r.Updates++
		return true
	}
	return false
}

// Receive 收到并确认 n 字节：右边界 RCV.NXT+RCV.WND 保持不动
func (r *Receiver) Receive(n int64) bool {
	room := r.Buff - r.User
	if n > room {
		n = room
	}
	r.User += n
	r.Nxt += n
	if r.Wnd-n < 0 {
		r.Wnd = 0
	} else {
		r.Wnd -= n
	}
	return r.maybeUpdate()
}

// Consume 应用读走 n 字节
func (r *Receiver) Consume(n int64) bool {
	if n > r.User {
		n = r.User
	}
	r.User -= n
	return r.maybeUpdate()
}

// Advertise 产生线上 16 位 SEG.WND；SYN 段 MUST NOT be scaled
func (r *Receiver) Advertise(syn bool) int64 {
	r.Acks++
	if syn {
		if r.Wnd > unscaledMax {
			return unscaledMax
		}
		return r.Wnd
	}
	return r.Wnd >> r.Shift
}

// Sender 建模 SND.UNA / SND.NXT / SND.WND / Max(SND.WND)
type Sender struct {
	MSS     int64
	Shift   int
	Una     int64
	Nxt     int64
	Wnd     int64
	MaxWnd  int64
}

func (s *Sender) OnAck(ackno, segWnd int64, syn bool) {
	s.Una = ackno
	if syn {
		s.Wnd = segWnd
	} else {
		s.Wnd = segWnd << s.Shift
	}
	if s.Wnd > s.MaxWnd {
		s.MaxWnd = s.Wnd
	}
}

func (s *Sender) Usable() int64 { return s.Una + s.Wnd - s.Nxt }

// MaySend RFC 9293 §3.8.6.2.1 四条判据
func (s *Sender) MaySend(D int64, pushed, override bool) bool {
	U := s.Usable()
	if U <= 0 {
		return false
	}
	m := D
	if U < m {
		m = U
	}
	if m >= s.MSS {
		return true // (1)
	}
	if pushed && s.Nxt == s.Una && D <= U {
		return true // (2)
	}
	if s.Nxt == s.Una && m >= fsNum*s.MaxWnd/fsDen {
		return true // (3)
	}
	return override // (4)
}

func (s *Sender) Send(n int64) int64 {
	U := s.Usable()
	if n > U {
		n = U
	}
	s.Nxt += n
	return n
}

// Prober 零窗口探测状态机：首探在 RTO 之后，间隔指数增长
type Prober struct {
	RTO     float64
	Interval float64
	NextAt  float64
	HasNext bool
	Sent    int
}

func NewProber(rto float64) *Prober { return &Prober{RTO: rto, Interval: rto} }

func (p *Prober) Observe(now float64, zero bool) bool {
	if !zero {
		p.HasNext = false
		p.Interval = p.RTO
		return false
	}
	if !p.HasNext {
		p.HasNext = true
		p.NextAt = now + p.RTO
		return false
	}
	if now < p.NextAt {
		return false
	}
	p.Sent++
	p.Interval *= 2
	p.NextAt = now + p.Interval
	return true
}

// QuantizationLoss 右移再左移造成的窗口损失
func QuantizationLoss(wnd int64, shift int) int64 {
	return wnd - ((wnd >> shift) << shift)
}

// ShiftFor 按缓冲大小选 shift.cnt，上限 14
func ShiftFor(buff int64) int {
	s := 0
	for s < maxShift && (buff>>s) > unscaledMax {
		s++
	}
	return s
}

// ---------------------------------------------------------------- 自检

var nAssert, nFail int

func check(label string, cond bool, detail string) {
	nAssert++
	if cond {
		fmt.Printf("ok   %-52s %s\n", label, detail)
		return
	}
	nFail++
	fmt.Printf("FAIL %-52s %s\n", label, detail)
}

func main() {
	fmt.Println("== 可用窗口 U ==")
	s := &Sender{MSS: mss}
	s.OnAck(0, 10000, false)
	check("初始 U = 10000", s.Usable() == 10000, fmt.Sprintf("U=%d", s.Usable()))
	s.Send(3000)
	check("发出 3000 后 U = 7000", s.Usable() == 7000, fmt.Sprintf("U=%d", s.Usable()))

	fmt.Println("\n== 发送端 SWS 判据 ==")
	s2 := &Sender{MSS: mss}
	s2.OnAck(0, 10000, false)
	check("(1) 够一个 MSS → 发", s2.MaySend(2000, false, false), "")
	check("(1) 负向：400 字节且不 PUSH → 不发", !s2.MaySend(400, false, false), "")
	check("(3) 6000 >= Fs*Max=5000 → 发", s2.MaySend(6000, false, false), "")
	check("(4) override → 发", s2.MaySend(100, false, true), "")

	s4 := &Sender{MSS: mss}
	s4.OnAck(0, 1000, false) // Fs*Max = 500 < MSS
	check("(3) 隔离后 600 >= 500 → 发", s4.MaySend(600, false, false), "")
	check("(3) 负向：400 < 500 → 不发", !s4.MaySend(400, false, false), "")
	check("(1) 负向：U 上限 1000 < MSS，1 字节不发", !s4.MaySend(1, false, false), "")

	fmt.Println("\n== 接收端 SWS ==")
	r := NewReceiver(65536, 0)
	r.Receive(2000)
	check("收 2000 后右边界固定", r.Wnd == 63536 && r.Reduction() == 0,
		fmt.Sprintf("wnd=%d", r.Wnd))
	r.Consume(1000)
	check("reduction=1000 < MSS → 不更新", r.Updates == 0 && r.Wnd == 63536, "")
	edge := r.Nxt + r.Wnd
	r.Consume(600)
	check("reduction 达 1600 → 更新窗口", r.Updates == 1 && r.Wnd == 65136,
		fmt.Sprintf("wnd=%d", r.Wnd))
	check("右边界一次性前移 1600", (r.Nxt+r.Wnd)-edge == 1600,
		fmt.Sprintf("advance=%d", (r.Nxt+r.Wnd)-edge))

	fmt.Println("\n== 零窗口 ==")
	rz := NewReceiver(4096, 0)
	rz.Receive(4096)
	check("应用不读 → RCV.WND 归零", rz.Wnd == 0 && rz.User == rz.Buff, "")
	check("零窗口下仍回 ACK 且窗口为 0", rz.Advertise(false) == 0 && rz.Nxt == 5096, "")

	fmt.Println("\n== 零窗口探测退避 ==")
	p := NewProber(1.0)
	var fires []float64
	for i := 0; i < 40; i++ {
		t := float64(i) * 0.5
		if p.Observe(t, true) {
			fires = append(fires, t)
		}
	}
	check("探测时刻为 1 / 3 / 7 / 15", len(fires) == 4 &&
		fires[0] == 1.0 && fires[1] == 3.0 && fires[2] == 7.0 && fires[3] == 15.0,
		fmt.Sprintf("%v", fires))
	p2 := NewProber(1.0)
	p2.Observe(0.0, true)
	p2.Observe(1.0, true)
	p2.Observe(2.0, false)
	check("窗口重开后状态机重置", !p2.HasNext && p2.Interval == 1.0, "")

	fmt.Println("\n== 窗口扩大 ==")
	check("ShiftFor(1GiB) == 14", ShiftFor(1<<30) == 14, fmt.Sprintf("%d", ShiftFor(1<<30)))
	rs := NewReceiver(1<<20, 14)
	seg := rs.Advertise(false)
	check("1 MiB 在 shift=14 下得 SEG.WND=64", seg == 64, fmt.Sprintf("%d", seg))
	ss := &Sender{Shift: 14}
	ss.OnAck(0, seg, false)
	check("发送端还原得 1 MiB", ss.Wnd == 1<<20, fmt.Sprintf("%d", ss.Wnd))
	check("SYN 段窗口不缩放（截断到 65535）", rs.Advertise(true) == 65535, "")

	fmt.Println("\n== 量化与回缩 ==")
	check("窗口 1000 在 shift=4 下损失 8", QuantizationLoss(1000, 4) == 8, "")
	check("1024 在 shift=4 下零损失", QuantizationLoss(1024, 4) == 0, "")
	check("1000→991 造成窗口回缩 62→61", 1000>>4 == 62 && 991>>4 == 61, "")
	check("992 仍落在同一档，不回缩", 992>>4 == 62, "")

	fmt.Printf("\n---- %d 项断言，失败 %d 项 ----\n", nAssert, nFail)
	if nFail > 0 {
		panic("有断言失败")
	}
	fmt.Println("ALL PASS")
}
