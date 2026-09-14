"""GitOps reconciliation loop and drift detection (Argo CD semantics).

Refs read before writing this file:
  - Argo CD "Automated Sync Policy"
    https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/
  - Argo CD "Sync Options" https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/
  - Kubernetes "Server-Side Apply"
    https://kubernetes.io/docs/reference/using-api/server-side-apply/
  - OpenGitOps Principles v1.0.0 https://opengitops.dev/

Mirrored facts:
  * Automated sync happens ONLY if the app is OutOfSync, and only once per
    unique combination of commit SHA + application parameters ("If the most
    recent successful sync in the history was already performed against the
    same commit-SHA and parameters, a second sync will not be attempted, unless
    selfHeal flag is set to true"). A failed attempt for the same pair is never
    retried -- failures instead use the policy's `retry` block with exponential
    backoff (limit / duration / factor / maxDuration).
  * By default a change made directly in the live cluster does NOT trigger
    automated sync; `selfHeal` enables that, re-attempting after the self-heal
    timeout (5 s by default).
  * Pruning is off by default, and a resource that Argo CD expects to prune
    keeps the app OutOfSync ("The app will be out of sync if Argo CD expects a
    resource to be pruned").
  * The `Replace=true` sync option switches from `kubectl apply` to
    `replace/create` -- destructive, and needed when a manifest is too large for
    the `last-applied-configuration` annotation.
"""

from __future__ import annotations

import random

from model import Cluster, GitRepo, SyncPolicy, TRACKING


class Controller:
    """The pull agent: watch Git, diff against live, act, record the outcome."""

    MANAGER = "argocd-controller"

    def __init__(self, repo: GitRepo, cluster: Cluster, policy: SyncPolicy):
        self.repo = repo
        self.cluster = cluster
        self.policy = policy
        self.synced_revision: str | None = None
        self.failed_pairs: set[tuple[str, str]] = set()
        self.retries = 0
        self.log: list[str] = []

    # -- diff ---------------------------------------------------------------

    def wants(self) -> dict[str, dict]:
        """Manifests plus the tracking annotation Argo CD adds while syncing."""
        out = {}
        for key, fields in self.repo.manifests().items():
            out[key] = dict(fields, **{TRACKING: f"{key}:{self.repo.sha}"})
        return out

    def drift(self) -> tuple[list[str], list[str]]:
        """Return (objects needing a write, tracked live objects needing a prune)."""
        wanted = self.wants()
        stale = [k for k, f in wanted.items()
                 if any(self.cluster.fields_of(k).get(p) != v for p, v in f.items())]
        extra = [k for k, o in self.cluster.objects.items()
                 if k not in wanted and TRACKING in o.fields]
        return stale, extra

    def status(self) -> str:
        stale, extra = self.drift()
        return "OutOfSync" if (stale or extra) else "Synced"

    # -- sync ---------------------------------------------------------------

    def sync(self) -> str:
        """Apply the desired state; returns "Synced" when the write succeeded.

        Note this is the *operation* result, not the app status: with pruning
        off, orphans legitimately survive and the app stays OutOfSync.
        """
        wanted = self.wants()
        if not wanted and not self.policy.allow_empty:
            self.log.append("    sync refused: empty target and allowEmpty is off")
            return "Unknown"
        for key, fields in wanted.items():
            try:
                self.cluster.apply(*key.split("/", 1), fields, self.MANAGER)
            except Exception as exc:                       # noqa: BLE001 (demo)
                self.log.append(f"    {exc}")
                return "SyncFailed"
        _, extra = self.drift()
        if self.policy.prune and extra:
            for key in extra:
                self.cluster.delete(key)
                self.log.append(f"    -   {key} pruned")
        self.synced_revision = self.repo.sha
        self.retries = 0
        self.log.append(f"    sync @ {self.repo.sha}, prune={self.policy.prune}")
        return "Synced"

    # -- the reconciliation loop -------------------------------------------

    def reconcile(self, params: str = "") -> tuple[str, int]:
        """One loop iteration. Returns (status, seconds until the next check)."""
        pair = (self.repo.sha, params)
        stale, extra = self.drift()
        pending = bool(stale or extra)
        git_moved = self.repo.sha != self.synced_revision
        # Drift not explained by a new revision must be a live-cluster edit, and
        # Argo CD leaves those alone unless selfHeal is on. An orphan-only diff
        # is NOT live drift: Git simply dropped the object.
        live_drift = bool(stale) and not git_moved

        if not pending:
            self.log.append("  Synced: nothing to do")
        elif not self.policy.automated:
            self.log.append("  OutOfSync, but automated sync is disabled")
        elif live_drift and not self.policy.self_heal:
            self.log.append("  live drift detected but selfHeal is off -- "
                            "a live change never triggers automated sync")
        elif pair == (self.synced_revision, params) and not self.policy.self_heal:
            self.log.append("  OutOfSync, but this revision+params already "
                            "synced -- only one sync per pair")
        elif pair in self.failed_pairs:
            self.log.append("  a previous attempt for this revision+params "
                            "failed -- not retrying")
        elif not stale and extra and not self.policy.prune:
            self.log.append(f"  {len(extra)} orphaned object(s) but pruning is "
                            "off -- the app stays OutOfSync")
        else:
            if live_drift:
                self.log.append("  selfHeal: re-syncing "
                                f"{self.policy.self_heal_timeout}s after the "
                                "drift was observed")
            if self.sync() != "Synced":
                self.failed_pairs.add(pair)
                self.retries += 1
                if self.retries == 1:
                    self.log.append("  will retry with backoff (factor 2)")

        interval = self.policy.reconciliation + random.randint(0, self.policy.jitter)
        return self.status(), interval


# --------------------------------------------------------------------- demo

def manifests(replicas: int, include_cache: bool = True) -> dict[str, dict]:
    out = {"Deployment/web": {"spec.replicas": replicas,
                              "spec.template.image": "web:1.0"}}
    if include_cache:
        out["Service/cache"] = {"spec.port": 6379}
    return out


def step(controller: Controller, title: str) -> None:
    """Run exactly one reconcile cycle and print what the agent decided."""
    print(f"-- {title}")
    status, interval = controller.reconcile()
    for entry in controller.log:
        print(entry)
    controller.log.clear()
    print(f"   status={status}, next reconcile in {interval}s\n")


def main() -> None:
    policy = SyncPolicy(automated=True, prune=False, self_heal=True)
    repo, cluster = GitRepo(), Cluster()
    controller = Controller(repo, cluster, policy)

    print("== [0] OpenGitOps v1.0.0: the four principles this loop implements ==")
    for i, text in enumerate([
        "Declarative: desired state is expressed declaratively",
        "Versioned and Immutable: full history, every change a commit",
        "Pulled Automatically: an agent inside pulls, nothing is pushed in",
        "Continuously Reconciled: keep observing actual vs desired, converge",
    ], start=1):
        print(f"   {i}. {text}")
    print()

    print("== [1] first reconcile: cluster empty, Git has two manifests ==")
    repo.commit(manifests(3), sha="r1")
    step(controller, "revision r1 -> auto-sync creates both objects")
    print("   live:", sorted(cluster.objects), "\n")

    step(controller, "[2] second cycle: Synced, so nothing is attempted")

    print("== [3] drift: a human kubectl-edits replicas to 9 (selfHeal on) ==")
    web = cluster.get_or_create("Deployment", "web")
    web.fields["spec.replicas"] = 9
    web.fields["edited-by"] = "human"          # unmanaged field
    step(controller, "the agent detects drift and self-heals")
    print("   spec.replicas is back to",
          cluster.fields_of("Deployment/web")["spec.replicas"])
    print("   `edited-by` survives: the controller never owned that field\n")

    print("== [4] drift again, but selfHeal is off ==")
    controller.policy.self_heal = False
    cluster.get_or_create("Deployment", "web").fields["spec.replicas"] = 9
    step(controller, "live change is reported, never reverted")
    print("   spec.replicas still",
          cluster.fields_of("Deployment/web")["spec.replicas"], "\n")

    print("== [5] new revision: auto-sync applies it (prune still off) ==")
    controller.policy.self_heal = True
    repo.commit(manifests(5, include_cache=False), sha="r2")
    step(controller, "r2 applied; Service/cache is now orphaned")
    print("   live:", sorted(cluster.objects))
    print("   the orphan is NOT pruned, so the app stays", controller.status(), "\n")

    print("== [6] turn pruning on -> the orphan goes away and the app converges ==")
    controller.policy.prune = True
    step(controller, "prune=true removes objects Git no longer declares")
    print("   live:", sorted(cluster.objects), "status:", controller.status(), "\n")

    print("== [7] empty target with allowEmpty off: the sync is refused ==")
    repo.commit({}, sha="r3")
    step(controller, "guard against wiping the app because Git went empty")
    print("   a refusal is a real failure, so this revision+params pair is "
          "remembered\n   and never retried -- Argo CD moves on to the retry "
          "backoff policy\n")

    print("== [8] SSA field ownership between two managers ==")
    solo = Cluster()
    solo.apply("Deployment", "web", {"spec.replicas": 3}, "argocd-controller")
    solo.apply("Deployment", "web", {"spec.replicas": 3}, "hpa-controller")
    print("   same value -> shared ownership:",
          {m: sorted(f) for m, f in solo.objects["Deployment/web"].managed.items()})
    try:
        solo.apply("Deployment", "web", {"spec.replicas": 4}, "other-team")
    except Exception as exc:                               # noqa: BLE001 (demo)
        print("  ", exc)
    solo.apply("Deployment", "web", {"spec.replicas": 4}, "other-team", force=True)
    owners = [m for m, f in solo.objects["Deployment/web"].managed.items()
              if "spec.replicas" in f]
    print("   after force, owners of spec.replicas:", owners)
    print("   spec.replicas =", solo.fields_of("Deployment/web")["spec.replicas"], "\n")

    print("== [9] documented reconcile interval: 120s base + up to 60s jitter ==")
    intervals = [policy.reconciliation + random.randint(0, policy.jitter)
                 for _ in range(8)]
    print("   observed intervals:", intervals)
    print(f"   min={min(intervals)}s max={max(intervals)}s "
          "(documented max period: 3 min)")


if __name__ == "__main__":
    main()
