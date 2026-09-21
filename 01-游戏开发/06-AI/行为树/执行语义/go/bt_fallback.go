package main

import (
	"fmt"
)

// ---------------------------------------------------------------- Fallback

type fallback struct {
	name            string
	children        []node
	currentChildIdx int
	skippedCount    int
	status          NodeStatus
}

func newFallback(name string, children ...node) *fallback {
	return &fallback{name: name, children: children, status: Idle}
}

func (f *fallback) Name() string       { return f.name }
func (f *fallback) Status() NodeStatus { return f.status }
func (f *fallback) ResetStatus()       { f.status = Idle }
func (f *fallback) Halt() {
	f.currentChildIdx = 0
	f.skippedCount = 0
	for _, c := range f.children {
		haltChild(c)
	}
}
func (f *fallback) resetChildren() {
	for _, c := range f.children {
		haltChild(c)
	}
}

func (f *fallback) ExecuteTick() (NodeStatus, error) {
	n := len(f.children)
	if !isStatusActive(f.status) {
		f.skippedCount = 0
	}
	f.status = Running
	for f.currentChildIdx < n {
		child := f.children[f.currentChildIdx]
		st, err := child.ExecuteTick()
		if err != nil {
			return Idle, fmt.Errorf("[%s]: %w", f.name, err)
		}
		switch st {
		case Running:
			return Running, nil
		case Success:
			f.resetChildren()
			f.currentChildIdx = 0
			f.status = Success
			return Success, nil
		case Failure:
			f.currentChildIdx++
		case Skipped:
			f.currentChildIdx++
			f.skippedCount++
		default:
			return Idle, fmt.Errorf("[%s]: %w", f.name, errIdleChild)
		}
	}
	allSkipped := f.skippedCount == n
	if f.currentChildIdx == n {
		f.resetChildren()
		f.currentChildIdx = 0
		f.skippedCount = 0
	}
	if allSkipped {
		f.status = Skipped
		return Skipped, nil
	}
	f.status = Failure
	return Failure, nil
}
