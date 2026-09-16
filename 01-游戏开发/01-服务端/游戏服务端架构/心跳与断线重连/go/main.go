// 应用层可靠性:ack 位图 / 序号回绕 / RTT EMA / 丢包推断 / 重连去重(Go 版)。
// 依据 Glenn Fiedler《Reliability and Congestion Avoidance over UDP》:
// 包头 [proto][seq][ack][ack_bits];恒定 33 个 ack/包;永不重发同一序号;
// 1 秒未 ack 判丢;RTT EMA α=10%;回绕用差值-半量程技巧。
package main

import (
	"fmt"
	"os"
)

const (
	mask        = 0xFFFF
	half        = 0x8000
	ackWindow   = 32
	lossTimeout = 1000
	rttAlpha    = 0.1
)

// seqMoreRecent:16 位回绕安全比较(差值 < 半量程按大小比,否则反向)。
func seqMoreRecent(s1, s2 int) bool {
	d := (s1 - s2) & mask
	return d != 0 && d < half
}

// Reliability:单向可靠层状态机(发送方跟踪 ack,接收方维护位图)。
type Reliability struct {
	sentAt      map[int]int   // seq -> 发送时刻
	acked       map[int]bool
	remoteSeq   int           // 收到的最高序号(-1 表示未收到)
	recvWindow  []int         // 最近 33 个已收序号
	rttMs       float64
	hasRtt      bool
}

func newReliability() *Reliability {
	return &Reliability{sentAt: map[int]int{}, acked: map[int]bool{}, remoteSeq: -1}
}

func (r *Reliability) onRecv(seq int) {
	if r.remoteSeq < 0 || seqMoreRecent(seq, r.remoteSeq) {
		r.remoteSeq = seq
	}
	r.recvWindow = append(r.recvWindow, seq)
	if len(r.recvWindow) > ackWindow+1 {
		r.recvWindow = r.recvWindow[1:]
	}
}

// makeAck:恒定 33 个 ack:ack + 32 位位图(冗余对抗 ack 本身丢包)。
func (r *Reliability) makeAck() (int, uint32) {
	if r.remoteSeq < 0 {
		return 0, 0
	}
	have := map[int]bool{}
	for _, s := range r.recvWindow {
		have[s] = true
	}
	var bits uint32
	for n := 1; n <= ackWindow; n++ {
		if have[(r.remoteSeq-n)&mask] {
			bits |= 1 << uint(n-1)
		}
	}
	return r.remoteSeq, bits
}

func (r *Reliability) onSend(seq, nowMs int) {
	r.sentAt[seq] = nowMs
}

// processAck:对端位图 -> 标记送达 + RTT EMA(α=10%)。
func (r *Reliability) processAck(ack int, bits uint32, nowMs int) {
	for n := 0; n <= ackWindow; n++ {
		seq := (ack - n) & mask
		hit := n == 0 || bits>>(uint(n)-1)&1 == 1
		if !hit || r.acked[seq] {
			continue
		}
		r.acked[seq] = true
		if t, ok := r.sentAt[seq]; ok {
			sample := float64(nowMs - t)
			if !r.hasRtt {
				r.rttMs, r.hasRtt = sample, true
			} else {
				r.rttMs = r.rttMs*(1-rttAlpha) + sample*rttAlpha
			}
		}
	}
}

// inferLost:1 秒未 ack 即判丢。
func (r *Reliability) inferLost(nowMs int) []int {
	var lost []int
	for s, t := range r.sentAt {
		if !r.acked[s] && nowMs-t > lossTimeout {
			lost = append(lost, s)
		}
	}
	return lost
}

var failures int

func check(label string, cond bool) {
	if !cond {
		failures++
		fmt.Printf("[FAIL] %s\n", label)
		return
	}
	fmt.Printf("[ok] %s\n", label)
}

func main() {
	// ---- 1. 序号回绕 ----
	check("回绕:2 比 65535 新", seqMoreRecent(2, 65535))
	check("回绕:0 比 65535 新", seqMoreRecent(0, 65535))
	check("不回绕:5 比 3 新", seqMoreRecent(5, 3))
	check("回绕:65535 不比 2 新", !seqMoreRecent(65535, 2))
	check("相等不算更新", !seqMoreRecent(7, 7))

	// ---- 2. ack 位图编码 ----
	r := newReliability()
	for _, s := range []int{10, 11, 13, 42} {
		r.onRecv(s)
	}
	ack, bits := r.makeAck()
	check("ack 取最高已收序号 42", ack == 42)
	check("位图:41 未收 bit0=0", bits&1 == 0)
	check("位图:10=42-32 命中 bit32", bits>>31&1 == 1)
	check("位图:11=42-31 命中 bit31", bits>>30&1 == 1)
	check("位图:12 未收 bit30=0", bits>>29&1 == 0)

	// ---- 3. 双向交换 + 冗余 ack(数据丢 15/100,回程 ack 丢 20%) ----
	A, B := newReliability(), newReliability()
	now, latency := 0, 37
	delivered := map[int]bool{}
	for i := 0; i < 100; i++ {
		if i%7 != 3 { // 数据包送达
			B.onRecv(i)
			delivered[i] = true
		}
		A.onSend(i, now)
		if i%5 != 1 { // B 每收一包回一个携带 ack 的包(20% 被丢)
			a, b := B.makeAck()
			A.processAck(a, b, now+latency)
		}
		now += 33
	}
	complete := true
	for s := range delivered {
		if !A.acked[s] {
			complete = false
		}
	}
	check("回程丢 20% ack 包后,送达集仍完整", complete)
	extra := false
	for s := range A.acked {
		if !delivered[s] {
			extra = true
		}
	}
	check("丢的数据包不会出现在 acked", !extra)
	check("RTT EMA 收敛到链路延迟量级", A.hasRtt && A.rttMs >= 30 && A.rttMs <= 45)

	// ---- 4. 丢包推断 ----
	lost := map[int]bool{}
	for _, s := range A.inferLost(now + 1100) {
		lost[s] = true
	}
	match := len(lost) == 15
	for i := 0; i < 100; i++ {
		if (i%7 == 3) != lost[i] {
			match = false
		}
	}
	check("1.1s 后丢包推断 == 被丢的 15 个", match)

	// ---- 5. 断线重连:消息号去重 + 未送达重发(新序号) ----
	type msg struct {
		id   int
		data string
	}
	seen := map[int]bool{}
	var deliveredMsgs []msg
	outbox := map[int]string{}
	for m := 0; m < 20; m++ {
		outbox[m] = fmt.Sprintf("msg-%d", m)
	}
	nextSeq := 0
	pump := func(drop func(seq int) bool) {
		for id := 0; id < 20; id++ {
			data, ok := outbox[id]
			if !ok {
				continue
			}
			seq := nextSeq
			nextSeq = (nextSeq + 1) & mask // 重发永远用新序号
			if drop(seq) {
				continue
			}
			if !seen[id] { // 消息号去重:重连重发可能重复到达
				seen[id] = true
				deliveredMsgs = append(deliveredMsgs, msg{id, data})
			}
			delete(outbox, id)
		}
	}
	drops := map[int]bool{5: true, 11: true, 17: true}
	pump(func(seq int) bool { return drops[seq] })
	check("第一段后服务端收到 17 条", len(deliveredMsgs) == 17)
	pump(func(seq int) bool { return false }) // 断线 1.2s 后重连
	check("重连后 20 条全部投递", len(deliveredMsgs) == 20)
	check("消息号去重:无重复投递", len(seen) == 20)
	check("客户端发件箱清空", len(outbox) == 0)

	if failures > 0 {
		fmt.Printf("\n%d 项断言失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\n全部断言通过")
}
