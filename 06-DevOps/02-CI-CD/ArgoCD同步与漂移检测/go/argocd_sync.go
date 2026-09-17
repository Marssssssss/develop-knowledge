// Argo CD 自动同步 / 自愈 / prune / 调和时序 语义模型(Go 对照实现)。
//
// 与 python/argocd_sync.py 同源同义, 权威依据见 README「参考资料」:
// argo-cd.readthedocs.io 的 user-guide/auto_sync/。
//
// Go 版用 *Automated 指针表达"automated 块缺失"与"automated: {}"的区别, 正好对上
// 官方 enabled 的三态语义(缺失 / null 都视为开启);Python 版用 dict 的 None 表达。
package main

import (
	"fmt"
	"math"
	"sort"
)

const (
	// DefaultSelfHealTimeoutS 对应 argocd-application-controller 的
	// --self-heal-timeout-seconds 默认值(秒)。
	DefaultSelfHealTimeoutS = 5.0
	// DefaultReconciliationS 对应 argocd-cm 的 timeout.reconciliation 默认值。
	DefaultReconciliationS = 120.0
	// DefaultJitterS 是官方给出的抖动上限(秒)。
	DefaultJitterS = 60.0
	// MaxSyncPeriodS 对应官方原文 "maximum period of 3 minutes"。
	MaxSyncPeriodS = 180.0
)

const (
	StatusSynced    = "Synced"
	StatusOutOfSync = "OutOfSync"
	StatusUnknown   = "Unknown"
)

// Automated 对应 syncPolicy.automated;Enabled 为 nil 表示写了 null / 留空。
type Automated struct {
	Enabled    *bool
	Prune      bool
	SelfHeal   bool
	AllowEmpty bool
}

// SyncPolicy 中 Automated 为 nil 表示整个 automated 块缺失。
type SyncPolicy struct {
	Automated *Automated
}

// RevKey 是 (commit SHA1, 应用参数哈希) 组合, 官方文档中的唯一尝试键。
type RevKey struct {
	Revision  string
	ParamHash string
}

// SyncState 是一次调和所依据的全部输入。
type SyncState struct {
	Policy      SyncPolicy
	Status      string
	Revision    string
	ParamHash   string
	LastSuccess *RevKey
	LastFailed  *RevKey
	LiveDrift   bool
}

// AutomatedEnabled 复刻三态: 块缺失 -> false;块在但 enabled 为 null -> true。
func AutomatedEnabled(sp SyncPolicy) bool {
	if sp.Automated == nil {
		return false
	}
	if sp.Automated.Enabled == nil {
		return true
	}
	return *sp.Automated.Enabled
}

// BoolPtr 便于在断言里写 "enabled: true/false"。
func BoolPtr(v bool) *bool { return &v }

// SyncDecision 返回 (是否发起同步, 原因)。三条短路全部来自官方 auto_sync 原文。
func SyncDecision(s SyncState) (bool, string) {
	if !AutomatedEnabled(s.Policy) {
		return false, "automated 未开启(或显式 false)"
	}
	if s.Status == StatusSynced || s.Status == StatusUnknown {
		return false, "只有 OutOfSync 才尝试自动同步"
	}
	key := RevKey{Revision: s.Revision, ParamHash: s.ParamHash}
	selfHeal := s.Policy.Automated != nil && s.Policy.Automated.SelfHeal

	if s.LastFailed != nil && *s.LastFailed == key {
		return false, "上一轮对同一 (commit, 参数) 已失败 -> 不再重试"
	}
	if s.LastSuccess != nil && *s.LastSuccess == key && !selfHeal {
		return false, "同一 (commit, 参数) 已成功同步过, 且未开 selfHeal"
	}
	if s.LiveDrift && !selfHeal {
		return false, "仅 live 漂移(Git 未变更)且未开 selfHeal"
	}
	return true, "OutOfSync 且不命中三条短路"
}

// SelfHealRecheckDelay 是 selfHeal 打开后再次尝试前的等待秒数。
func SelfHealRecheckDelay(selfHealTimeoutS float64) float64 { return selfHealTimeoutS }

// PruneDecision 返回 (要删除的资源, 原因)。返回的是**排序后的切片**, 与 Python 版的
// set 等价(集合语义, 只是表示法不同)。
func PruneDecision(auto Automated, gitRes, liveRes map[string]bool, gitIsEmpty bool) ([]string, string) {
	orphans := []string{}
	for r := range liveRes {
		if !gitRes[r] {
			orphans = append(orphans, r)
		}
	}
	sort.Strings(orphans)
	if len(orphans) == 0 {
		return nil, "无孤立资源"
	}
	if !auto.Prune {
		return nil, "prune 未开启(默认安全机制): 只标记不删除"
	}
	if gitIsEmpty && !auto.AllowEmpty {
		return nil, "目标资源集为空且未开 allowEmpty -> 拒绝清空应用"
	}
	return orphans, fmt.Sprintf("prune 已开启, 删除 %d 个孤立资源", len(orphans))
}

// ReconciliationPeriod 封顶在 3 分钟(官方最大周期)。
func ReconciliationPeriod(configuredS, jitterS float64) float64 {
	return math.Min(configuredS+jitterS, MaxSyncPeriodS)
}

// RollbackAllowed: 开启自动同步的应用不能做 rollback(官方原文)。
func RollbackAllowed(sp SyncPolicy) bool { return !AutomatedEnabled(sp) }

// RetryDelays 按 limit / duration / factor / maxDuration 展开退避序列。
// MaxDuration <= 0 视为"未设置", 用 +Inf 封顶。
func RetryDelays(limit int, backoff Backoff, attempts int) []float64 {
	n := attempts
	if limit != -1 && limit < attempts {
		n = limit
	}
	if n < 0 {
		n = 0
	}
	factor := backoff.Factor
	if factor == 0 {
		factor = 1
	}
	capS := backoff.MaxDuration
	if capS <= 0 {
		capS = math.Inf(1)
	}
	out := []float64{}
	cur := backoff.Duration
	for i := 0; i < n; i++ {
		out = append(out, math.Min(cur, capS))
		cur *= factor
	}
	return out
}

// Backoff 对应 syncPolicy.retry.backoff。
type Backoff struct {
	Duration    float64
	Factor      float64
	MaxDuration float64
}
