// Godot 4 AnimationNodeStateMachine 推进与交叉淡入的 Go 侧转写，
// 与 python/statemachine.py 同协议。
package main

import (
	"errors"
	"fmt"
	"math"
)

// CMPEpsilon 取自 core/math/math_defs.h。
const CMPEpsilon = 0.00001

// SwitchMode 对应 AnimationNodeStateMachineTransition::SwitchMode。
type SwitchMode int

// 三种切换模式。
const (
	SwitchImmediate SwitchMode = iota
	SwitchSync
	SwitchAtEnd
)

// AdvanceMode 对应过渡的推进模式。
type AdvanceMode int

// 三种推进模式。
const (
	AdvanceDisabled AdvanceMode = iota
	AdvanceEnabled
	AdvanceAuto
)

// Transition 为一条状态机过渡。
type Transition struct {
	From, To        string
	XfadeTime       float64
	XfadeCurve      func(float64) float64
	SwitchMode      SwitchMode
	Priority        int
	AdvanceMode     AdvanceMode
	AdvanceCond     string
	IsReset         bool
	BreakLoopAtEnd  bool
}

// NewTransition 校验 xfade_time（源码 set_xfade_time 有 ERR_FAIL_COND(p_xfade < 0)）。
func NewTransition(from, to string, xfade float64) (*Transition, error) {
	if xfade < 0 {
		return nil, errors.New("xfade_time 必须 >= 0")
	}
	return &Transition{From: from, To: to, XfadeTime: xfade, AdvanceMode: AdvanceAuto}, nil
}

// StateMachine 持有过渡表与条件参数。
type StateMachine struct {
	Transitions []*Transition
	Conditions  map[string]bool
}

// SetCondition 写入 "conditions/<name>"。
func (s *StateMachine) SetCondition(name string, v bool) {
	if s.Conditions == nil {
		s.Conditions = map[string]bool{}
	}
	s.Conditions["conditions/"+name] = v
}

// Playback 为状态机播放器。
type Playback struct {
	sm          *StateMachine
	Current     string
	FadingFrom  string
	FadingTime  float64
	FadingPos   float64
	Curve       func(float64) float64
	Path        []string
	Position    float64
	Teleported  bool
	LoopAborted bool
}

// NewPlayback 建立播放器并停在初始状态。
func NewPlayback(sm *StateMachine, start string) *Playback {
	return &Playback{sm: sm, Current: start}
}

func (p *Playback) checkAdvance(tr *Transition) bool {
	if tr.AdvanceMode != AdvanceAuto {
		return false
	}
	if tr.AdvanceCond != "" && !p.sm.Conditions["conditions/"+tr.AdvanceCond] {
		return false
	}
	return true
}

func (p *Playback) findNext() *Transition {
	if len(p.Path) > 0 { // travel 路径：不看条件、不比优先级
		for _, tr := range p.sm.Transitions {
			if tr.AdvanceMode == AdvanceDisabled {
				continue
			}
			if tr.From == p.Current && tr.To == p.Path[0] {
				return tr
			}
		}
		return nil
	}
	best := 1e20
	idx := -1
	for i, tr := range p.sm.Transitions {
		if tr.AdvanceMode == AdvanceDisabled {
			continue
		}
		if tr.From == p.Current && p.checkAdvance(tr) {
			if float64(tr.Priority) <= best { // <= 让下标大者在平局时胜出
				best = float64(tr.Priority)
				idx = i
			}
		}
	}
	if idx == -1 {
		return nil
	}
	return p.sm.Transitions[idx]
}

func (p *Playback) transitionRecursive() {
	seen := []string{p.Current}
	for {
		nxt := p.findNext()
		if nxt == nil {
			return
		}
		looped := false
		for _, s := range seen {
			if s == nxt.To {
				looped = true
			}
		}
		if looped { // 源码：检测到环路就 WARN 并 break
			p.LoopAborted = true
			return
		}
		seen = append(seen, nxt.To)
		if nxt.XfadeTime > 0 {
			p.FadingFrom = p.Current
			p.FadingTime = nxt.XfadeTime
			p.FadingPos = 0
		} else {
			p.FadingFrom = ""
			p.FadingTime = 0
			p.FadingPos = 0
		}
		if len(p.Path) > 0 {
			p.Path = p.Path[1:]
		}
		prev := p.Position
		p.Current = nxt.To
		p.Curve = nxt.XfadeCurve
		if nxt.SwitchMode == SwitchSync {
			p.Position = prev
		} else if nxt.IsReset {
			p.Position = 0
		}
		if p.FadingTime > 0 {
			return // 有淡入必须先处理淡入
		}
	}
}

// Process 推进一帧并返回各状态的权重。
func (p *Playback) Process(delta float64, seek bool) map[string]float64 {
	p.transitionRecursive()
	fade := 1.0
	if p.FadingTime > 0 && p.FadingFrom != "" {
		if !seek {
			p.FadingPos += math.Abs(delta) // 注意是 abs
		}
		fade = math.Min(1.0, p.FadingPos/p.FadingTime)
	}
	if p.Curve != nil {
		fade = p.Curve(fade)
	}
	if fade < CMPEpsilon {
		fade = CMPEpsilon
	}
	out := map[string]float64{p.Current: fade}
	if p.FadingFrom != "" {
		inv := 1.0 - fade
		if inv < CMPEpsilon {
			inv = CMPEpsilon
		}
		out[p.FadingFrom] += inv
	}
	return out
}

// Travel 规划路径；不可达时退化为 teleport。
func (p *Playback) Travel(target string) {
	if route := p.makeTravelPath(target); len(route) > 0 {
		p.Path = route
		return
	}
	p.Current = target
	p.Position = 0
	p.FadingFrom = ""
	p.FadingTime = 0
	p.FadingPos = 0
	p.Teleported = true
}

func (p *Playback) makeTravelPath(target string) []string {
	if target == p.Current {
		return nil
	}
	seen := map[string]bool{p.Current: true}
	type node struct {
		name string
		acc  []string
	}
	queue := []node{{p.Current, nil}}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		for _, tr := range p.sm.Transitions {
			if tr.AdvanceMode == AdvanceDisabled || tr.From != cur.name || seen[tr.To] {
				continue
			}
			acc := append(append([]string{}, cur.acc...), tr.To)
			if tr.To == target {
				return acc
			}
			seen[tr.To] = true
			queue = append(queue, node{tr.To, acc})
		}
	}
	return nil
}

// Start 直接 teleport 到目标并清掉淡入。
func (p *Playback) Start(name string) {
	p.Current = name
	p.Position = 0
	p.FadingFrom = ""
	p.FadingTime = 0
	p.FadingPos = 0
	p.Path = nil
	p.Teleported = true
}

func main() {
	tr, _ := NewTransition("Idle", "Walk", 0.5)
	tr.AdvanceCond = "move"
	sm := &StateMachine{Transitions: []*Transition{tr}}
	sm.SetCondition("move", true)
	pb := NewPlayback(sm, "Idle")
	for i := 0; i < 6; i++ {
		fmt.Println("[交叉淡入] 第", i+1, "帧:", pb.Process(0.1, false))
	}

	t1, _ := NewTransition("A", "B", 0)
	t1.Priority, t1.AdvanceCond = 5, "go"
	t2, _ := NewTransition("A", "C", 0)
	t2.Priority, t2.AdvanceCond = 1, "go"
	sm2 := &StateMachine{Transitions: []*Transition{t1, t2}}
	sm2.SetCondition("go", true)
	pb2 := NewPlayback(sm2, "A")
	pb2.Process(0.1, false)
	fmt.Println("[优先级] 选中:", pb2.Current)
}
