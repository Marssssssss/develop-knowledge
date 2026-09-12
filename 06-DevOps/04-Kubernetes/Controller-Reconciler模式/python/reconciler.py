"""
Kubernetes Controller Reconciler Loop minimal implementation.
Based on kubernetes.io/blog/2026/07/29/controller-runtime-cache-explained/.

Core model (TL;DR from official docs):
  1. r.Get / r.List reads from local Indexer (map[ns/name] → object),
     NOT apiserver. Cache miss falls back to APIReader.
  2. r.Update/Create writes to apiserver (bypasses cache).
  3. Watch event flow: apiserver → Reflector → DeltaFIFO → handler
     → workqueue (key dedup) → Reconcile(ctx, NamespacedName).
  4. Workqueue stores only keys (e.g. "default/my-pod"), no object body.
     Repeated keys for same object are silently deduplicated.
  5. Reconcile MUST be idempotent (stale reads are possible).
  6. Periodic re-trigger uses Result{RequeueAfter: 30s}, not time.Sleep.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class Object:
    ns: str
    name: str
    replicas: int
    ready: int
    resource_version: int = 0


@dataclass
class WatchEvent:
    event: str   # 'A' / 'U' / 'D'
    target: str  # "ns/name"
    obj: Object


class APIserver:
    def __init__(self):
        self.objs: List[Object] = []
        self.rv_counter = 0
        self.watch_events: List[WatchEvent] = []

    def _find(self, ns: str, name: str) -> int:
        for i, o in enumerate(self.objs):
            if o.ns == ns and o.name == name:
                return i
        return -1

    def create(self, obj: Object) -> int:
        if self._find(obj.ns, obj.name) >= 0:
            return -1
        self.rv_counter += 1
        obj.resource_version = self.rv_counter
        self.objs.append(obj)
        self.watch_events.append(WatchEvent('A', f"{obj.ns}/{obj.name}", obj))
        return 0

    def update(self, obj: Object) -> int:
        idx = self._find(obj.ns, obj.name)
        if idx < 0:
            return -1
        existing = self.objs[idx]
        if obj.resource_version != existing.resource_version:
            return -2  # 409 Conflict
        existing.replicas = obj.replicas
        existing.ready = obj.ready
        self.rv_counter += 1
        existing.resource_version = self.rv_counter
        self.watch_events.append(WatchEvent('U', f"{existing.ns}/{existing.name}", existing))
        return 0

    def consume_events(self) -> List[WatchEvent]:
        events = self.watch_events[:]
        self.watch_events = []
        return events


class Indexer:
    """Local thread-safe map keyed by ns/name (per docs)."""
    def __init__(self):
        self.items: Dict[str, Object] = {}

    def set(self, obj: Object):
        self.items[f"{obj.ns}/{obj.name}"] = obj

    def get(self, ns: str, name: str) -> Optional[Object]:
        return self.items.get(f"{ns}/{name}")


class Workqueue:
    """Key-dedup FIFO."""
    def __init__(self):
        self.keys: List[str] = []
        self.in_queue: set = set()

    def add(self, ns: str, name: str):
        key = f"{ns}/{name}"
        if key in self.in_queue:
            print(f"  [wq] dedup: {key} already in queue")
            return
        self.keys.append(key)
        self.in_queue.add(key)
        print(f"  [wq] enqueue: {key}")

    def get(self) -> Optional[str]:
        if not self.keys:
            return None
        key = self.keys.pop(0)
        self.in_queue.discard(key)
        return key

    @property
    def count(self) -> int:
        return len(self.keys)


class Reconciler:
    def __init__(self, api: APIserver, cache: Indexer, wq: Workqueue):
        self.api = api
        self.cache = cache
        self.wq = wq
        self.reconcile_count = 0
        self.write_count = 0

    def reconcile(self, key: str):
        ns, _, name = key.partition("/")
        self.reconcile_count += 1
        # 1. r.Get reads from CACHE (not apiserver)
        cached = self.cache.get(ns, name)
        if not cached:
            print(f"  [reconcile {self.reconcile_count}] {key}: not in cache, "
                  f"would fallback to APIReader")
            return
        print(f"  [reconcile {self.reconcile_count}] {key}: replicas={cached.replicas} "
              f"ready={cached.ready} rv={cached.resource_version} (from cache)")

        # 2. Reconcile logic: ensure ready == replicas
        if cached.replicas != cached.ready:
            # 3. r.Update writes to APISERVER (bypasses cache)
            updated = Object(ns=cached.ns, name=cached.name,
                             replicas=cached.replicas, ready=cached.replicas,
                             resource_version=cached.resource_version)
            rc = self.api.update(updated)
            if rc == -2:
                print(f"  [reconcile {self.reconcile_count}] {key}: 409 Conflict, "
                      f"will retry on next event")
                self.wq.add(ns, name)
            elif rc == 0:
                self.write_count += 1
                print(f"  [reconcile {self.reconcile_count}] {key}: updated ready="
                      f"{updated.ready} (rv={updated.resource_version}, "
                      f"watch event will refresh cache)")
        else:
            print(f"  [reconcile {self.reconcile_count}] {key}: in sync, no action")


# ============== Demo 1: list+watch warm-up ==============
def demo1_cache_warmup():
    print("\n========== Demo 1: List+watch warm-up (cache initial sync) ==========")
    api = APIserver()
    cache = Indexer()
    wq = Workqueue()
    api.create(Object("default", "web", 3, 3))
    api.create(Object("default", "api", 2, 1))
    api.create(Object("default", "db",  1, 1))

    print("[reflector] LIST: populate cache from initial snapshot")
    for o in api.objs:
        cache.set(o)
        print(f"  cached {o.ns}/{o.name} replicas={o.replicas} ready={o.ready} rv={o.resource_version}")

    for e in api.consume_events():
        wq.add(e.target.split("/")[0], e.target.split("/")[1])

    print("[reconciler] start worker, drain queue:")
    r = Reconciler(api, cache, wq)
    while True:
        k = wq.get()
        if k is None:
            break
        r.reconcile(k)
    print(f"[result] reconcile_count={r.reconcile_count}, write_count={r.write_count}")


# ============== Demo 2: workqueue dedup ==============
def demo2_workqueue_dedup():
    print("\n========== Demo 2: Workqueue dedup (5 events to same key → 1 enqueue) ==========")
    wq = Workqueue()
    print("Emit 5 UPDATE events to default/web:")
    for i in range(5):
        print(f"event {i+1}: UPDATE default/web (replicas changed)")
        wq.add("default", "web")
    print(f"Final queue.count = {wq.count} (expected 1)")


# ============== Demo 3: stale read after write ==============
def demo3_stale_read():
    print("\n========== Demo 3: Stale read after write (eventually consistent) ==========")
    api = APIserver()
    cache = Indexer()
    wq = Workqueue()
    api.create(Object("default", "web", 3, 2))
    for o in api.objs:
        cache.set(o)
    api.consume_events()

    cached = cache.get("default", "web")
    api_idx = api._find("default", "web")
    print(f"Initial state: cache.rv={cached.resource_version}, "
          f"api.rv={api.objs[api_idx].resource_version}")

    r = Reconciler(api, cache, wq)
    r.reconcile("default/web")  # write to apiserver

    cached = cache.get("default", "web")
    api_idx = api._find("default", "web")
    print(f"Right after write: cache.rv={cached.resource_version} (still old), "
          f"api.rv={api.objs[api_idx].resource_version} (new) — STALE!")

    print("[watch] UPDATE event delivered, refresh cache:")
    api_idx = api._find("default", "web")
    cache.set(api.objs[api_idx])
    cached = cache.get("default", "web")
    print(f"After watch event: cache.rv={cached.resource_version}, "
          f"api.rv={api.objs[api_idx].resource_version} — CONSISTENT")

    r.reconcile("default/web")


# ============== Demo 4: 409 Conflict retry ==============
def demo4_conflict_retry():
    print("\n========== Demo 4: 409 Conflict retry on concurrent write ==========")
    api = APIserver()
    cache = Indexer()
    wq = Workqueue()
    api.create(Object("default", "web", 3, 2))
    for o in api.objs:
        cache.set(o)
    api.consume_events()

    r = Reconciler(api, cache, wq)
    print("Controller A reads cache: rv=1")
    # Another controller writes first
    other = Object("default", "web", 3, 3, resource_version=cache.get("default", "web").resource_version)
    api.update(other)
    api_idx = api._find("default", "web")
    print(f"Controller B updates: api.rv={api.objs[api_idx].resource_version} (A's view is stale)")

    # A writes with stale rv → 409
    a_write = Object("default", "web", 3, 3,
                     resource_version=cache.get("default", "web").resource_version)
    rc = api.update(a_write)
    print(f"Controller A writes with stale rv: rc={rc} (expected -2)")
    wq.add("default", "web")
    print("Re-enqueue, waiting for next watch event...")
    k = wq.get()
    if k:
        api_idx = api._find("default", "web")
        cache.set(api.objs[api_idx])
        r.reconcile(k)


if __name__ == "__main__":
    demo1_cache_warmup()
    demo2_workqueue_dedup()
    demo3_stale_read()
    demo4_conflict_retry()
