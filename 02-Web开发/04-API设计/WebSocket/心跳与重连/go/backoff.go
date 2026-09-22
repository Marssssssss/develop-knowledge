// 610 重连退避 + Go 版演示入口 —— Go 版（仅标准库），配套 ws.go / hb.go。
//
// 口径说明（避免把两套东西混为一谈）：
//   - RFC 6455 **没有**规定重连策略，它只定义关闭码语义（§7.4.1）与区间归属
//     （§7.4.2）。所以"收到哪个码该不该重连"是工程策略，依据 §7.4.1 的文字。
//   - 退避算法本身取自 gRPC 官方 doc/connection-backoff.md（master）：
//     INITIAL_BACKOFF=1s / MULTIPLIER=1.6 / MAX_BACKOFF=120s / JITTER=0.2 /
//     MIN_CONNECT_TIMEOUT=20s。原文要求替代实现必须让同时开始的退避发散，
//     且不得比该算法更频繁地尝试连接。
//
// 运行：go run ws.go hb.go backoff.go
package main

import (
	"fmt"
	"math"
	"math/rand"
)

// gRPC connection-backoff.md 的固定参数。
const (
	InitialBackoff     = 1.0
	BackoffMultiplier  = 1.6
	MaxBackoff         = 120.0
	BackoffJitter      = 0.2
	MinConnectTimeout  = 20.0
)

// Backoff gRPC 连接退避算法（含 jitter 与重置）。
type Backoff struct {
	Initial            float64
	Multiplier         float64
	Max                float64
	Jitter             float64
	MinConnectTimeout  float64
	Current            float64
}

// NewBackoff 按官方默认参数构造。
func NewBackoff() *Backoff {
	return &Backoff{
		Initial: InitialBackoff, Multiplier: BackoffMultiplier,
		Max: MaxBackoff, Jitter: BackoffJitter,
		MinConnectTimeout: MinConnectTimeout, Current: InitialBackoff,
	}
}

// NextWait 返回本次应等待的秒数，并把游标推进到下一档。
// 原文是先算等待、后推进游标，所以第一次调用给出的是 INITIAL_BACKOFF。
func (b *Backoff) NextWait(rnd *rand.Rand) float64 {
	spread := b.Jitter * b.Current
	wait := b.Current + (rnd.Float64()*2-1)*spread
	b.Current = math.Min(b.Current*b.Multiplier, b.Max)
	if wait < 0 {
		return 0
	}
	return wait
}

// ConnectDeadline 单次连接尝试的超时：不短于 MinConnectTimeout。
// 原文 TryConnect(Max(current_deadline, now() + MIN_CONNECT_TIMEOUT))。
func (b *Backoff) ConnectDeadline(now float64) float64 {
	return math.Max(now+b.Current, now+b.MinConnectTimeout)
}

// Reset 退避必须能重置，否则"新连接"与"断线重连"行为不一致。
func (b *Backoff) Reset() { b.Current = b.Initial }

// Reconnect 依据 RFC 6455 §7.4.1 各码语义给出的工程策略表。
var Reconnect = map[int]bool{
	1000: false, 1001: true, 1002: false, 1003: false,
	1007: false, 1008: false, 1009: false, 1010: false,
	1011: true, 1012: true, 1013: true, 1014: true, 1015: false,
}

// ImmediateRetry 服务端明确要求"稍后再来"的码，不必等退避。
var ImmediateRetry = map[int]bool{1012: true, 1013: true}

// ShouldReconnect 未知码一律重连（保守但不会永久停摆）。
func ShouldReconnect(code int) bool {
	if v, ok := Reconnect[code]; ok {
		return v
	}
	return true
}

// WaitBeforeRetry 不该重连时返回 (0, false)。
func WaitBeforeRetry(code int, b *Backoff, rnd *rand.Rand) (float64, bool) {
	if !ShouldReconnect(code) {
		return 0, false
	}
	if ImmediateRetry[code] {
		return 0, true
	}
	return b.NextWait(rnd), true
}

func must5(cond bool, label string) {
	if !cond {
		panic("断言失败: " + label)
	}
}

func main() {
	rnd := rand.New(rand.NewSource(7))

	// ---- 控制帧 ----
	must5(MaxControlPayload == 125, "控制帧载荷上限 125")
	must5(IsControl(OpPing) && IsControl(OpClose) && IsControl(OpPong), "0x8/0x9/0xA 是控制帧")
	must5(!IsControl(OpText) && !IsControl(OpContinuation), "数据帧不是控制帧")
	must5(len(ValidateControlFrame(OpPing, true, 125)) == 0, "125 字节合法")
	must5(len(ValidateControlFrame(OpPing, true, 126)) == 1, "126 字节非法")
	must5(len(ValidateControlFrame(OpPing, false, 4)) == 1, "控制帧不得分片")
	must5(len(ValidateControlFrame(OpText, true, 4)) == 1, "数据帧不能当控制帧")
	must5(len(ValidateControlFrame(0xB, true, 4)) == 1, "0xB 是保留控制帧")
	must5(MayInterject(OpPing, true, 4), "控制帧可插在分片消息中间")

	// ---- Close body ----
	body, errs := EncodeCloseBody(1000, true, "bye")
	must5(len(errs) == 0 && len(body) == 5 && body[0] == 0x03 && body[1] == 0xe8,
		"Close body 状态码网络字节序")
	code, hasCode, reason, errs := ParseCloseBody(body)
	must5(len(errs) == 0 && hasCode && code == 1000 && reason == "bye", "Close body 往返")
	_, hasCode, _, _ = ParseCloseBody([]byte{})
	must5(!hasCode, "空 body → 无状态码")
	_, _, _, errs = ParseCloseBody([]byte{0x03})
	must5(len(errs) == 1, "1 字节 body 非法")
	_, errs = EncodeCloseBody(1005, true, "")
	must5(len(errs) == 1, "1005 不得写入")
	_, errs = EncodeCloseBody(1006, true, "")
	must5(len(errs) == 1, "1006 不得写入")
	_, errs = EncodeCloseBody(1015, true, "")
	must5(len(errs) == 1, "1015 不得写入")

	// ---- 状态码区间 ----
	must5(CodeRange(999) == "0-999 不使用" && CodeRange(1000) != CodeRange(3000), "区间分界")
	must5(CodeRange(4000) == "4000-4999 私有用途（不可注册）", "4000 私有")
	must5(IsRegisterable(3000) && IsRegisterable(3999), "3000-3999 可注册")
	must5(!IsRegisterable(4000) && !IsRegisterable(2999), "4000/2999 不可注册")
	must5(len(StatusCodeIssues(1000)) == 0 && len(StatusCodeIssues(1013)) == 0, "已分配码可发")
	must5(len(StatusCodeIssues(2000)) == 1, "1016-2999 未分配")
	must5(len(StatusCodeIssues(4500)) == 1, "4500 私有不可注册")

	// ---- 关闭握手 ----
	cli := &CloseHandshake{Role: "client"}
	srv := &CloseHandshake{Role: "server"}
	must5(cli.State() == StateOpen && cli.MaySendData(), "初始可发数据")
	must5(cli.OnSendClose(1000), "发出 Close")
	must5(cli.State() == StateClosing && !cli.MaySendData(), "发出后进入 closing")
	must5(!cli.SendData(), "发出 Close 后发数据被拒")
	must5(len(cli.Violations) == 1, "违规被记录")
	should, echo := srv.OnRecvClose(1000)
	must5(should && echo == 1000, "未发过则 MUST 回并回显码")
	must5(srv.OnSendClose(echo), "server 回 Close")
	must5(srv.State() == StateClosed && srv.TcpAction() == "close-immediately",
		"server MUST 立即关 TCP")
	cli.OnRecvClose(1000)
	must5(cli.State() == StateClosed && cli.TcpAction() == "wait-then-close",
		"client SHOULD 等 server 先关")
	me, peer := SimultaneousClose()
	must5(me == StateClosed && peer == StateClosed, "同时关闭 → 双方 closed")

	// ---- 心跳 ----
	hb := NewHeartbeat(30.0, 10.0, true)
	now := 500.0
	must5(hb.PingDue(now), "初始应发 Ping")
	hb.SendPing("a", now)
	must5(hb.Outstanding() == 1, "记一笔待应答")
	must5(!hb.PingDue(now+100), "single_flight：未应答前不再发")
	must5(hb.RecvPong("a", now+0.5) == "reply", "匹配 Pong → reply")
	must5(math.Abs(hb.LastRTT-0.5) < 1e-9, "RTT = 0.5")
	must5(hb.RecvPong("zz", now+1) == "unsolicited" && hb.Unsolicited == 1,
		"非匹配 Pong → 单向心跳")
	op, payload, ok := hb.RecvPing("ping-1", now+2, false)
	must5(ok && op == OpPong && payload == "ping-1", "回 Pong 且载荷原样回显")
	_, _, ok = hb.RecvPing("x", now+3, true)
	must5(!ok, "已收过 Close 则不必回 Pong")
	hb2 := NewHeartbeat(1.0, 10.0, false)
	hb2.SendPing("p1", now)
	hb2.SendPing("p2", now+1)
	must5(hb2.Outstanding() == 2, "两笔待应答")
	must5(hb2.RecvPong("p2", now+1.2) == "reply", "最近一个被应答")
	must5(hb2.MissedPongs == 1 && hb2.Outstanding() == 0, "更早的作废")
	hb3 := NewHeartbeat(30.0, 10.0, true)
	hb3.RecvFrame(now)
	must5(hb3.IsAlive(now+10), "恰好等于阈值仍算活")
	must5(!hb3.IsAlive(now+10.001), "超过阈值即判死")
	must5(math.Abs(hb3.Deadline(now)-(now+10.0)) < 1e-9, "deadline 计算")

	// ---- 重连退避 ----
	b := NewBackoff()
	must5(math.Abs(b.Current-InitialBackoff) < 1e-9, "游标从 INITIAL_BACKOFF 起")
	b.NextWait(rnd)
	must5(math.Abs(b.Current-1.6) < 1e-9, "游标推进 1.0*1.6")
	b.NextWait(rnd)
	must5(math.Abs(b.Current-1.6*1.6) < 1e-9, "游标再乘 1.6")
	capped := NewBackoff()
	for i := 0; i < 40; i++ {
		capped.NextWait(rnd)
	}
	must5(math.Abs(capped.Current-MaxBackoff) < 1e-9, "游标被 MAX_BACKOFF 封顶")
	must5(math.Abs(capped.NextWait(rnd)-MaxBackoff) < 1e-9, "封顶后等待恒为 120s")
	b.Reset()
	must5(math.Abs(b.Current-InitialBackoff) < 1e-9, "reset 回到 INITIAL_BACKOFF")
	must5(NewBackoff().ConnectDeadline(now) >= now+MinConnectTimeout,
		"连接尝试超时不短于 MIN_CONNECT_TIMEOUT")
	must5(!ShouldReconnect(1000) && ShouldReconnect(1001), "1000 不重连 / 1001 重连")
	must5(ShouldReconnect(1011) && !ShouldReconnect(1015), "1011 重连 / 1015 不重连")
	must5(ShouldReconnect(4999), "未知码保守重连")
	_, retry := WaitBeforeRetry(1000, NewBackoff(), rnd)
	must5(!retry, "1000 不重连")
	w1013, retry := WaitBeforeRetry(1013, NewBackoff(), rnd)
	must5(retry && w1013 == 0, "1013 立即重试")

	fmt.Printf("Close(1000,'bye') = % x\n", body)
	fmt.Println("client tcp:", cli.TcpAction(), "/ server tcp:", srv.TcpAction())
	fmt.Println("心跳 RTT:", hb.LastRTT, "/ 单向心跳计数:", hb.Unsolicited)
	fmt.Println("all go assertions passed")
}
