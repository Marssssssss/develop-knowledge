// plancache.go — 与 plancache.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type PreparedStmt struct {
	HasParams    bool
	Factor       float64
	customCosts  []float64
	GenericCost  float64
	UsingGeneric bool
}

// Run:auto 模式状态机(前五次定制,之后比较切换)。
func (p *PreparedStmt) Run(valueCost float64) string {
	if !p.HasParams {
		return "generic"
	}
	if len(p.customCosts) < 5 {
		p.customCosts = append(p.customCosts, valueCost)
		return "custom"
	}
	avg := 0.0
	for _, c := range p.customCosts {
		avg += c
	}
	avg /= float64(len(p.customCosts))
	if p.GenericCost <= p.Factor*avg {
		p.UsingGeneric = true
		return "generic"
	}
	return "custom"
}

func main() {
	ps := &PreparedStmt{HasParams: true, Factor: 1.1}
	for i := 0; i < 5; i++ {
		ps.Run(100)
	}
	ps.GenericCost = 105
	fmt.Println("6th with generic=105:", ps.Run(100), "using:", ps.UsingGeneric) // generic true

	ps2 := &PreparedStmt{HasParams: true, Factor: 1.1}
	for i := 0; i < 5; i++ {
		ps2.Run(100)
	}
	ps2.GenericCost = 5000
	fmt.Println("6th with generic=5000:", ps2.Run(100)) // custom
}
