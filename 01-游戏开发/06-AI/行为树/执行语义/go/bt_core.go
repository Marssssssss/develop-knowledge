package main

import (
	"errors"
)

// Package main 是 BehaviorTree.CPP 控制节点/装饰器执行语义的 Go 转写。
//
// 对应官方源码（逐行实读后转写）：
//   src/controls/sequence_node.cpp, fallback_node.cpp, reactive_sequence.cpp,
//   parallel_node.cpp, decorators/inverter_node.cpp, decorators/repeat_node.cpp
//
// 语言差异显式落地：
//   - C++ 的 enum class NodeStatus → Go 的自定义整型常量（取值与官方一致 0..4）
//   - C++ 抛 LogicError → Go 返回 error
//   - 官方 size_t 下标无下溢 → Go 用 int，仅在命中分支自增

type NodeStatus int

const (
	Idle NodeStatus = iota
	Running
	Success
	Failure
	Skipped
)

func (s NodeStatus) String() string {
	return [...]string{"IDLE", "RUNNING", "SUCCESS", "FAILURE", "SKIPPED"}[s]
}

// isStatusActive 对应 basic_types.h：status != IDLE && status != SKIPPED
func isStatusActive(s NodeStatus) bool { return s != Idle && s != Skipped }

var errIdleChild = errors.New("a children should not return IDLE")

type node interface {
	Status() NodeStatus
	ExecuteTick() (NodeStatus, error)
	Halt()
	ResetStatus()
	Name() string
}

func haltChild(c node) {
	if c.Status() == Running {
		c.Halt()
	}
	c.ResetStatus()
}
