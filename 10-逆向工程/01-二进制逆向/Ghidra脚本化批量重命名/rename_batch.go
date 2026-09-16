package main

// Ghidra 批量重命名的 Go 同题实现:计划 → 预演 → 事务提交(符号表模型见 ghidra_symbols.go)。
//
// 依据(本轮实读的官方文档):
//   * Ghidra 自带 BatchRename.java 用于模式化批量改名,官方 Symbol Management 文档的
//     "Batch Renaming" 流程是「Select Symbols → Apply Pattern → Preview changes →
//     Apply to selection」,本文件把这条流程抽象成 plan / dryRun / apply 三段。
//   * 只该动自动生成的名字(FUN_ / DAT_ / LAB_ / SUB_);用户命名的来源是 USER_DEFINED,
//     优先级最高,批量脚本必须跳过。
//   * 冲突用官方 makeUnique 口径拼地址后缀。
//   * 所有写操作在一个事务里,任一条失败整批回滚。
//   * 脚本参数来自 GhidraScript.getScriptArgs();注意 askXxx() 会先从参数里取,
//     所以非交互脚本必须按固定顺序解析。

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

var failed []string

func check(cond bool, label string, detail interface{}) {
	tag := "PASS"
	if !cond {
		tag = "FAIL"
		failed = append(failed, label)
	}
	if detail != nil {
		fmt.Printf("  [%s] %s  <- %v\n", tag, label, detail)
	} else {
		fmt.Printf("  [%s] %s\n", tag, label)
	}
}

const argsUsage = "dry|run <include-regex> <template> [exclude-regex]"

type Entry struct {
	Addr uint64
	Old  string
	New  string
}

type Options struct {
	Mode        string
	Include     *regexp.Regexp
	Template    string
	Exclude     *regexp.Regexp
	OnlyDefault bool
}

// parseArgs 模型对应 getScriptArgs()。
func parseArgs(argv []string) (Options, error) {
	if len(argv) < 3 {
		return Options{}, fmt.Errorf("用法: %s", argsUsage)
	}
	if argv[0] != "dry" && argv[0] != "run" {
		return Options{}, fmt.Errorf("mode 必须是 dry 或 run,收到 %q", argv[0])
	}
	inc, err := regexp.Compile(argv[1])
	if err != nil {
		return Options{}, err
	}
	o := Options{Mode: argv[0], Include: inc, Template: argv[2], OnlyDefault: true}
	if len(argv) > 3 {
		ex, err := regexp.Compile(argv[3])
		if err != nil {
			return Options{}, err
		}
		o.Exclude = ex
	}
	return o, nil
}

// buildPlan:纯函数,不改程序 —— 因此可以反复预演;名没变就不进计划,保证幂等。
func buildPlan(p *Program, o Options) []Entry {
	plan := []Entry{}
	for _, sym := range p.AllSymbols() {
		if !sym.Primary {
			continue
		}
		if o.OnlyDefault && !isDefaultName(sym.Name) {
			continue // 用户命名的符号绝不碰
		}
		if o.Exclude != nil && o.Exclude.MatchString(sym.Name) {
			continue
		}
		if !o.Include.MatchString(sym.Name) {
			continue
		}
		// 注意:Go 的 ReplaceAllString 对不存在的捕获组会静默展开成空串,
		// Python 的 re.sub 会抛 invalid group reference —— 见 README 坑 6。
		out := o.Include.ReplaceAllString(sym.Name, o.Template)
		out = strings.ReplaceAll(out, "{addr}", fmt.Sprintf("%08x", sym.Addr))
		if out == sym.Name {
			continue
		}
		plan = append(plan, Entry{sym.Addr, sym.Name, out})
	}
	sort.Slice(plan, func(i, j int) bool { return plan[i].Addr < plan[j].Addr })
	return plan
}

// resolveConflicts:目标名已被别的地址占用 → 官方 makeUnique 口径拼地址后缀。
func resolveConflicts(p *Program, plan []Entry) []Entry {
	taken := map[string]uint64{}
	for _, sym := range p.AllSymbols() {
		if sym.Primary {
			taken[sym.Name] = sym.Addr
		}
	}
	out := []Entry{}
	for _, e := range plan {
		nu := e.New
		if owner, ok := taken[nu]; ok && owner != e.Addr {
			nu = uniqueName(nu, e.Addr)
		}
		taken[nu] = e.Addr
		out = append(out, Entry{e.Addr, e.Old, nu})
	}
	return out
}

type Report struct {
	Planned    int
	Changed    int
	RolledBack bool
	Err        string
}

func applyPlan(p *Program, plan []Entry, dryRun bool) Report {
	rep := Report{Planned: len(plan)}
	if dryRun {
		return rep
	}
	tx := p.Begin("batch rename")
	for _, e := range plan {
		if err := p.RenameSymbol(e.Addr, e.Old, e.New, "USER_DEFINED"); err != nil {
			tx.Rollback()
			rep.RolledBack = true
			rep.Changed = 0
			rep.Err = err.Error()
			return rep
		}
		rep.Changed++
	}
	tx.Commit()
	return rep
}

// ---------------------------------------------------------------- 自检

func sampleProgram() *Program {
	p := NewProgram()
	p.CreateFunction(0x401000, "FUN_00401000")
	p.CreateFunction(0x401100, "FUN_00401100")
	p.CreateFunction(0x401200, "FUN_00401200")
	p.CreateLabel(0x402000, "LAB_00402000", true, "DEFAULT")
	p.CreateLabel(0x403000, "config_loader", true, "USER_DEFINED")
	p.CreateLabel(0x404000, "FUN_00404000_marker", true, "DEFAULT")
	return p
}

func names(entries []Entry) []string {
	out := []string{}
	for _, e := range entries {
		out = append(out, e.New)
	}
	return out
}

func funcNames(p *Program) []string {
	out := []string{}
	for _, f := range p.FunctionsList() {
		out = append(out, f.Name)
	}
	return out
}

// dumpStr:把整份符号表序列化成一个字符串,便于逐字段比较"是否完全没变"。
func dumpStr(p *Program) string { return fmt.Sprintf("%v", p.Dump()) }

func main() {
	fmt.Println("== 1. 参数解析(getScriptArgs 顺序固定) ==")
	if _, err := parseArgs([]string{"dry", "^FUN_[0-9A-Fa-f]+$", "sub_{addr}"}); err != nil {
		check(false, "合法参数应被接受", err)
	} else {
		check(true, "解析 dry 模式 + 包含正则 + 模板", nil)
	}
	for _, bad := range [][]string{{}, {"go", "x", "y"}, {"dry"}} {
		if _, err := parseArgs(bad); err != nil {
			check(true, fmt.Sprintf("参数 %v 被拒绝", bad), nil)
		} else {
			check(false, fmt.Sprintf("参数 %v 应报错", bad), nil)
		}
	}

	fmt.Println("== 2. 计划:只碰自动生成名 ==")
	p := sampleProgram()
	o, _ := parseArgs([]string{"dry", "^FUN_[0-9A-Fa-f]+$", "sub_{addr}"})
	plan := buildPlan(p, o)
	check(len(plan) == 3 && plan[0].Addr == 0x401000 && plan[2].Addr == 0x401200,
		"3 个 FUN_ 进计划,LAB_ 不匹配包含正则", names(plan))
	check(isDefaultName("FUN_00401000") && isDefaultName("SUB_00404000") &&
		!isDefaultName("sub_00401000"),
		"默认名前缀判断区分大小写:FUN_/SUB_ 算,小写 sub_ 不算", nil)

	fmt.Println("== 3. dry-run 不修改程序 ==")
	before := dumpStr(p)
	rep := applyPlan(p, plan, true)
	check(dumpStr(p) == before, "预演后符号表逐字段不变", nil)
	check(rep.Planned == 3 && rep.Changed == 0, "预演只报告 Planned=3 / Changed=0",
		fmt.Sprintf("(%d, %d)", rep.Planned, rep.Changed))

	fmt.Println("== 4. 提交改名 ==")
	rep = applyPlan(p, plan, false)
	check(rep.Changed == 3 && !rep.RolledBack, "3 条全部改名成功", rep.Changed)
	got := funcNames(p)
	check(len(got) == 3 && got[0] == "sub_00401000" && got[2] == "sub_00401200",
		"函数名已更新({addr} 展开为 8 位十六进制)", got)

	fmt.Println("== 5. 幂等 ==")
	plan2 := buildPlan(p, o)
	check(len(plan2) == 0, "旧正则再也匹配不到任何符号 → 计划为空", len(plan2))
	o3, _ := parseArgs([]string{"dry", "^sub_", "fun_{addr}"})
	check(len(buildPlan(p, o3)) == 0,
		"改完名后 ^sub_ 也拿不到东西(小写 sub_ 不被认作自动生成名)", nil)
	o3.OnlyDefault = false
	check(len(buildPlan(p, o3)) == 3,
		"关掉 OnlyDefault 才会再拿到 3 条 —— 这正是保护用户命名的机制", nil)

	fmt.Println("== 6. 冲突:目标名已被别的地址占用 ==")
	p2 := NewProgram()
	p2.CreateFunction(0x4000, "FUN_00400000")
	p2.CreateLabel(0x5000, "renamed", true, "USER_DEFINED")
	oc, _ := parseArgs([]string{"dry", "^FUN_00400000$", "renamed"})
	fixed := resolveConflicts(p2, buildPlan(p2, oc))
	check(len(fixed) == 1 && fixed[0].New == "renamed_4000",
		"目标名冲突 → 按官方 makeUnique 口径拼地址后缀", names(fixed))
	rep = applyPlan(p2, fixed, false)
	check(rep.Changed == 1 && p2.PrimarySymbol(0x4000).Name == "renamed_4000",
		"冲突版计划可正常提交", nil)

	fmt.Println("== 7. 非法名 → 整批回滚 ==")
	p3 := sampleProgram()
	snap := dumpStr(p3)
	badPlan := []Entry{{0x401000, "FUN_00401000", "good_name"},
		{0x401100, "FUN_00401100", "bad name"}}
	rep = applyPlan(p3, badPlan, false)
	check(rep.RolledBack && rep.Changed == 0, "任一条失败 → 整批回滚,Changed 归零", rep.Err)
	check(dumpStr(p3) == snap, "符号表逐字段回到事务前状态 —— 这就是必须用事务的原因",
		funcNames(p3))

	fmt.Println("== 8. 排除表 ==")
	p4 := sampleProgram()
	o8, _ := parseArgs([]string{"dry", "^FUN_", "impl_{addr}", "00401100"})
	got8 := buildPlan(p4, o8)
	has := func(addr uint64) bool {
		for _, e := range got8 {
			if e.Addr == addr {
				return true
			}
		}
		return false
	}
	check(!has(0x401100), "被 exclude 正则命中的地址不进计划", len(got8))
	check(has(0x401000) && has(0x401200) && has(0x404000),
		"^FUN_ 共命中 4 个,排除 1 个后剩 3 个", len(got8))

	fmt.Printf("\n结果: %d 项失败\n", len(failed))
	for _, x := range failed {
		fmt.Printf("  FAIL: %s\n", x)
	}
	if len(failed) > 0 {
		panic("self-check failed")
	}
}
