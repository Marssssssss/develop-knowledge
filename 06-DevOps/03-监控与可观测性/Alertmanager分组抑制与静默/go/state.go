package main

// Prometheus 侧告警状态与 Alertmanager 侧通知节奏。
//
// 状态机(官方 alerting rules 文档):
//
//	inactive --(表达式命中)--> pending --(连续满足 for)--> firing
//
// 任一评估不命中 -> for 计时器归零;keep_firing_for(默认 0)让条件不再满足后
// 仍保持 firing 一段时间,用于防抖。合成序列 ALERTS{alertname, alertstate}
// 活跃期间恒为 1,不再活跃即 stale。
//
// 通知节奏(官方 configuration 文档):新 group 等 group_wait 后首发;已有 group
// 每 group_interval 检查一次,有告警新 firing / 有告警 resolved 就发,否则看
// 距上次发送是否已过 repeat_interval。

const (
	StateInactive = "inactive"
	StatePending  = "pending"
	StateFiring   = "firing"
)

// AlertState 单条告警的状态机。带 Has* 布尔是为了区分"时刻恰为 0"与"未设置"。
type AlertState struct {
	State       string
	ActiveAt    float64
	FiringAt    float64
	LastMetAt   float64
	HasActive   bool
	HasFiring   bool
	HasMet      bool
	Evaluations int
}

// NewAlertState 初始处于 inactive。
func NewAlertState() *AlertState { return &AlertState{State: StateInactive} }

// Evaluate 执行一次规则评估并返回新状态。
func (s *AlertState) Evaluate(now float64, matched bool, forS, keepFiringForS float64) string {
	s.Evaluations++
	if matched {
		s.LastMetAt, s.HasMet = now, true
		if s.State == StateInactive {
			s.State, s.ActiveAt, s.HasActive = StatePending, now, true
		}
		// forS=0 时首次评估即转 firing(没有 for 子句的规则首次评估就激活)
		if s.State == StatePending && now-s.ActiveAt >= forS {
			s.State, s.FiringAt, s.HasFiring = StateFiring, now, true
		}
		return s.State
	}
	if s.State == StatePending {
		s.State, s.ActiveAt, s.HasActive = StateInactive, 0, false
		return s.State
	}
	if s.State == StateFiring {
		if keepFiringForS > 0 && now-s.LastMetAt < keepFiringForS {
			return s.State
		}
		s.State, s.FiringAt, s.HasFiring = StateInactive, 0, false
	}
	return s.State
}

// Active pending 与 firing 都算活跃。
func (s *AlertState) Active() bool {
	return s.State == StatePending || s.State == StateFiring
}

// SampleValue ALERTS 的样本值:活跃为 (1,true);不活跃为 (0,false) 表示 stale。
func (s *AlertState) SampleValue() (int, bool) {
	if s.Active() {
		return 1, true
	}
	return 0, false
}

// AlertsLabels 合成序列 ALERTS 的标签;不活跃时返回 ok=false(不输出样本)。
func AlertsLabels(alertname string, s *AlertState) (map[string]string, bool) {
	if !s.Active() {
		return nil, false
	}
	return map[string]string{"alertname": alertname, "alertstate": s.State}, true
}

// ---------------------------------------------------------------- 通知节奏

// Send 一次已发送的通知。
type Send struct {
	At     float64
	Reason string
}

// Group 一个聚合组。FirstSeen 是组内首条告警到达的时刻。
type Group struct {
	Key       string
	FirstSeen float64
	LastSent  float64
	HasSent   bool
	LastFPS   map[string]bool
	Sends     []Send
}

// NewGroup 建组;首条告警到达时刻为 firstSeen。
func NewGroup(key string, firstSeen float64) *Group {
	return &Group{Key: key, FirstSeen: firstSeen, LastFPS: map[string]bool{}}
}

func sameSet(a, b map[string]bool) bool {
	if len(a) != len(b) {
		return false
	}
	for k := range a {
		if !b[k] {
			return false
		}
	}
	return true
}

func copySet(a map[string]bool) map[string]bool {
	out := make(map[string]bool, len(a))
	for k := range a {
		out[k] = true
	}
	return out
}

// NotificationEngine 按 group_interval 节拍检查每个组是否需要发送通知。
type NotificationEngine struct {
	GroupWait      float64
	GroupInterval  float64
	RepeatInterval float64
}

// NewNotificationEngine 返回官方默认值:30s / 5m / 4h。
func NewNotificationEngine() *NotificationEngine {
	return &NotificationEngine{GroupWait: 30, GroupInterval: 300, RepeatInterval: 14400}
}

// EffectiveRepeat repeat_interval 向上取整到 group_interval 的倍数。
func (e *NotificationEngine) EffectiveRepeat() float64 {
	return EffectiveRepeatInterval(e.RepeatInterval, e.GroupInterval)
}

// NextCheckAt 该组下一次被检查的时刻。
func (e *NotificationEngine) NextCheckAt(g *Group) float64 {
	if !g.HasSent {
		return g.FirstSeen + e.GroupWait
	}
	return g.LastSent + e.GroupInterval
}

// Decide 返回 "" / "first" / "changed" / "repeat"。
func (e *NotificationEngine) Decide(g *Group, now float64, fps map[string]bool) string {
	if !g.HasSent {
		if len(fps) == 0 {
			return "" // group_wait 内全部 resolved:不发通知
		}
		if now >= g.FirstSeen+e.GroupWait {
			return "first"
		}
		return ""
	}
	if now-g.LastSent < e.GroupInterval {
		return ""
	}
	if !sameSet(fps, g.LastFPS) {
		return "changed"
	}
	if now-g.LastSent >= e.EffectiveRepeat() {
		return "repeat"
	}
	return ""
}

// Dispatch 判定发送并记账;返回 reason 或空串。
func (e *NotificationEngine) Dispatch(g *Group, now float64, fps map[string]bool) string {
	reason := e.Decide(g, now, fps)
	if reason == "" {
		return ""
	}
	g.LastSent, g.HasSent = now, true
	g.LastFPS = copySet(fps)
	g.Sends = append(g.Sends, Send{At: now, Reason: reason})
	return reason
}
