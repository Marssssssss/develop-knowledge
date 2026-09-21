package main

import (
	"fmt"
)

// ---------------------------------------------------------------- ReactiveSequence

// throwIfMultipleRunning 对应官方 static 成员，默认 false。
var throwIfMultipleRunning = false

type reactiveSequence struct {
	name         string
	children     []node
	runningChild int
	status       NodeStatus
}

func newReactiveSequence(name string, children ...node) *reactiveSequence {
	return &reactiveSequence{name: name, children: children, runningChild: -1, status: Idle}
}

func (r *reactiveSequence) Name() string       { return r.name }
func (r *reactiveSequence) Status() NodeStatus { return r.status }
func (r *reactiveSequence) ResetStatus()       { r.status = Idle }
func (r *reactiveSequence) Halt() {
	r.runningChild = -1
	for _, c := range r.children {
		haltChild(c)
	}
}
func (r *reactiveSequence) resetChildren() {
	for _, c := range r.children {
		haltChild(c)
	}
}

func (r *reactiveSequence) ExecuteTick() (NodeStatus, error) {
	allSkipped := true
	if r.status == Idle {
		r.runningChild = -1
	}
	r.status = Running
	for index, child := range r.children {
		st, err := child.ExecuteTick()
		if err != nil {
			return Idle, fmt.Errorf("[%s]: %w", r.name, err)
		}
		allSkipped = allSkipped && st == Skipped
		switch st {
		case Running:
			for i := range r.children {
				if i != index {
					haltChild(r.children[i])
				}
			}
			if r.runningChild == -1 {
				r.runningChild = index
			} else if throwIfMultipleRunning && r.runningChild != index {
				return Idle, fmt.Errorf("[%s]: only a single child can return RUNNING", r.name)
			}
			return Running, nil
		case Failure:
			r.resetChildren()
			r.status = Failure
			return Failure, nil
		case Skipped:
			haltChild(r.children[index])
		case Success:
		default:
			return Idle, fmt.Errorf("[%s]: %w", r.name, errIdleChild)
		}
	}
	r.resetChildren()
	if allSkipped {
		r.status = Skipped
		return Skipped, nil
	}
	r.status = Success
	return Success, nil
}
