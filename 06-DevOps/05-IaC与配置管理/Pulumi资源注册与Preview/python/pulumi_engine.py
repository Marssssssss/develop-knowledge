"""A miniature Pulumi deployment engine: registration, URNs, preview vs up.

Refs read before writing this file:
  - "How Pulumi works" https://www.pulumi.com/docs/iac/concepts/how-pulumi-works/
  - "Resource names and identity" https://www.pulumi.com/docs/iac/concepts/resources/names/
  - "Type system" (URN EBNF grammar) https://pulumi-developer-docs.readthedocs.io/latest/docs/architecture/types/README.html

Mirrored facts (see README for the full quotes):
  * language host + deployment engine + resource provider; the engine never
    talks to the cloud itself, it asks a provider plugin;
  * construction only *registers* a resource -- "the call returning does not
    mean the bucket was created" -- and independent registrations run in
    parallel; a state entry with no registration this run is scheduled for
    deletion; `refresh` needs an explicit flag;
  * replacement defaults to create-then-delete, `deleteBeforeReplace` flips it,
    and disabling auto-naming implies it;
  * URN = urn:pulumi:<stack>::<project>::<qualified type>::<name> with
    qualified type = [parentType "$"] type; duplicates are rejected;
  * an Output carries its dependencies plus a known/unknown flag, and preview
    keeps everything derived from a not-yet-created resource unknown.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

UNKNOWN = "<unknown>"
HEX = "0123456789abcdef"


class DuplicateURN(Exception):
    """Pulumi's `error: Duplicate resource URN '...'`."""


def urn(stack: str, project: str, base_type: str, name: str,
        parent_type: str | None = None) -> str:
    """Build a URN per the documented EBNF grammar."""
    qualified = f"{parent_type}${base_type}" if parent_type else base_type
    return f"urn:pulumi:{stack}::{project}::{qualified}::{name}"


@dataclass
class Output:
    """A node in the program graph: a value plus its provenance."""

    urn: str | None = None
    known: bool = True
    value: object = None
    deps: frozenset[str] = frozenset()

    def derive(self, fn) -> "Output":
        """Any computation on an unknown output stays unknown (documented)."""
        if not self.known:
            return Output(known=False, value=UNKNOWN, deps=self.deps)
        return Output(known=True, value=fn(self.value), deps=self.deps)


@dataclass
class Resource:
    type: str
    name: str
    inputs: dict
    urn: str
    parent: str | None = None
    outputs: dict[str, Output] = field(default_factory=dict)

    def output(self, key: str) -> Output:
        """Reading an output establishes a dependency on this resource."""
        if key not in self.outputs:
            self.outputs[key] = Output(urn=self.urn, deps=frozenset({self.urn}))
        return self.outputs[key]


class Provider:
    """Stand-in for a resource plugin: knows which inputs force replacement."""

    def __init__(self, replace_on: dict[str, set[str]]):
        self.replace_on = replace_on

    def forces_replace(self, type_: str, changed: set[str]) -> bool:
        return bool(changed & self.replace_on.get(type_, set()))


class Engine:
    def __init__(self, project: str, stack: str, provider: Provider, live: bool):
        self.project = project
        self.stack = stack
        self.provider = provider
        self.live = live                      # False == `pulumi preview`
        self.state: dict[str, dict] = {}      # the checkpoint / state file
        self.registered: dict[str, Resource] = {}

    # -- language host side --------------------------------------------------
    def register(self, type_: str, name: str, inputs: dict,
                 parent: Resource | None = None) -> Resource:
        u = urn(self.stack, self.project, type_, name,
                parent.type if parent else None)
        if u in self.registered:
            raise DuplicateURN(f"error: Duplicate resource URN '{u}'")
        res = Resource(type=type_, name=name, inputs=inputs, urn=u,
                       parent=parent.urn if parent else None)
        self.registered[u] = res

        # In preview a resource that does not exist yet reports UNKNOWN outputs;
        # an existing one keeps the values recorded in state.
        old = self.state.get(u)
        res.outputs["id"] = Output(
            urn=u,
            known=old is not None,
            value=UNKNOWN if old is None else old["id"],
            deps=frozenset({u}),
        )
        return res

    # -- engine side ---------------------------------------------------------
    def dependencies(self, res: Resource) -> set[str]:
        """Edges come from Outputs used as Inputs, plus the explicit parent."""
        deps: set[str] = set()
        for val in res.inputs.values():
            if isinstance(val, Output):
                deps |= {d for d in val.deps if d != res.urn}
        if res.parent:
            deps.add(res.parent)
        return deps

    def waves(self) -> list[list[str]]:
        """Kahn layering: a resource waits for every resource it consumes."""
        pending = {u: self.dependencies(r) for u, r in self.registered.items()}
        done: set[str] = set()
        waves: list[list[str]] = []
        while pending:
            ready = sorted(u for u, d in pending.items() if d <= done)
            if not ready:                     # cycle: keep it debuggable
                waves.append(sorted(pending))
                break
            waves.append(ready)
            done |= set(ready)
            for u in ready:
                del pending[u]
        return waves

    MISSING = object()

    def plan(self) -> dict[str, str]:
        """Classify every URN: create / same / update / replace / delete."""
        ops: dict[str, str] = {}
        for u, res in self.registered.items():
            old = self.state.get(u)
            if old is None:
                ops[u] = "create"
                continue
            changed: set[str] = set()
            for key in set(old["inputs"]) | set(res.inputs):
                new_v = res.inputs.get(key, self.MISSING)
                old_v = old["inputs"].get(key, self.MISSING)
                if isinstance(new_v, Output):
                    continue        # output-derived input: not statically comparable
                if new_v is self.MISSING or old_v is self.MISSING or new_v != old_v:
                    changed.add(key)
            if not changed:
                ops[u] = "same"
            elif self.provider.forces_replace(res.type, changed):
                ops[u] = "replace"
            else:
                ops[u] = "update"
        # A state entry with no registration request this run gets deleted.
        for u in self.state:
            ops.setdefault(u, "delete")
        return ops

    def apply(self, ops: dict[str, str]) -> list[str]:
        """Walk the waves in dependency order; in preview nothing is written."""
        log: list[str] = []
        for wave in self.waves():
            for u in wave:
                res = self.registered[u]
                op = ops.get(u, "same")
                if op == "same":
                    log.append(f"    {'':<3} {res.name:<19} unchanged")
                    continue
                tag = {"create": "+", "update": "~", "replace": "+-"}[op]
                if op == "replace":
                    log.append(f"    ++  {res.name:<19} create-replacement")
                if self.live:
                    self._write(u, res)
                    log.append(f"    {tag:<3} {res.name:<19} {op}d")
                    if op == "replace":
                        log.append(f"    --  {res.name:<19} delete-replaced")
                else:
                    log.append(f"    {tag:<3} {res.name:<19} {op} (preview)")
        for u in list(self.state):
            if u not in self.registered:
                dead = self.state.pop(u)
                if self.live:
                    log.append(f"    -   {dead['name']:<19} deleted")
        return log

    def _write(self, u: str, res: Resource) -> None:
        """Provider create/update: auto-name the physical resource, record state."""
        explicit = res.inputs.get("name") or res.inputs.get("bucket")
        suffix = "" if explicit else "-" + "".join(random.choice(HEX) for _ in range(7))
        self.state[u] = {
            "type": res.type,
            "name": res.name,
            "id": res.type.split(":")[0] + "-" + "".join(random.choice(HEX)
                                                         for _ in range(12)),
            "physical_name": explicit or res.name + suffix,
            "inputs": {k: v for k, v in res.inputs.items()
                       if not isinstance(v, Output)},
        }


# --------------------------------------------------------------------- demo

def bucket_stack(engine: Engine, content_name: str = "content-bucket",
                 content_tags: str = "owner=platform"):
    """media-bucket, plus a second bucket whose tags derive from its id."""
    media = engine.register("aws:s3/bucket:Bucket", "media-bucket", {})
    arn = media.output("id").derive(lambda v: f"{v}:arn")
    engine.register("aws:s3/bucket:Bucket", content_name, {"tags": arn})
    return media


def show(title: str, ops: dict[str, str], log: list[str]) -> None:
    print(f"== {title} ==")
    print("   ops:", {u.split('::')[-1]: op for u, op in ops.items()})
    for line in log:
        print(line)
    print()


def main() -> None:
    provider = Provider({"aws:s3/bucket:Bucket": {"bucket", "name"}})
    bucket = "aws:s3/bucket:Bucket"

    print("== URN grammar (project acmecorp-website, stack production) ==")
    print("  ", urn("production", "acmecorp-website", bucket, "my-bucket"))
    print("  ", urn("production", "acmecorp-website", bucket, "my-bucket",
                    parent_type="custom:resources:Resource"))
    print()

    engine = Engine("acmecorp-website", "production", provider, live=False)
    bucket_stack(engine)
    show("[1] preview on an empty stack: both buckets are `+`, ids unknown",
         engine.plan(), engine.apply(engine.plan()))
    assert all(not r.outputs["id"].known for r in engine.registered.values())

    content = engine.registered[list(engine.registered)[1]]
    print("== unknown propagation + dependency edge ==")
    print("   content-bucket.tags <- media-bucket.id.derive(..)",
          " known =", content.inputs["tags"].known)
    print("   dependency waves:",
          [[u.split("::")[-1] for u in w] for w in engine.waves()])
    print("   -> media-bucket is created in wave 1, content-bucket in wave 2")
    print()

    engine.live = True
    show("[2] up: auto-naming appends a random suffix to physical names",
         engine.plan(), engine.apply(engine.plan()))
    print("   physical names:", {v["name"]: v["physical_name"]
                                for v in engine.state.values()})
    print()

    base = dict(engine.state)

    renamed = Engine("acmecorp-website", "production", provider, live=False)
    renamed.state = {u: dict(v) for u, v in base.items()}
    bucket_stack(renamed, content_name="app-bucket")
    show("[3] rename content-bucket -> app-bucket: one create + one delete",
         renamed.plan(), renamed.apply(renamed.plan()))

    updated = Engine("acmecorp-website", "production", provider, live=False)
    updated.state = {u: dict(v) for u, v in base.items()}
    updated.register("aws:s3/bucket:Bucket", "media-bucket", {})
    updated.register("aws:s3/bucket:Bucket", "content-bucket", {"tags": "owner=infra"})
    show("[4] only `tags` changed: the provider updates it in place",
         updated.plan(), updated.apply(updated.plan()))

    pinned = Engine("acmecorp-website", "production", provider, live=False)
    pinned.register("aws:s3/bucket:Bucket", "media-bucket", {"bucket": "fixed-1"})
    pinned.live = True
    pinned.apply(pinned.plan())

    replaced = Engine("acmecorp-website", "production", provider, live=False)
    replaced.state = {u: dict(v) for u, v in pinned.state.items()}
    replaced.register("aws:s3/bucket:Bucket", "media-bucket", {"bucket": "fixed-2"})
    show("[5] `bucket` changed and the provider says it cannot be patched "
         "-> replace", replaced.plan(), replaced.apply(replaced.plan()))
    print("   default order is create-replacement then delete-replaced;")
    print("   `deleteBeforeReplace` (implied when auto-naming is off) swaps them.\n")
    print("== [6] duplicate URN is rejected ==")
    try:
        replaced.register("aws:s3/bucket:Bucket", "media-bucket", {})
    except DuplicateURN as exc:
        print("  ", exc)


if __name__ == "__main__":
    main()
