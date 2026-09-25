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

func (EvalVisitor) VisitNum(n *Num) int { return n.Value }
func (EvalVisitor) VisitAdd(a *Add) int { return a.L.Accept(a.rv) + a.R.Accept(a.rv) }

// Go 无方法内引用接收者的捷径,这里以字段承载同一访问者实例(示意)。
type Add2 struct {
	L, R Node
	rv   EvalVisitor
}

type DoubleVisitor struct{}

// 加操作 = 新访问者,元素层级零改动。
func (DoubleVisitor) VisitNum(n *Num) int { return n.Value * 2 }
func (DoubleVisitor) VisitAdd(a *Add) int {
	return a.L.Accept(DoubleVisitor{}) + a.R.Accept(DoubleVisitor{})
}

func main() {
	expr := &Add{&Num{1}, &Num{2}}
	fmt.Println(expr.Accept(EvalVisitor{}))   // 3
	fmt.Println(expr.Accept(DoubleVisitor{})) // 6
}
