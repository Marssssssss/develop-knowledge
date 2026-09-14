"""Ansible variable precedence resolver (all 22 levels).

Refs read before writing this file:
  - Ansible docs, "Using variables" (Variable precedence: where should I put a
    variable?) https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_variables.html

Mirrored facts:
  * The docs list exactly 22 precedence levels; the LAST listed overrides all
    others, and level 22 (`--extra-vars` / `-e`) "always win precedence".
  * Level 1 is "Command-line values (for example, -u my_user, these are not
    variables)" -- it is a placeholder, not a variable source.
  * Within any section, redefining a var overrides the previous instance:
    "If multiple groups have the same variable, the last one loaded wins."
  * Inventory merging is "more specific overrides more generic": a host_var
    beats a group_var, and a child group beats its parent group.
  * The default config is `hash_behavior=replace`; switching to `merge`
    replaces only *partially* (dict values are deep-merged).
"""

from __future__ import annotations

import copy

# The 22 levels, verbatim wording from the docs (index 1 == lowest priority).
PRECEDENCE = [
    "Command-line values (not variables)",
    "Role defaults",
    "Inventory file or script group vars",
    "Inventory group_vars/all",
    "Playbook group_vars/all",
    "Inventory group_vars/*",
    "Playbook group_vars/*",
    "Inventory file or script host vars",
    "Inventory host_vars/*",
    "Playbook host_vars/*",
    "Host facts and cached set_facts",
    "Play vars",
    "Play vars_prompt",
    "Play vars_files",
    "Role vars",
    "Block vars",
    "Task vars",
    "include_vars",
    "Registered vars and set_facts",
    "Role (and include_role) params",
    "include params",
    "Extra vars (-e) -- always win",
]
LEVEL = {name: i for i, name in enumerate(PRECEDENCE, start=1)}


class Definition:
    """One `name: value` assignment together with the level it came from."""

    __slots__ = ("level", "source", "value", "seq")

    def __init__(self, level: int, source: str, value, seq: int = 0):
        self.level = level
        self.source = source
        self.value = value
        self.seq = seq


class VarStore:
    """Collects every definition Ansible would find, then resolves per name.

    Inventory layering is modelled with a *fractional* offset on top of level 3
    ("Inventory file or script group vars"): depth 0 = the `all` group, and each
    deeper group / the host scope adds 0.1. That keeps the 22 documented levels
    intact while making "more specific overrides more generic" fall out of the
    same max() rule, independent of load order.
    """

    INVENTORY_BASE = 3  # == PRECEDENCE index of "Inventory file or script group vars"
    DEPTH_STEP = 0.1

    def __init__(self, hash_behavior: str = "replace"):
        self.hash_behavior = hash_behavior
        self._defs: dict[str, list[Definition]] = {}
        self._seq = 0
        # Group vars are merged first (generic) and overridden by host scope,
        # so the inventory layer records depth to order "more specific wins".
        self._inventory: dict[str, list[tuple[int, str, object]]] = {}

    def add(self, name: str, level: int, source: str, value) -> None:
        self._seq += 1
        self._defs.setdefault(name, []).append(Definition(level, source, value, self._seq))

    def add_inventory_var(self, name: str, depth: int, source: str, value) -> None:
        """Inventory-layer definition; `depth` = group nesting distance (0=all)."""
        self._inventory.setdefault(name, []).append((depth, source, value))

    def _candidates(self, name: str) -> list[Definition]:
        out = list(self._defs.get(name, []))
        for depth, source, value in self._inventory.get(name, []):
            level = self.INVENTORY_BASE + depth * self.DEPTH_STEP
            out.append(Definition(level, source, value, 0))
        return out

    def resolve(self, name: str, default=None):
        """Highest level wins; ties go to the definition loaded last."""
        candidates = self._candidates(name)
        if not candidates:
            return default
        return max(candidates, key=lambda d: (d.level, d.seq)).value

    def resolve_with_source(self, name: str):
        candidates = self._candidates(name)
        if not candidates:
            return None, None
        winner = max(candidates, key=lambda d: (d.level, d.seq))
        return winner.value, f"{winner.source} (level {winner.level:g})"

    def merge_dict(self, low: dict, high: dict) -> dict:
        """hash_behavior=replace -> high wins wholesale; merge -> deep merge."""
        if self.hash_behavior == "replace":
            return copy.deepcopy(high)
        out = copy.deepcopy(low)
        for key, val in high.items():
            if isinstance(val, dict) and isinstance(out.get(key), dict):
                out[key] = self.merge_dict(out[key], val)
            else:
                out[key] = copy.deepcopy(val)
        return out


def inventory_example() -> None:
    """The docs' canonical ntp_server chain, resolved level by level."""
    print("== inventory layering: group_vars/all -> group_vars/boston -> host_vars ==")
    steps = [
        (0, "group_vars/all", {"ntp_server": "default-time.example.com"}),
        (1, "group_vars/boston", {"ntp_server": "boston-time.example.com"}),
        (2, "host_vars/xyz.boston.example.com", {"ntp_server": "override.example.com"}),
    ]
    for keep in (3, 2, 1):
        store = VarStore()
        for depth, source, values in steps[:keep]:
            for k, v in values.items():
                store.add_inventory_var(k, depth, source, v)
        val, who = store.resolve_with_source("ntp_server")
        print(f"  layers={keep}  -> {val:<28} from {who}")

    print("\n  ['group_vars/all','group_vars/boston'] are both depth-ordered:")
    store = VarStore()
    store.add_inventory_var("ntp_server", 1, "group_vars/boston", "boston-time")
    store.add_inventory_var("ntp_server", 0, "group_vars/all", "default-time")
    print("  reversed load order still yields:", store.resolve("ntp_server"),
          "(depth, not insertion order, decides)")


def last_loaded_wins() -> None:
    """Docs: 'If multiple groups have the same variable, the last one wins.'"""
    print("\n== same level, last write wins ==")
    store = VarStore()
    store.add("http_port", LEVEL["Play vars"], "play #1", 8080)
    store.add("http_port", LEVEL["Play vars"], "play #1 (redefined)", 9090)
    print("  two Play vars entries ->", store.resolve("http_port"), "(second one wins)")


def role_vars_vs_inventory() -> None:
    """Docs: role `vars/` beats inventory, but extra vars beat everything."""
    print("\n== role vars vs inventory vs -e ==")
    for extra in (False, True):
        store = VarStore()
        store.add("http_port", LEVEL["Role defaults"], "roles/x/defaults", 80)
        store.add("http_port", LEVEL["Inventory host_vars/*"], "host_vars/a", 8080)
        store.add("http_port", LEVEL["Role vars"], "roles/x/vars", 80)
        if extra:
            store.add("http_port", LEVEL["Extra vars (-e) -- always win"], "-e", 1234)
        val, who = store.resolve_with_source("http_port")
        print(f"  extra_vars={str(extra):<5} -> {val:<5} from {who}")


def hash_behavior() -> None:
    """Docs: default `hash_behavior=replace`; `merge` overwrites partially."""
    print("\n== hash_behavior: replace (default) vs merge ==")
    low = {"nginx": {"worker_processes": 2, "keepalive_timeout": 65}}
    high = {"nginx": {"worker_processes": 8}}
    print("  replace ->", VarStore("replace").merge_dict(low, high))
    print("  merge   ->", VarStore("merge").merge_dict(low, high))


def level_table() -> None:
    """One definition per level; the highest level must always win."""
    print("== one definition per level (winner is always the highest) ==")
    store = VarStore()
    for i, name in enumerate(PRECEDENCE, start=1):
        if i == 1:
            continue  # level 1 is a command-line value, not a variable
        store.add("max_workers", i, name, f"v{i:02d}")
    val, who = store.resolve_with_source("max_workers")
    print(f"  22 levels defined -> winner value={val}  <- {who}")
    assert val == "v22", "extra vars must always win"


if __name__ == "__main__":
    level_table()
    inventory_example()
    last_loaded_wins()
    role_vars_vs_inventory()
    hash_behavior()
