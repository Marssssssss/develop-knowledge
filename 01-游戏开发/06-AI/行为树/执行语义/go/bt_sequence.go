package main

import (
	"fmt"
)

// ---------------------------------------------------------------- Sequence

type sequence struct {
	name                    string
	children                []node
	currentChildIdx         int
	skippedCount            int
	status                  NodeStatus
}

func newSequence(name string, children ...node) *sequence {
	return &sequence{name: name, children: children, status: Idle}
}

func (s *sequence) Name() string       { return s.name }
func (s *sequence) Status() NodeStatus { return s.status }
func (s *sequence) Halt() {
	s.currentChildIdx = 0
	s.skippedCount = 0
	for _, c := range s.children {
		haltChild(c)
	}
}
func (s *sequence) ResetStatus() { s.status = Idle }

func (s *sequence) resetChildren() {
	for _, c := range s.children {
		haltChild(c)
	}
}

func (s *sequence) ExecuteTick() (NodeStatus, error) {
	n := len(s.children)
	if !isStatusActive(s.status) {
		s.skippedCount = 0
	}
	s.status = Running
	for s.currentChildIdx < n {
		child := s.children[s.currentChildIdx]
		st, err := child.ExecuteTick()
		if err != nil {
			return Idle, fmt.Errorf("[%s]: %w", s.name, err)
		}
		switch st {
		case Running:
			return Running, nil
		case Failure:
			s.resetChildren()
			s.currentChildIdx = 0
			s.status = Failure
			return Failure, nil
		case Success:
			s.currentChildIdx++
		case Skipped:
			s.currentChildIdx++
			s.skippedCount++
		default:
			return Idle, fmt.Errorf("[%s]: %w", s.name, errIdleChild)
		}
	}
	allSkipped := s.skippedCount == n
	if s.currentChildIdx == n {
		s.resetChildren()
		s.currentChildIdx = 0
		s.skippedCount = 0
	}
	if allSkipped {
		s.status = Skipped
		return Skipped, nil
	}
	s.status = Success
	return Success, nil
}
