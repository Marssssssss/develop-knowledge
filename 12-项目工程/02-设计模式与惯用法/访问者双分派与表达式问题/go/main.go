// main.go — 与 python/visitor.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type Visitor interface {
	VisitNum(*Num) int
	VisitAdd(*Add) int
}

type Node interface{ Accept(Visitor) int }

type Num struct{ Value int }

// 双分派:元素替访问者选方法。
func (n *Num) Accept(v Visitor) int { return v.VisitNum(n) }

type Add struct{ L, R Node }

func (a *Add) Accept(v Visitor) int { return v.VisitAdd(a) }

type EvalVisitor struct{}

// 命名接收者让同一访问者实例能递归传下去。
func (v EvalVisitor) VisitNum(n *Num) int { return n.Value }
func (v EvalVisitor) VisitAdd(a *Add) int { return a.L.Accept(v) + a.R.Accept(v) }

type DoubleVisitor struct{}

// 加操作 = 新访问者,元素层级零改动。
func (v DoubleVisitor) VisitNum(n *Num) int { return n.Value * 2 }
func (v DoubleVisitor) VisitAdd(a *Add) int { return a.L.Accept(v) + a.R.Accept(v) }

func main() {
	expr := &Add{&Num{1}, &Num{2}}
	fmt.Println(expr.Accept(EvalVisitor{}))   // 3
	fmt.Println(expr.Accept(DoubleVisitor{})) // 6
}
