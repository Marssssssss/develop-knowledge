package main

import (
	"fmt"
)

// ---------------------------------------------------------------- Inverter / Repeat

type inverter struct {
	name   string
	child  node
	status NodeStatus
}

func newInverter(name string, child node) *inverter {
	return &inverter{name: name, child: child, status: Idle}
}

func (v *inverter) Name() string       { return v.name }
func (v *inverter) Status() NodeStatus { return v.status }
func (v *inverter) ResetStatus()       { v.status = Idle }
func (v *inverter) Halt()              {}
func (v *inverter) resetChild()        { haltChild(v.child) }

func (v *inverter) ExecuteTick() (NodeStatus, error) {
	v.status = Running
	st, err := v.child.ExecuteTick()
	if err != nil {
		return Idle, err
	}
	switch st {
	case Success:
		v.resetChild()
		v.status = Failure
		return Failure, nil
	case Failure:
		v.resetChild()
		v.status = Success
		return Success, nil
	case Running, Skipped:
		v.status = st
		return st, nil
	}
	return Idle, fmt.Errorf("[%s]: %w", v.name, errIdleChild)
}

type repeat struct {
	name        string
	child       node
	numCycles   int
	repeatCount int
	status      NodeStatus
}

func newRepeat(name string, numCycles int, child node) *repeat {
	return &repeat{name: name, child: child, numCycles: numCycles, status: Idle}
}

func (r *repeat) Name() string       { return r.name }
func (r *repeat) Status() NodeStatus { return r.status }
func (r *repeat) ResetStatus()       { r.status = Idle }
func (r *repeat) Halt() {
	r.repeatCount = 0
	haltChild(r.child)
}
func (r *repeat) resetChild() { haltChild(r.child) }

func (r *repeat) ExecuteTick() (NodeStatus, error) {
	r.status = Running
	doLoop := true
	for doLoop {
		st, err := r.child.ExecuteTick()
		if err != nil {
			return Idle, err
		}
		switch st {
		case Success:
			r.repeatCount++
			doLoop = r.repeatCount < r.numCycles || r.numCycles == -1
			r.resetChild()
		case Failure:
			r.repeatCount = 0
			r.resetChild()
			r.status = Failure
			return Failure, nil
		case Running:
			return Running, nil
		case Skipped:
			r.resetChild() // 官方注释：不重置 repeat_count_
			r.status = Skipped
			return Skipped, nil
		default:
			return Idle, fmt.Errorf("[%s]: %w", r.name, errIdleChild)
		}
	}
	r.repeatCount = 0
	r.status = Success
	return Success, nil
}
