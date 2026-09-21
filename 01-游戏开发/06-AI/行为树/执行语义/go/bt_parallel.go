package main

import (
	"fmt"
)

// ---------------------------------------------------------------- Parallel

type parallel struct {
	name             string
	children         []node
	successThreshold int
	failureThreshold int
	completed        map[int]bool
	successCount     int
	failureCount     int
	status           NodeStatus
}

func newParallel(name string, successThreshold, failureThreshold int, children ...node) *parallel {
	return &parallel{name: name, children: children,
		successThreshold: successThreshold, failureThreshold: failureThreshold,
		completed: map[int]bool{}, status: Idle}
}

func (p *parallel) Name() string       { return p.name }
func (p *parallel) Status() NodeStatus { return p.status }
func (p *parallel) ResetStatus()       { p.status = Idle }
func (p *parallel) Halt() {
	p.clear()
	for _, c := range p.children {
		haltChild(c)
	}
}
func (p *parallel) clear() {
	p.completed = map[int]bool{}
	p.successCount = 0
	p.failureCount = 0
}
func (p *parallel) resetChildren() {
	for _, c := range p.children {
		haltChild(c)
	}
}

// succThreshold 对应 successThreshold()：负值表示「从末尾数」
func (p *parallel) succThreshold() int {
	if p.successThreshold < 0 {
		return maxInt(len(p.children)+p.successThreshold+1, 0)
	}
	return p.successThreshold
}

func (p *parallel) failThreshold() int {
	if p.failureThreshold < 0 {
		return maxInt(len(p.children)+p.failureThreshold+1, 0)
	}
	return p.failureThreshold
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func (p *parallel) ExecuteTick() (NodeStatus, error) {
	n := len(p.children)
	if n < p.succThreshold() {
		return Idle, fmt.Errorf("[%s]: number of children is less than threshold. Can never succeed", p.name)
	}
	if n < p.failThreshold() {
		return Idle, fmt.Errorf("[%s]: number of children is less than threshold. Can never fail", p.name)
	}
	p.status = Running
	skipped := 0
	for i := 0; i < n; i++ {
		if !p.completed[i] {
			st, err := p.children[i].ExecuteTick()
			if err != nil {
				return Idle, fmt.Errorf("[%s]: %w", p.name, err)
			}
			switch st {
			case Skipped:
				skipped++
			case Success:
				p.completed[i] = true
				p.successCount++
			case Failure:
				p.completed[i] = true
				p.failureCount++
			case Running:
			default:
				return Idle, fmt.Errorf("[%s]: %w", p.name, errIdleChild)
			}
		}
		required := p.succThreshold()
		if p.successCount >= required ||
			(p.successThreshold < 0 && p.successCount+skipped >= required) {
			p.clear()
			p.resetChildren()
			p.status = Success
			return Success, nil
		}
		if (n-p.failureCount) < required || p.failureCount == p.failThreshold() {
			p.clear()
			p.resetChildren()
			p.status = Failure
			return Failure, nil
		}
	}
	if skipped == n {
		p.status = Skipped
		return Skipped, nil
	}
	return Running, nil
}
