// main.go — 与 python/templ.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type GameAI struct {
	// 模板方法依赖的步骤全部以函数字段表达(Go 无继承,组合即模板)。
	collectResources func() []string
	buildStructures  func() []string   // 抽象步骤:必须提供
	buildUnits       func() []string   // 抽象步骤:必须提供
	beforeBuildHook  func() []string   // 钩子:可选
}

// TemplateTurn 定死骨架;nil 的钩子跳过,缺失的抽象步骤 panic。
func (a *GameAI) TemplateTurn() []string {
	if a.buildStructures == nil || a.buildUnits == nil {
		panic("abstract steps must be provided")
	}
	var out []string
	if a.beforeBuildHook != nil {
		out = append(out, a.beforeBuildHook()...)
	}
	if a.collectResources != nil {
		out = append(out, a.collectResources()...)
	}
	out = append(out, a.buildStructures()...)
	out = append(out, a.buildUnits()...)
	return out
}

func main() {
	orc := &GameAI{
		collectResources: func() []string { return []string{"collect:shared"} },
		buildStructures:  func() []string { return []string{"build:farm"} },
		buildUnits:       func() []string { return []string{"build:peon"} },
	}
	fmt.Println(orc.TemplateTurn())
	// 不提供 beforeBuildHook 也能跑(空体钩子);Go 的组合式模板与 Strategy 同构。
}
