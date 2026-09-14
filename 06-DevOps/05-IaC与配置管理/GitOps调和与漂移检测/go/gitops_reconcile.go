// GitOps reconciliation loop and drift detection (Argo CD semantics).
//
// Refs read: Argo CD "Automated Sync Policy" https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/
//            Argo CD "Sync Options" https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/
//            Kubernetes "Server-Side Apply" https://kubernetes.io/docs/reference/using-api/server-side-apply/
//
// Mirrored facts: automated sync happens ONLY if the app is OutOfSync and only
// once per unique commit-SHA + application-parameters pair ("a second sync will
// not be attempted, unless selfHeal flag is set to true"); a *failed* attempt
// against the same pair is never re-attempted (failures use the retry block
// with exponential backoff instead); a change made directly in the live cluster
// does not trigger automated sync unless selfHeal is on, and selfHeal re-attempts
// after the self-heal timeout (5s default); pruning is off by default and a
// resource Argo CD expects to prune keeps the app OutOfSync.
package main

import (
	"fmt"
	"sort"
)

// Controller is the pull agent: watch Git, diff against live, act, record.
type Controller struct {
	Repo           *GitRepo
	Cluster        *Cluster
	Policy         SyncPolicy
	SyncedRevision string
	FailedPairs    map[string]bool
	Retries        int
	Log            []string
}

const controllerManager = "argocd-controller"

func NewController(repo *GitRepo, c *Cluster, p SyncPolicy) *Controller {
	return &Controller{Repo: repo, Cluster: c, Policy: p, FailedPairs: map[string]bool{}}
}

// Wants is the manifest set plus the tracking annotation Argo CD adds.
func (ct *Controller) Wants() map[string]map[string]interface{} {
	out := map[string]map[string]interface{}{}
	for key, fields := range ct.Repo.Manifests() {
		merged := map[string]interface{}{tracking: fmt.Sprintf("%s:%s", key, ct.Repo.SHA())}
		for k, v := range fields {
			merged[k] = v
		}
		out[key] = merged
	}
	return out
}

// Drift returns (objects needing a write, tracked live objects needing a prune).
func (ct *Controller) Drift() (stale, extra []string) {
	wanted := ct.Wants()
	for key, fields := range wanted {
		live := ct.Cluster.FieldsOf(key)
		for path, value := range fields {
			if live[path] != value {
				stale = append(stale, key)
				break
			}
		}
	}
	for key, obj := range ct.Cluster.Objects {
		if _, ok := wanted[key]; !ok {
			if _, tracked := obj.Fields[tracking]; tracked {
				extra = append(extra, key)
			}
		}
	}
	sort.Strings(stale)
	sort.Strings(extra)
	return stale, extra
}

func (ct *Controller) Status() string {
	stale, extra := ct.Drift()
	if len(stale) == 0 && len(extra) == 0 {
		return "Synced"
	}
	return "OutOfSync"
}

// Sync applies the desired state. The return value is the *operation* result:
// with pruning off, orphans survive and the app legitimately stays OutOfSync.
func (ct *Controller) Sync() string {
	wanted := ct.Wants()
	if len(wanted) == 0 && !ct.Policy.AllowEmpty {
		ct.Log = append(ct.Log, "    sync refused: empty target and allowEmpty is off")
		return "Unknown"
	}
	for key, fields := range wanted {
		kind, name := splitOnce(key)
		literal := map[string]interface{}{}
		for k, v := range fields {
			literal[k] = v
		}
		if _, err := ct.Cluster.Apply(kind, name, literal, controllerManager, false); err != nil {
			ct.Log = append(ct.Log, "    "+err.Error())
			return "SyncFailed"
		}
	}
	if _, extra := ct.Drift(); ct.Policy.Prune {
		for _, key := range extra {
			ct.Cluster.Delete(key)
			ct.Log = append(ct.Log, "    -   "+key+" pruned")
		}
	}
	ct.SyncedRevision = ct.Repo.SHA()
	ct.Retries = 0
	ct.Log = append(ct.Log, fmt.Sprintf("    sync @ %s, prune=%v",
		ct.Repo.SHA(), ct.Policy.Prune))
	return "Synced"
}

// Reconcile is one loop iteration; it returns the app status.
func (ct *Controller) Reconcile(params string) string {
	pair := ct.Repo.SHA() + "|" + params
	stale, extra := ct.Drift()
	pending := len(stale) > 0 || len(extra) > 0
	gitMoved := ct.Repo.SHA() != ct.SyncedRevision
	// Drift not explained by a new revision must be a live-cluster edit; Argo CD
	// leaves those alone unless selfHeal is on. Orphan-only diffs are not live
	// drift: Git simply dropped the object.
	liveDrift := len(stale) > 0 && !gitMoved

	switch {
	case !pending:
		ct.Log = append(ct.Log, "  Synced: nothing to do")
	case !ct.Policy.Automated:
		ct.Log = append(ct.Log, "  OutOfSync, but automated sync is disabled")
	case liveDrift && !ct.Policy.SelfHeal:
		ct.Log = append(ct.Log, "  live drift detected but selfHeal is off -- "+
			"a live change never triggers automated sync")
	case pair == ct.SyncedRevision+"|"+params && !ct.Policy.SelfHeal:
		ct.Log = append(ct.Log, "  OutOfSync, but this revision+params already "+
			"synced -- only one sync per pair")
	case ct.FailedPairs[pair]:
		ct.Log = append(ct.Log, "  a previous attempt for this revision+params "+
			"failed -- not retrying")
	case len(stale) == 0 && len(extra) > 0 && !ct.Policy.Prune:
		ct.Log = append(ct.Log, fmt.Sprintf("  %d orphaned object(s) but pruning is "+
			"off -- the app stays OutOfSync", len(extra)))
	default:
		if liveDrift {
			ct.Log = append(ct.Log, fmt.Sprintf("  selfHeal: re-syncing %ds after "+
				"the drift was observed", ct.Policy.SelfHealTimeout))
		}
		if ct.Sync() != "Synced" {
			ct.FailedPairs[pair] = true
			ct.Retries++
			if ct.Retries == 1 {
				ct.Log = append(ct.Log, "  will retry with backoff (factor 2)")
			}
		}
	}
	return ct.Status()
}

func splitOnce(key string) (string, string) {
	for i := 0; i < len(key); i++ {
		if key[i] == '/' {
			return key[:i], key[i+1:]
		}
	}
	return key, ""
}

// --------------------------------------------------------------------- demo

func manifests(replicas int, withCache bool) map[string]map[string]interface{} {
	out := map[string]map[string]interface{}{
		"Deployment/web": {
			"spec.replicas":       replicas,
			"spec.template.image": "web:1.0",
		},
	}
	if withCache {
		out["Service/cache"] = map[string]interface{}{"spec.port": 6379}
	}
	return out
}

func step(ct *Controller, title string) {
	fmt.Println("-- " + title)
	status := ct.Reconcile("")
	for _, entry := range ct.Log {
		fmt.Println(entry)
	}
	ct.Log = nil
	fmt.Printf("   status=%s\n\n", status)
}

func main() {
	policy := SyncPolicy{Automated: true, Prune: false, SelfHeal: true,
		Reconciliation: 120, Jitter: 60, SelfHealTimeout: 5}
	repo, cluster := &GitRepo{}, NewCluster()
	ct := NewController(repo, cluster, policy)

	fmt.Println("== [0] OpenGitOps v1.0.0: the four principles this loop implements ==")
	for i, text := range []string{
		"Declarative: desired state is expressed declaratively",
		"Versioned and Immutable: full history, every change a commit",
		"Pulled Automatically: an agent inside pulls, nothing is pushed in",
		"Continuously Reconciled: keep observing actual vs desired, converge",
	} {
		fmt.Printf("   %d. %s\n", i+1, text)
	}
	fmt.Println()

	fmt.Println("== [1] first reconcile: cluster empty, Git has two manifests ==")
	repo.Commit("r1", manifests(3, true))
	step(ct, "revision r1 -> auto-sync creates both objects")
	fmt.Println("   live:", cluster.Keys(), "\n")

	step(ct, "[2] second cycle: Synced, so nothing is attempted")

	fmt.Println("== [3] drift: a human kubectl-edits replicas to 9 (selfHeal on) ==")
	web := cluster.GetOrCreate("Deployment", "web")
	web.Fields["spec.replicas"] = 9
	web.Fields["edited-by"] = "human" // unmanaged field
	step(ct, "the agent detects drift and self-heals")
	fmt.Println("   spec.replicas is back to",
		cluster.FieldsOf("Deployment/web")["spec.replicas"])
	fmt.Println("   `edited-by` survives: the controller never owned it\n")

	fmt.Println("== [4] drift again, but selfHeal is off ==")
	ct.Policy.SelfHeal = false
	cluster.GetOrCreate("Deployment", "web").Fields["spec.replicas"] = 9
	step(ct, "live change is reported, never reverted")
	fmt.Println("   spec.replicas still",
		cluster.FieldsOf("Deployment/web")["spec.replicas"], "\n")

	fmt.Println("== [5] new revision: auto-sync applies it (prune still off) ==")
	ct.Policy.SelfHeal = true
	repo.Commit("r2", manifests(5, false))
	step(ct, "r2 applied; Service/cache is now orphaned")
	fmt.Println("   live:", cluster.Keys())
	fmt.Println("   the orphan is NOT pruned, so the app stays", ct.Status(), "\n")

	fmt.Println("== [6] turn pruning on -> the orphan goes away and the app converges ==")
	ct.Policy.Prune = true
	step(ct, "prune=true removes objects Git no longer declares")
	fmt.Println("   live:", cluster.Keys(), "status:", ct.Status(), "\n")

	fmt.Println("== [7] empty target with allowEmpty off: the sync is refused ==")
	repo.Commit("r3", map[string]map[string]interface{}{})
	step(ct, "guard against wiping the app because Git went empty")
	fmt.Println("   a refusal is a real failure, so this revision+params pair is")
	fmt.Println("   remembered and never retried\n")

	fmt.Println("== [8] SSA field ownership between two managers ==")
	solo := NewCluster()
	solo.Apply("Deployment", "web", map[string]interface{}{"spec.replicas": 3}, "argocd-controller", false)
	solo.Apply("Deployment", "web", map[string]interface{}{"spec.replicas": 3}, "hpa-controller", false)
	fmt.Println("   same value -> shared ownership:",
		solo.Objects["Deployment/web"].Managed)
	if _, err := solo.Apply("Deployment", "web",
		map[string]interface{}{"spec.replicas": 4}, "other-team", false); err != nil {
		fmt.Println("  ", err)
	}
	solo.Apply("Deployment", "web", map[string]interface{}{"spec.replicas": 4}, "other-team", true)
	owners := []string{}
	for manager, owned := range solo.Objects["Deployment/web"].Managed {
		if owned["spec.replicas"] {
			owners = append(owners, manager)
		}
	}
	sort.Strings(owners)
	fmt.Println("   after force, owners of spec.replicas:", owners, "spec.replicas =",
		solo.FieldsOf("Deployment/web")["spec.replicas"], "\n")

	fmt.Println("== [9] documented reconcile interval: 120s base + up to 60s jitter ==")
	fmt.Printf("   base %ds + jitter %ds  ->  max period %ds (documented: 3 min)\n",
		policy.Reconciliation, policy.Jitter, policy.Reconciliation+policy.Jitter)
}
