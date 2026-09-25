// main.go — 与 python/strat.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type Strategy interface{ Execute(amount int) int }

type Normal struct{}
func (Normal) Execute(a int) int { return a }

type VIP struct{}
func (VIP) Execute(a int) int { return a / 2 }

type Context struct{ s Strategy }

func NewContext(s Strategy) *Context { return &Context{s: s} }
func (c *Context) SetStrategy(s Strategy) { c.s = s }
func (c *Context) Checkout(amount int) int { return c.s.Execute(amount) }

func branchVersion(kind string, amount int) int {
	switch kind {
	case "normal":
		return amount
	case "vip":
		return amount / 2
	}
	panic(kind)
}

func main() {
	ctx := NewContext(Normal{})
	fmt.Println(ctx.Checkout(100)) // 100,与分支版一致
	ctx.SetStrategy(VIP{})
	fmt.Println(ctx.Checkout(100), branchVersion("vip", 100)) // 50 50
}
