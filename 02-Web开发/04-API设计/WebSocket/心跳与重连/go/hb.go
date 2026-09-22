// 610 关闭握手 + 心跳 —— Go 版（仅标准库），配套 ws.go。
//
// 关闭握手（§5.5.1）：
//   - 发出 Close 之后 MUST NOT 再发数据帧。
//   - 未发过就收到 Close，MUST 回一个，且 typically echo 状态码。
//   - 收发齐备即视为已关闭，MUST 关 TCP；server 立即关，client 等一下再关。
//
// 心跳（§5.5.2 / §5.5.3）：
//   - 收到 Ping MUST 回 Pong，除非此前已收到 Close。
//   - 应答 Pong 的载荷必须与所应答 Ping 完全一致。
//   - 对端 MAY 只应答最近一个 Ping ⇒ 不能假设"发 N 个必回 N 个"。
//   - 未被请求的 Pong 是单向心跳，不期待应答。
package main

import (
	"fmt"
	"sort"
)

// 连接状态。用"是否发出 / 是否收到"两个布尔量合成，而不是单枚举字段：
// "已收未发" 与 "已发未收" 都是 closing，但下一步动作完全不同。
const (
	StateOpen    = "open"
	StateClosing = "closing"
	StateClosed  = "closed"
)

// CloseHandshake 跟踪本端在关闭握手中的状态。
type CloseHandshake struct {
	Role       string
	Sent       bool
	Received   bool
	SentCode   int
	RecvCode   int
	Violations []string
}

// State 收发齐备才是 closed。
func (c *CloseHandshake) State() string {
	if c.Sent && c.Received {
		return StateClosed
	}
	if c.Sent || c.Received {
		return StateClosing
	}
	return StateOpen
}

// MaySendData 发出 Close 之后就再也不能发数据帧。
func (c *CloseHandshake) MaySendData() bool { return !c.Sent }

// TcpAction 关闭握手完成后底层 TCP 该怎么处置。
func (c *CloseHandshake) TcpAction() string {
	if c.State() != StateClosed {
		return "keep"
	}
	if c.Role == "server" {
		return "close-immediately"
	}
	return "wait-then-close"
}

func (c *CloseHandshake) SendData() bool {
	if !c.MaySendData() {
		c.Violations = append(c.Violations, "已发出 Close 帧后仍然发送数据帧")
		return false
	}
	return true
}

// OnSendClose 本端发出 Close 帧；重复发要记违规。
func (c *CloseHandshake) OnSendClose(code int) bool {
	if c.Sent {
		c.Violations = append(c.Violations, "重复发送 Close 帧")
		return false
	}
	c.Sent = true
	c.SentCode = code
	return true
}

// OnRecvClose 收到对端 Close 帧；未发过则 MUST 回一个并回显状态码。
func (c *CloseHandshake) OnRecvClose(code int) (bool, int) {
	if c.Received {
		c.Violations = append(c.Violations, "重复收到 Close 帧")
		return false, 0
	}
	c.Received = true
	c.RecvCode = code
	if !c.Sent {
		return true, code
	}
	return false, 0
}

// Heartbeat Ping/Pong 的收发账本，时间为单调秒。
type Heartbeat struct {
	Interval       float64
	PongTimeout    float64
	SingleFlight   bool
	Pending        map[string]float64
	LastPingAt     float64
	HasPing        bool
	LastRecvAt     float64
	HasRecv        bool
	LastRTT        float64
	Unsolicited    int
	MissedPongs    int
}

func NewHeartbeat(interval float64, pongTimeout float64, singleFlight bool) *Heartbeat {
	return &Heartbeat{
		Interval: interval, PongTimeout: pongTimeout, SingleFlight: singleFlight,
		Pending: map[string]float64{},
	}
}

// PingDue 是否该发下一个 Ping。
func (h *Heartbeat) PingDue(now float64) bool {
	if h.SingleFlight && len(h.Pending) > 0 {
		return false
	}
	if !h.HasPing {
		return true
	}
	return now-h.LastPingAt >= h.Interval
}

// SendPing 记一笔待应答的 Ping，载荷受控制帧上限约束。
func (h *Heartbeat) SendPing(payload string, now float64) (int, string, error) {
	if len(payload) > MaxControlPayload {
		return 0, "", fmt.Errorf("Ping 载荷 %d 字节超过 125 上限", len(payload))
	}
	h.Pending[payload] = now
	h.LastPingAt = now
	h.HasPing = true
	return OpPing, payload, nil
}

// RecvPing 收到 Ping：除非已收到 Close，否则 MUST 回 Pong 且载荷原样。
func (h *Heartbeat) RecvPing(payload string, now float64, haveRecvClose bool) (int, string, bool) {
	h.LastRecvAt = now
	h.HasRecv = true
	if haveRecvClose {
		return 0, "", false
	}
	return OpPong, payload, true
}

// RecvPong 匹配得上就算 RTT，匹配不上就是单向心跳。
// RFC 允许对端只应答最近一个 Ping，所以收到匹配项时把更早的待应答项一并作废。
func (h *Heartbeat) RecvPong(payload string, now float64) string {
	h.LastRecvAt = now
	h.HasRecv = true
	sentAt, ok := h.Pending[payload]
	if !ok {
		h.Unsolicited++
		return "unsolicited"
	}
	delete(h.Pending, payload)
	h.LastRTT = now - sentAt
	stale := []string{}
	for k, v := range h.Pending {
		if v <= sentAt {
			stale = append(stale, k)
		}
	}
	sort.Strings(stale)
	for _, k := range stale {
		delete(h.Pending, k)
		h.MissedPongs++
	}
	return "reply"
}

// RecvFrame 收到任意数据帧也要刷新活性计时。
func (h *Heartbeat) RecvFrame(now float64) {
	h.LastRecvAt = now
	h.HasRecv = true
}

// IsAlive 距最后一次收到帧未超过 PongTimeout 即认为连接仍活。
func (h *Heartbeat) IsAlive(now float64) bool {
	if !h.HasRecv {
		return true
	}
	return now-h.LastRecvAt <= h.PongTimeout
}

// Deadline 活性截止时刻。
func (h *Heartbeat) Deadline(now float64) float64 {
	if !h.HasRecv {
		return now + h.PongTimeout
	}
	return h.LastRecvAt + h.PongTimeout
}

// Outstanding 未被应答的 Ping 数。
func (h *Heartbeat) Outstanding() int { return len(h.Pending) }

// SimultaneousClose 双方同时发 Close：两边都收发过，直接进 closed。
func SimultaneousClose() (string, string) {
	me := &CloseHandshake{Role: "client"}
	peer := &CloseHandshake{Role: "server"}
	me.OnSendClose(1000)
	peer.OnSendClose(1000)
	me.OnRecvClose(1000)
	peer.OnRecvClose(1000)
	return me.State(), peer.State()
}
