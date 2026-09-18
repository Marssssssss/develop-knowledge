// 污点分析的精确性阶梯（Go 版）：flow-insensitive / flow-sensitive / path-sensitive。
//
// 对应 CodeQL 的 DataFlow（只跟保值步骤）与 TaintTracking（追加非保值步骤，
// 如 "SELECT " + x）。IR 与 Python 版一一对应，三档精度的判定结果应当一致。
package main

import (
	"fmt"
	"os"
	"sort"
)

type Kind int

const (
	KSource Kind = iota
	KConst
	KCopy
	KCat
	KSanitize
	KFromFlag
)

type Expr struct {
	K Kind
	V string  // KCopy / KFromFlag 的变量名
	S []Expr  // KCat / KSanitize 的子表达式
}

// Stmt.Op ∈ {"let", "sink", "setflag", "if"}
//   let     : Dst = 变量, E = 表达式
//   sink    : Dst = 被传入的变量, Label = sink 名
//   setflag : Dst = 标记名, Val = 布尔值
//   if      : Flag = 条件标记名, Then/Else = 两个分支
type Stmt struct {
	Op    string
	Dst   string
	Label string
	Flag  string
	Val   bool
	E     Expr
	Then  []Stmt
	Else  []Stmt
}

func src() Expr          { return Expr{K: KSource} }
func konst() Expr        { return Expr{K: KConst} }
func cp(v string) Expr   { return Expr{K: KCopy, V: v} }
func cat(a, b Expr) Expr { return Expr{K: KCat, S: []Expr{a, b}} }
func san(a Expr) Expr    { return Expr{K: KSanitize, S: []Expr{a}} }
func ff(n string) Expr   { return Expr{K: KFromFlag, V: n} }

// mode ∈ {"flow", "taint"}：flow 不传播 KCat（非保值），taint 传播。
func evalE(e Expr, env map[string]bool, flags map[string]*bool, mode string) bool {
	switch e.K {
	case KSource:
		return true
	case KConst:
		return false
	case KCopy:
		return env[e.V]
	case KCat:
		if mode == "flow" {
			return false
		}
		return evalE(e.S[0], env, flags, mode) || evalE(e.S[1], env, flags, mode)
	case KSanitize:
		return false // isBarrier
	case KFromFlag:
		return false // 隐式流：不跟踪
	}
	return false
}

func cloneEnv(m map[string]bool) map[string]bool {
	out := make(map[string]bool, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func cloneFlags(m map[string]*bool) map[string]*bool {
	out := make(map[string]*bool, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func flatten(stmts []Stmt) []Stmt {
	out := []Stmt{}
	for _, s := range stmts {
		if s.Op == "if" {
			out = append(out, flatten(s.Then)...)
			out = append(out, flatten(s.Else)...)
		} else {
			out = append(out, s)
		}
	}
	return out
}

// flowInsensitive：忽略顺序与分支，取**所有赋值的并集**迭代到不动点。
// 注意并集语义：用"后写覆盖"会在同一变量有冲突赋值时振荡不收敛。
func flowInsensitive(prog []Stmt, mode string) []string {
	flat := flatten(prog)
	env := map[string]bool{}
	for {
		changed := false
		for _, s := range flat {
			if s.Op != "let" {
				continue
			}
			v := evalE(s.E, env, nil, mode) || env[s.Dst]
			if env[s.Dst] != v {
				env[s.Dst] = v
				changed = true
			}
		}
		if !changed {
			break
		}
	}
	out := []string{}
	for _, s := range flat {
		if s.Op == "sink" && env[s.Dst] {
			out = append(out, s.Label)
		}
	}
	return out
}

func mergeEnv(a, b map[string]bool) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		out[k] = a[k]
	}
	for k := range b {
		out[k] = out[k] || b[k]
	}
	return out
}

// mergeFlags：两侧取值不一致 → nil（"不确定"）。这是相关分支误报的根源。
func mergeFlags(a, b map[string]*bool) map[string]*bool {
	out := map[string]*bool{}
	for k := range a {
		out[k] = a[k]
	}
	for k := range b {
		if av, ok := out[k]; !ok || av == nil || b[k] == nil || *av != *b[k] {
			out[k] = nil
		}
	}
	return out
}

// flowSensitive：按序执行，分支汇合处做并集（丢失分支间相关性）。
func flowSensitive(prog []Stmt, mode string) []string {
	rep := []string{}
	var run func([]Stmt, map[string]bool, map[string]*bool) (map[string]bool, map[string]*bool)
	run = func(stmts []Stmt, env map[string]bool, flags map[string]*bool) (map[string]bool, map[string]*bool) {
		for _, s := range stmts {
			switch s.Op {
			case "let":
				env[s.Dst] = evalE(s.E, env, flags, mode)
			case "sink":
				if env[s.Dst] {
					rep = append(rep, s.Label)
				}
			case "setflag":
				b := s.Val
				flags[s.Dst] = &b
			case "if":
				te, tf := run(s.Then, cloneEnv(env), cloneFlags(flags))
				ee, ef := run(s.Else, cloneEnv(env), cloneFlags(flags))
				env = mergeEnv(te, ee)
				flags = mergeFlags(tf, ef)
			}
		}
		return env, flags
	}
	run(prog, map[string]bool{}, map[string]*bool{})
	return rep
}

// pathSensitive：枚举可行路径；条件标记已知时只走可行一侧。
func pathSensitive(prog []Stmt, mode string) []string {
	seen := map[string]bool{}
	var run func([]Stmt, map[string]bool, map[string]*bool)
	run = func(stmts []Stmt, env map[string]bool, flags map[string]*bool) {
		for i, s := range stmts {
			switch s.Op {
			case "let":
				env[s.Dst] = evalE(s.E, env, flags, mode)
			case "sink":
				if env[s.Dst] {
					seen[s.Label] = true
				}
			case "setflag":
				b := s.Val
				flags[s.Dst] = &b
			case "if":
				var branches [][]Stmt
				f := flags[s.Flag]
				switch {
				case f == nil:
					branches = [][]Stmt{s.Then, s.Else}
				case *f:
					branches = [][]Stmt{s.Then}
				default:
					branches = [][]Stmt{s.Else}
				}
				// 分叉后必须带上 if 之后的语句，否则后面的 sink 永远走不到
				rest := stmts[i+1:]
				for _, b := range branches {
					nb := append(append([]Stmt{}, b...), rest...)
					run(nb, cloneEnv(env), cloneFlags(flags))
				}
				return
			}
		}
	}
	run(prog, map[string]bool{}, map[string]*bool{})
	out := []string{}
	for k := range seen {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func main() {
	fail := 0
	fmt.Printf("%-26s %-14s %-14s %-14s %s\n", "program", "insensitive", "sensitive", "path", "truth")
	for _, p := range programs() {
		fi := flowInsensitive(p.Body, "taint")
		fs := flowSensitive(p.Body, "taint")
		ps := pathSensitive(p.Body, "taint")
		fmt.Printf("%-26s %-14s %-14s %-14s\n", p.Name,
			joinOr(fi), joinOr(fs), joinOr(ps))
		// 与 Python 版的 run_all() 结果逐一对齐
		want := map[string][]string{
			"P1_read_before_write":   {"S", "", ""},
			"P2_concat":              {"S", "S", "S"},
			"P3_sanitizer":           {"", "", ""},
			"P4_correlated_flag":     {"S", "S", ""},
			"P4b_broken_correlation": {"S", "S", "S"},
			"P5_implicit_flow":       {"", "", ""},
			"P6_true_positive":       {"S", "S", "S"},
		}[p.Name]
		if joinOr(fi) != dash(want[0]) || joinOr(fs) != dash(want[1]) || joinOr(ps) != dash(want[2]) {
			fmt.Printf("  MISMATCH want=%v/%v/%v\n", want[0], want[1], want[2])
			fail++
		}
	}
	if joinOr(flowSensitive(programs()[1].Body, "flow")) != "-" {
		fmt.Println("  MISMATCH: flow-mode 应当漏报 P2（拼接非保值）")
		fail++
	}
	if fail > 0 {
		fmt.Printf("FAILED %d\n", fail)
		os.Exit(1)
	}
	fmt.Println("all checks passed")
}

func joinOr(xs []string) string {
	if len(xs) == 0 {
		return "-"
	}
	out := ""
	for i, x := range xs {
		if i > 0 {
			out += ","
		}
		out += x
	}
	return out
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}
