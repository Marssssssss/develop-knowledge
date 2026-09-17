// 自检入口(与 python/argocd_check.py + argocd_check_diff.py 对应的关键断言子集)。
// diff 侧的断言在 argocd_check_diff.go, 两个文件同属 package main。
package main

import (
	"fmt"
	"math"
	"sort"
)

var (
	pass, fail int
	failed     []string
)

func check(label string, cond bool, detail string) {
	if cond {
		pass++
		return
	}
	fail++
	failed = append(failed, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

// feq 是浮点等值断言: IEEE754 下 0.9/0.1 这类运算结果必然有尾差, 一律给容差。
func feq(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func eqF(a, b []float64) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if !feq(a[i], b[i]) {
			return false
		}
	}
	return true
}

func eqS(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func main() {
	fmt.Println("Argo CD 语义自检(Go)")

	testAutomatedFlag()
	testDecision()
	testPrune()
	testTiming()
	testDiff()

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", pass, fail)
	if fail > 0 {
		fmt.Println("失败明细:")
		for _, f := range failed {
			fmt.Println("  - " + f)
		}
	}
}

// ============================ 1. automated 三态 ============================

func testAutomatedFlag() {
	fmt.Println("[1] syncPolicy.automated 的三态语义")
	check("automated 块缺失 -> 未开自动同步", !AutomatedEnabled(SyncPolicy{}), "")
	check("automated: {} (enabled 未写) -> 视为开启",
		AutomatedEnabled(SyncPolicy{Automated: &Automated{}}), "")
	check("enabled: null -> 视为开启",
		AutomatedEnabled(SyncPolicy{Automated: &Automated{Enabled: nil}}), "")
	check("enabled: true -> 开启",
		AutomatedEnabled(SyncPolicy{Automated: &Automated{Enabled: BoolPtr(true)}}), "")
	check("enabled: false -> 关闭(只有显式 false 才关)",
		!AutomatedEnabled(SyncPolicy{Automated: &Automated{Enabled: BoolPtr(false)}}), "")
}

// ============================ 2. 同步决策 ============================

func baseState(status string) SyncState {
	return SyncState{
		Policy:   SyncPolicy{Automated: &Automated{}},
		Status:   status,
		Revision: "sha1",
	}
}

func testDecision() {
	fmt.Println("[2] 自动同步的三个短路条件")
	s := baseState(StatusOutOfSync)
	ok, why := SyncDecision(s)
	check("OutOfSync + 全新 (commit, 参数) -> 发起同步", ok, why)

	s = baseState(StatusSynced)
	ok, why = SyncDecision(s)
	check("Synced 状态不触发自动同步", !ok, why)

	s = baseState(StatusUnknown)
	ok, why = SyncDecision(s)
	check("Unknown 状态不触发自动同步", !ok, why)

	key := RevKey{Revision: "sha1", ParamHash: ""}
	s = baseState(StatusOutOfSync)
	s.LastSuccess = &key
	ok, why = SyncDecision(s)
	check("同一组合已成功过且未开 selfHeal -> 不再试", !ok, why)

	s.Policy.Automated.SelfHeal = true
	ok, why = SyncDecision(s)
	check("开 selfHeal 后同一组合仍会重试", ok, why)

	s = baseState(StatusOutOfSync)
	s.LastFailed = &key
	s.Policy.Automated.SelfHeal = true
	ok, why = SyncDecision(s)
	check("同一组合上一轮失败过 -> 即便 selfHeal 也不再重试", !ok, why)

	s = baseState(StatusOutOfSync)
	s.LiveDrift = true
	ok, why = SyncDecision(s)
	check("仅 live 漂移且未开 selfHeal -> 不自愈", !ok, why)

	s.Policy.Automated.SelfHeal = true
	ok, why = SyncDecision(s)
	check("仅 live 漂移但开了 selfHeal -> 自愈", ok, why)

	s = baseState(StatusOutOfSync)
	s.Policy = SyncPolicy{Automated: &Automated{Enabled: BoolPtr(false)}}
	s.LastFailed = &key
	ok, why = SyncDecision(s)
	check("automated 显式 false 时不进入后续判据", !ok, why)
}

// ============================ 3. prune / allowEmpty ============================

func testPrune() {
	fmt.Println("[3] prune 与 allowEmpty")
	git := map[string]bool{"apps/Deployment/a": true, "apps/Service/b": true}
	live := map[string]bool{"apps/Deployment/a": true, "apps/Service/b": true,
		"apps/ConfigMap/old": true, "apps/Secret/legacy": true}

	got, why := PruneDecision(Automated{}, git, live, false)
	check("prune 默认关闭 -> 不删除任何资源", len(got) == 0, why)
	check("原因说明是安全机制而非无孤立资源", why == "prune 未开启(默认安全机制): 只标记不删除", why)

	got, why = PruneDecision(Automated{Prune: true}, git, live, false)
	check("prune 开启 -> 删除孤立资源",
		eqS(got, []string{"apps/ConfigMap/old", "apps/Secret/legacy"}), why)

	got, why = PruneDecision(Automated{Prune: true}, map[string]bool{}, live, true)
	check("目标资源集为空且未开 allowEmpty -> 拒绝清空应用", len(got) == 0, why)

	got, why = PruneDecision(Automated{Prune: true, AllowEmpty: true}, map[string]bool{}, live, true)
	check("开了 allowEmpty 才允许清空", len(got) == 4, why)

	got, why = PruneDecision(Automated{Prune: true}, git, git, false)
	check("无孤立资源时直接短路", len(got) == 0 && why == "无孤立资源", why)

	ordered := []string{}
	for r := range live {
		ordered = append(ordered, r)
	}
	sort.Strings(ordered)
	got, _ = PruneDecision(Automated{Prune: true}, map[string]bool{}, live, false)
	check("返回切片按字典序(与 Python 版 set 等价)", eqS(got, ordered), fmt.Sprint(got))
}

// ============================ 4. 调和周期与退避 ============================

func testTiming() {
	fmt.Println("[4] 调和间隔与失败退避")
	check("默认调和周期 = 120 + 60 = 180 秒(恰为官方最大 3 分钟)",
		feq(ReconciliationPeriod(DefaultReconciliationS, DefaultJitterS), 180.0) &&
			feq(ReconciliationPeriod(DefaultReconciliationS, DefaultJitterS), MaxSyncPeriodS), "")
	check("custom timeout.reconciliation 也被封顶到 180",
		feq(ReconciliationPeriod(300, 60), MaxSyncPeriodS), "")
	check("小值与 jitter 相加不封顶", feq(ReconciliationPeriod(30, 10), 40.0), "")
	check("默认 reconciliation 常量 = 120", feq(DefaultReconciliationS, 120), "")
	check("self-heal timeout 默认 5 秒",
		feq(SelfHealRecheckDelay(DefaultSelfHealTimeoutS), 5.0), "")

	check("开启自动同步的应用不能 rollback",
		!RollbackAllowed(SyncPolicy{Automated: &Automated{}}), "")
	check("未开自动同步时可以 rollback", RollbackAllowed(SyncPolicy{}), "")

	bo := Backoff{Duration: 5, Factor: 2, MaxDuration: 30}
	check("退避序列 5,10,20,30,30(被 maxDuration 封顶)",
		eqF(RetryDelays(5, bo, 5), []float64{5, 10, 20, 30, 30}),
		fmt.Sprint(RetryDelays(5, bo, 5)))
	check("limit 截断请求次数", len(RetryDelays(5, bo, 9)) == 5, "")
	check("limit = -1 表示无限次(此处请求 7 次)",
		len(RetryDelays(-1, Backoff{Duration: 1, Factor: 1}, 7)) == 7, "")
	check("未配置 retry(limit = 0) -> 不退避",
		len(RetryDelays(0, Backoff{}, 3)) == 0, "")
}
