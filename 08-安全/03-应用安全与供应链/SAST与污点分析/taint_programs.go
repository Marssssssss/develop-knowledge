package main

// 测试程序集：与 Python 版 programs() 一一对应。
// 单独成文件是为了满足单源文件 ≤300 行的约束（仓库约定）。

type program struct {
	Name string
	Body []Stmt
}

func mkLet(d string, e Expr) Stmt  { return Stmt{Op: "let", Dst: d, E: e} }
func mkSink(v, label string) Stmt  { return Stmt{Op: "sink", Dst: v, Label: label} }
func mkFlag(n string, v bool) Stmt { return Stmt{Op: "setflag", Dst: n, Val: v} }

func programs() []program {
	return []program{
		// P1 先读后写：flow-insensitive 分不清顺序 → 误报
		{"P1_read_before_write", []Stmt{mkSink("y", "S"), mkLet("y", src())}},
		// P2 非保值步骤：DataFlow 漏报、TaintTracking 命中
		{"P2_concat", []Stmt{
			mkLet("x", src()), mkLet("q", cat(konst(), cp("x"))), mkSink("q", "S")}},
		// P3 sanitizer 作为 barrier：三档都应判干净
		{"P3_sanitizer", []Stmt{
			mkLet("x", src()), mkLet("y", san(cp("x"))), mkSink("y", "S")}},
		// P4 相关分支：只有 x 被 sanitize 时 ok 才为 True。
		//    path-insensitive 在汇合点丢失相关性 → 误报；path-sensitive 能排除
		{"P4_correlated_flag", []Stmt{
			mkLet("x", src()), mkFlag("ok", false),
			Stmt{Op: "if", Flag: "cond", Then: []Stmt{
				mkLet("x", san(cp("x"))), mkFlag("ok", true)}},
			Stmt{Op: "if", Flag: "ok", Then: []Stmt{mkSink("x", "S")}}}},
		// P4' 打掉相关性：两条分支都置 ok=True → 确实存在漏洞路径
		{"P4b_broken_correlation", []Stmt{
			mkLet("x", src()), mkFlag("ok", false),
			Stmt{Op: "if", Flag: "cond", Then: []Stmt{
				mkLet("x", san(cp("x"))), mkFlag("ok", true)},
				Else: []Stmt{mkFlag("ok", true)}},
			Stmt{Op: "if", Flag: "ok", Then: []Stmt{mkSink("x", "S")}}}},
		// P5 隐式流：数据经控制流传递，三档全部漏报
		{"P5_implicit_flow", []Stmt{
			mkLet("x", src()),
			Stmt{Op: "if", Flag: "x",
				Then: []Stmt{mkFlag("admin", true)}, Else: []Stmt{mkFlag("admin", false)}},
			mkLet("y", ff("admin")), mkSink("y", "S")}},
		// P6 真阳性：任何精度都该报出来
		{"P6_true_positive", []Stmt{
			mkLet("x", src()), mkLet("y", cp("x")), mkSink("y", "S")}},
	}
}
