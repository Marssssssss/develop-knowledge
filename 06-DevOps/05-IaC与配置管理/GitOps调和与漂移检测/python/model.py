"""GitOps data model: the Git source of truth, the cluster, and SSA ownership.

Refs read before writing this file:
  - OpenGitOps Principles v1.0.0 https://opengitops.dev/
  - Argo CD "Automated Sync Policy"
    https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/
  - Argo CD "Sync Options" https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/
  - Kubernetes "Server-Side Apply"
    https://kubernetes.io/docs/reference/using-api/server-side-apply/

Modelled facts:
  * OpenGitOps v1.0.0 principles: (1) Declarative, (2) Versioned and Immutable,
    (3) Pulled Automatically, (4) Continuously Reconciled. "Pulled" is
    deliberately contrasted with push-based CI/CD, so cluster credentials never
    leave the perimeter.
  * Pruning is OFF by default: "automated sync will not delete resources when
    Argo CD detects the resource is no longer defined in Git". `allowEmpty`
    guards against wiping an app because the target produced no manifests.
  * The reconcile interval is `timeout.reconciliation` in argocd-cm, default
    120 s, "with added jitter of 60s for a maximum period of 3 minutes"; the
    self-heal timeout is 5 s by default.
  * Resources are tracked with the `argocd.argoproj.io/tracking-id` annotation.
  * Server-Side Apply tracks `managedFields` per field manager: writing the same
    value shares ownership, writing a different value owned by someone else is a
    conflict (`force` takes the field from every other manager), and dropping a
    field from your manifest releases it -- the field disappears once nobody
    else owns it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

TRACKING = "argocd.argoproj.io/tracking-id"


class Conflict(Exception):
    """SSA: applying a field another manager owns with a different value."""


@dataclass
class Object:
    kind: str
    name: str
    fields: dict[str, object] = field(default_factory=dict)
    managed: dict[str, set[str]] = field(default_factory=dict)  # manager -> fields

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.name}"


class Cluster:
    """A deliberately tiny API server: SSA merge + managedFields accounting."""

    def __init__(self):
        self.objects: dict[str, Object] = {}

    def get_or_create(self, kind: str, name: str) -> Object:
        key = f"{kind}/{name}"
        if key not in self.objects:
            self.objects[key] = Object(kind=kind, name=name)
        return self.objects[key]

    def fields_of(self, key: str) -> dict:
        obj = self.objects.get(key)
        return obj.fields if obj else {}

    def apply(self, kind: str, name: str, fields: dict, manager: str,
              force: bool = False) -> Object:
        """Server-Side Apply with a fully specified intent.

        The request carries only the fields this manager has an opinion about.
        Conflicts are computed *before* anything is written, so a rejected apply
        leaves the object untouched.
        """
        obj = self.get_or_create(kind, name)
        conflicts = []
        for path, value in fields.items():
            for other, owned in obj.managed.items():
                if other != manager and path in owned and obj.fields.get(path) != value:
                    conflicts.append((path, other))
        if conflicts and not force:
            raise Conflict(f"apply conflict on {obj.key}: {conflicts}")

        if force:  # force strips the field from every other manager
            for other in list(obj.managed):
                if other != manager:
                    obj.managed[other] -= set(fields)
        obj.fields.update(fields)
        obj.managed.setdefault(manager, set()).update(fields)

        # A field you no longer specify is released.
        for path in obj.managed[manager] - set(fields):
            obj.managed[manager].discard(path)
            if not any(path in owned for owned in obj.managed.values()):
                obj.fields.pop(path, None)
        return obj

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@dataclass
class GitRepo:
    """The single source of truth: versioned, immutable, append-only."""

    revisions: list[tuple[str, dict[str, dict]]] = field(default_factory=list)
    head: int = -1

    def commit(self, manifests: dict[str, dict], sha: str | None = None) -> str:
        self.head += 1
        sha = sha or f"{random.randrange(16 ** 7):07x}"
        self.revisions.append((sha, manifests))
        return sha

    @property
    def sha(self) -> str:
        return self.revisions[self.head][0]

    def manifests(self) -> dict[str, dict]:
        return self.revisions[self.head][1]


@dataclass
class SyncPolicy:
    automated: bool = True
    prune: bool = False
    self_heal: bool = False
    allow_empty: bool = False
    reconciliation: int = 120      # argocd-cm timeout.reconciliation
    jitter: int = 60               # documented "maximum period of 3 minutes"
    self_heal_timeout: int = 5     # --self-heal-timeout-seconds default
