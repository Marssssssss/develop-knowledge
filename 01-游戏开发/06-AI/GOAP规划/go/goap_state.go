package main

// Package main 是 ReGoap 的 GOAP 规划器（反向 A*）的 Go 转写。
//
// 对应官方源码（luxkun/ReGoap，master，逐行实读后转写）：
//   ReGoap/Planner/ReGoapNode.cs、ReGoap/Planner/AStar.cs、
//   ReGoap/Planner/ReGoapPlanner.cs、ReGoap/Core/ReGoapState.cs、
//   ReGoap/Core/ReGoapCondition.cs
//
// 语言差异显式落地：
//   - C# 的 ConcurrentDictionary<T,W> → Go 的 map[string]any；
//     因为 map 遍历无序，本实现显式把状态序列化成「排序后的 key=value」字符串才能当 map 的键；
//   - C# 的 out 参数取不到键时给 default(W) → Go 用零值 nil，isMatch 对 nil 走等值比较；
//   - C# float → Go float64。
package main

import (
	"fmt"
	"sort"
	"strings"
)

// ---------------------------------------------------------------- 条件匹配

type condOp string

const (
	opEqual condOp = "eq"
	opGe    condOp = "ge"
)

type cond struct {
	op    condOp
	value any
}

func ge(v any) cond { return cond{op: opGe, value: v} }

func (c cond) satisfiedByRaw(candidate any) bool {
	switch c.op {
	case opEqual:
		return candidate == c.value
	case opGe:
		n, ok := toFloat(candidate)
		if !ok {
			return false
		}
		v, _ := toFloat(c.value)
		return n >= v
	}
	return false
}

func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case int:
		return float64(x), true
	case float64:
		return x, true
	}
	return 0, false
}

func isMatch(required, candidate any) bool {
	if rc, ok := required.(cond); ok {
		return rc.satisfiedByRaw(candidate)
	}
	if cc, ok := candidate.(cond); ok {
		return cc.satisfiedByRaw(required)
	}
	return required == candidate
}

func areCompatible(left, right any) bool {
	if lc, ok := left.(cond); ok {
		return lc.satisfiedByRaw(right)
	}
	if rc, ok := right.(cond); ok {
		return rc.satisfiedByRaw(left)
	}
	return left == right
}
