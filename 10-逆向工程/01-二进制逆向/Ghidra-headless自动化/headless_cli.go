// headless_cli.go — analyzeHeadless 命令行构造与选项组合校验,同题 Go 实现
//
// 依据: Ghidra 官方 Headless Analyzer README(analyzeHeadlessREADME.html):
//   * project_location + project_name 或 ghidra:// 仓库 URL 必居其一;
//   * -import 与 -process 不能同时出现;脚本名必须带扩展名且不含路径;
//   * -readOnly 时 -overwrite 被忽略;-process 删程序必须给 -okToDelete;
//   * -max-cpu 传 0 或负数等价于 1;
//   * HeadlessContinuationOption 四种取值的 4×4 合并表(官方给全)。
//
// 本机无 Go 工具链,靠人工审查 + 括号配平/未使用 import 的程序化校验。
//
// 构建: go build ./...    运行: go run .
package main

import (
	"fmt"
	"os"
	"strings"
)

// Options 对应命令行里我们建模的那部分开关。
type Options struct {
	ProjectLocation string
	ProjectName     string
	ServerURL       string
	Folder          string
	Imports         []string
	Process         string
	HasProcess      bool
	Pre             [][2]string // [脚本名, 空格分隔的参数串]
	Post            [][2]string
	ScriptPath      []string
	NoAnalysis      bool
	ReadOnly        bool
	DeleteProject   bool
	Overwrite       bool
	OkToDelete      bool
	MaxCPU          int
	HasMaxCPU       bool
	Recursive       int
	HasRecursive    bool
}

func hasPathSep(s string) bool {
	return strings.ContainsAny(s, "/\\")
}

// BuildCommand 先校验再拼串:这类工具的坑几乎都在**选项组合**上。
func BuildCommand(o Options) (string, error) {
	if o.ProjectLocation == "" && o.ServerURL == "" {
		return "", fmt.Errorf("必须给出 project_location + project_name,或 ghidra:// 仓库 URL")
	}
	if o.ProjectLocation != "" && o.ProjectName == "" {
		return "", fmt.Errorf("给出 project_location 时必须同时给出 project_name")
	}
	if len(o.Imports) > 0 && o.HasProcess {
		return "", fmt.Errorf("-import 与 -process 不能同时出现")
	}
	if o.HasProcess && o.DeleteProject {
		return "", fmt.Errorf("-process 模式下不能删除项目(只对本次 -import 新建项目生效)")
	}
	if o.ReadOnly && o.Overwrite {
		return "", fmt.Errorf("-readOnly 时 -overwrite 被忽略,不要同时给")
	}
	for _, g := range [][][2]string{o.Pre, o.Post} {
		for _, item := range g {
			if hasPathSep(item[0]) {
				return "", fmt.Errorf("脚本名不能含路径: %s", item[0])
			}
			if !strings.Contains(item[0], ".") {
				return "", fmt.Errorf("脚本名必须带扩展名(如 MyScript.java): %s", item[0])
			}
		}
	}
	maxCPU := o.MaxCPU
	if o.HasMaxCPU && maxCPU <= 0 {
		maxCPU = 1 // 官方:0 或负数等价于 1
	}

	var cmd []string
	cmd = append(cmd, "analyzeHeadless")
	if o.ServerURL != "" {
		target := o.ServerURL
		if o.Folder != "" {
			target = target + "/" + o.Folder
		}
		cmd = append(cmd, target)
	} else {
		target := o.ProjectName
		if o.Folder != "" {
			target = target + "/" + o.Folder
		}
		cmd = append(cmd, o.ProjectLocation, target)
	}
	if o.Imports != nil {
		cmd = append(cmd, "-import")
		cmd = append(cmd, o.Imports...)
	}
	if o.HasProcess {
		cmd = append(cmd, "-process")
		if o.Process != "" {
			cmd = append(cmd, o.Process)
		}
	}
	for _, item := range o.Pre {
		cmd = append(cmd, "-preScript", item[0])
		if item[1] != "" {
			cmd = append(cmd, strings.Fields(item[1])...)
		}
	}
	for _, item := range o.Post {
		cmd = append(cmd, "-postScript", item[0])
		if item[1] != "" {
			cmd = append(cmd, strings.Fields(item[1])...)
		}
	}
	if len(o.ScriptPath) > 0 {
		cmd = append(cmd, "-scriptPath", strings.Join(o.ScriptPath, ";"))
	}
	if o.HasRecursive {
		cmd = append(cmd, "-recursive")
		if o.Recursive > 0 {
			cmd = append(cmd, fmt.Sprint(o.Recursive))
		}
	}
	if o.Overwrite {
		cmd = append(cmd, "-overwrite")
	}
	if o.ReadOnly {
		cmd = append(cmd, "-readOnly")
	}
	if o.DeleteProject {
		cmd = append(cmd, "-deleteProject")
	}
	if o.NoAnalysis {
		cmd = append(cmd, "-noanalysis")
	}
	if o.OkToDelete {
		cmd = append(cmd, "-okToDelete")
	}
	if o.HasMaxCPU {
		cmd = append(cmd, "-max-cpu", fmt.Sprint(maxCPU))
	}
	return strings.Join(cmd, " "), nil
}

// 官方 4×4 合并表:行 = Script1 先设,列 = Script2 后设。
var continuations = []string{"ABORT", "ABORT_AND_DELETE", "CONTINUE_THEN_DELETE", "CONTINUE"}

var officialTable = map[string]map[string]string{
	"ABORT": {"ABORT": "ABORT", "ABORT_AND_DELETE": "ABORT",
		"CONTINUE_THEN_DELETE": "ABORT", "CONTINUE": "ABORT"},
	"ABORT_AND_DELETE": {"ABORT": "ABORT_AND_DELETE", "ABORT_AND_DELETE": "ABORT_AND_DELETE",
		"CONTINUE_THEN_DELETE": "ABORT_AND_DELETE", "CONTINUE": "ABORT_AND_DELETE"},
	"CONTINUE_THEN_DELETE": {"ABORT": "ABORT_AND_DELETE", "ABORT_AND_DELETE": "ABORT_AND_DELETE",
		"CONTINUE_THEN_DELETE": "CONTINUE_THEN_DELETE", "CONTINUE": "CONTINUE_THEN_DELETE"},
	"CONTINUE": {"ABORT": "ABORT", "ABORT_AND_DELETE": "ABORT_AND_DELETE",
		"CONTINUE_THEN_DELETE": "CONTINUE_THEN_DELETE", "CONTINUE": "CONTINUE"},
}

// Merge 按规则合并,而不是直接查表 —— 规则能被反查才算真的理解这张表。
func Merge(a, b string) string {
	if a == "ABORT" || a == "ABORT_AND_DELETE" {
		return a // Script1 一旦要求中止,Script2 根本不运行
	}
	if b == "CONTINUE" {
		return a
	}
	if b == "ABORT_AND_DELETE" {
		return "ABORT_AND_DELETE"
	}
	if b == "CONTINUE_THEN_DELETE" {
		return "CONTINUE_THEN_DELETE"
	}
	if a == "CONTINUE_THEN_DELETE" {
		return "ABORT_AND_DELETE" // 删除意图 + 立即中止 → 中止并删除
	}
	return "ABORT"
}

// Script2Runs 官方脚注:Script1 为 ABORT / ABORT_AND_DELETE 时 Script2 不运行。
func Script2Runs(first string) bool {
	return first != "ABORT" && first != "ABORT_AND_DELETE"
}

// Outcome 返回 (是否继续后续处理, 程序最终是否保留)。
func Outcome(mode, option string, okToDelete, readOnly bool) (bool, bool) {
	cont := option == "CONTINUE" || option == "CONTINUE_THEN_DELETE"
	if mode == "import" {
		keep := option == "ABORT" || option == "CONTINUE"
		if readOnly && keep {
			keep = false
		}
		return cont, keep
	}
	keep := option == "ABORT" || option == "CONTINUE" // -process:ABORT 会保存改动
	if !keep {
		if readOnly || !okToDelete {
			keep = true // 官方:未给 -okToDelete 只警告;-readOnly 下不能删
		}
	}
	return cont, keep
}

func main() {
	fails := 0
	report := func(ok bool, label, detail string) {
		state := "PASS"
		if !ok {
			state, fails = "FAIL", fails+1
		}
		if detail != "" {
			fmt.Printf("  [%s] %s  <- %s\n", state, label, detail)
		} else {
			fmt.Printf("  [%s] %s\n", state, label)
		}
	}

	var bad []string
	for _, a := range continuations {
		for _, b := range continuations {
			if Merge(a, b) != officialTable[a][b] {
				bad = append(bad, a+" x "+b)
			}
		}
	}
	report(len(bad) == 0, "16 格续延合并表逐格一致", strings.Join(bad, ","))
	report(Merge("CONTINUE_THEN_DELETE", "ABORT") == "ABORT_AND_DELETE",
		"CTD + ABORT → ABORT_AND_DELETE", "")
	report(Merge("ABORT", "CONTINUE_THEN_DELETE") == "ABORT",
		"ABORT + CTD → ABORT(合并表非交换)", "")

	cont, keep := Outcome("process", "ABORT_AND_DELETE", false, false)
	report(!cont && keep, "process 下想删但没给 -okToDelete → 只警告不删", "")
	cont, keep = Outcome("process", "ABORT_AND_DELETE", true, false)
	report(!cont && !keep, "给了 -okToDelete 才真的删", "")
	cont, keep = Outcome("import", "CONTINUE", true, true)
	report(cont && !keep, "-readOnly 导入 → 不保存", "")

	cmd, err := BuildCommand(Options{ProjectLocation: "/Users/user/ghidra/projects",
		ProjectName: "MyProject", Imports: []string{"hello.exe"},
		Pre: [][2]string{{"Script.java", "arg1 arg2"}}, NoAnalysis: true})
	want := "analyzeHeadless /Users/user/ghidra/projects MyProject -import hello.exe" +
		" -preScript Script.java arg1 arg2 -noanalysis"
	report(err == nil && cmd == want, "复现官方示例命令行", cmd)

	if _, err := BuildCommand(Options{ProjectLocation: "/p", ProjectName: "P",
		Imports: []string{"a.exe"}, HasProcess: true, Process: "p"}); err == nil {
		report(false, "-import 与 -process 互斥被拦截", "未拦截")
	} else {
		report(true, "-import 与 -process 互斥被拦截", err.Error())
	}
	if _, err := BuildCommand(Options{ProjectLocation: "/p", ProjectName: "P",
		Pre: [][2]string{{"/tmp/S.java", ""}}}); err == nil {
		report(false, "脚本名含路径被拦截", "未拦截")
	} else {
		report(true, "脚本名含路径被拦截", err.Error())
	}
	cmd2, _ := BuildCommand(Options{ProjectLocation: "/p", ProjectName: "P",
		MaxCPU: -2, HasMaxCPU: true})
	report(strings.HasSuffix(cmd2, "-max-cpu 1"), "max-cpu 负数归一为 1", cmd2)

	if fails > 0 {
		fmt.Printf("\nSOME CHECKS FAILED (fails=%d)\n", fails)
		os.Exit(1)
	}
	fmt.Println("\nALL CHECKS PASSED")
}
