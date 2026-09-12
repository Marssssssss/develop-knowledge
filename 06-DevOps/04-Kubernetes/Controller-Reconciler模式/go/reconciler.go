// reconciler.go — Kubernetes Controller Reconciler Loop 模式最小实现
//
// 权威来源:
//   - kubernetes.io/blog/2026/07/29/controller-runtime-cache-explained/
//     (r.Get/r.List 走本地 cache,写走 apiserver,watch 事件入 workqueue)
//
// 核心模型(摘自官方 TL;DR):
//   1. r.Get / r.List 读本地 Indexer(map[ns/name] → object),不是 apiserver。
//      Cache miss 时 fallback 到 APIReader。
//   2. r.Update / Create 写 apiserver,绕开 cache(避免脑裂)。
//   3. watch 事件流: apiserver → Reflector → DeltaFIFO → handler
//      → workqueue(按 key 去重)→ Reconcile(ctx, NamespacedName)。
//   4. workqueue 中只有 key(如 "default/my-pod"),没有对象体。
//      同一 key 重复入队会被静默合并(去重)。
//   5. Reconcile 必须幂等:读到的状态可能滞后,但下次会自我修正。
package main

import "fmt"

type Object struct {
	Ns              string
	Name            string
	Replicas        int
	Ready           int
	ResourceVersion int
}

type WatchEvent struct {
	Event  string // 'A' / 'U' / 'D'
	Target string // "ns/name"
	Obj    Object
}

type APIserver struct {
	objs         []Object
	rvCounter    int
	watchEvents  []WatchEvent
}

func (a *APIserver) find(ns, name string) int {
	for i, o := range a.objs {
		if o.Ns == ns && o.Name == name {
			return i
		}
	}
	return -1
}

func (a *APIserver) Create(o Object) int {
	if a.find(o.Ns, o.Name) >= 0 {
		return -1
	}
	a.rvCounter++
	o.ResourceVersion = a.rvCounter
	a.objs = append(a.objs, o)
	a.watchEvents = append(a.watchEvents, WatchEvent{
		Event: "A", Target: o.Ns + "/" + o.Name, Obj: o,
	})
	return 0
}

func (a *APIserver) Update(o Object) int {
	idx := a.find(o.Ns, o.Name)
	if idx < 0 {
		return -1
	}
	existing := &a.objs[idx]
	if o.ResourceVersion != existing.ResourceVersion {
		return -2 // 409 Conflict
	}
	existing.Replicas = o.Replicas
	existing.Ready = o.Ready
	a.rvCounter++
	existing.ResourceVersion = a.rvCounter
	a.watchEvents = append(a.watchEvents, WatchEvent{
		Event: "U", Target: existing.Ns + "/" + existing.Name, Obj: *existing,
	})
	return 0
}

func (a *APIserver) ConsumeEvents() []WatchEvent {
	ev := a.watchEvents
	a.watchEvents = nil
	return ev
}

// Indexer: local thread-safe map keyed by ns/name
type Indexer struct {
	items map[string]Object
}

func NewIndexer() *Indexer {
	return &Indexer{items: map[string]Object{}}
}

func (i *Indexer) Set(o Object) {
	i.items[o.Ns+"/"+o.Name] = o
}

func (i *Indexer) Get(ns, name string) (Object, bool) {
	o, ok := i.items[ns+"/"+name]
	return o, ok
}

// Workqueue: key-dedup FIFO
type Workqueue struct {
	keys     []string
	inQueue  map[string]bool
}

func NewWorkqueue() *Workqueue {
	return &Workqueue{inQueue: map[string]bool{}}
}

func (q *Workqueue) Add(ns, name string) {
	key := ns + "/" + name
	if q.inQueue[key] {
		fmt.Printf("  [wq] dedup: %s already in queue\n", key)
		return
	}
	q.keys = append(q.keys, key)
	q.inQueue[key] = true
	fmt.Printf("  [wq] enqueue: %s\n", key)
}

func (q *Workqueue) Get() (string, bool) {
	if len(q.keys) == 0 {
		return "", false
	}
	key := q.keys[0]
	q.keys = q.keys[1:]
	delete(q.inQueue, key)
	return key, true
}

func (q *Workqueue) Count() int {
	return len(q.keys)
}

type Reconciler struct {
	api             *APIserver
	cache           *Indexer
	wq              *Workqueue
	reconcileCount  int
	writeCount      int
}

func (r *Reconciler) Reconcile(key string) {
	var ns, name string
	for i := 0; i < len(key); i++ {
		if key[i] == '/' {
			ns = key[:i]
			name = key[i+1:]
			break
		}
	}
	r.reconcileCount++

	cached, ok := r.cache.Get(ns, name)
	if !ok {
		fmt.Printf("  [reconcile %d] %s: not in cache, would fallback to APIReader\n",
			r.reconcileCount, key)
		return
	}
	fmt.Printf("  [reconcile %d] %s: replicas=%d ready=%d rv=%d (from cache)\n",
		r.reconcileCount, key, cached.Replicas, cached.Ready, cached.ResourceVersion)

	if cached.Replicas != cached.Ready {
		updated := Object{
			Ns:              cached.Ns,
			Name:            cached.Name,
			Replicas:        cached.Replicas,
			Ready:           cached.Replicas,
			ResourceVersion: cached.ResourceVersion,
		}
		rc := r.api.Update(updated)
		if rc == -2 {
			fmt.Printf("  [reconcile %d] %s: 409 Conflict, will retry on next event\n",
				r.reconcileCount, key)
			r.wq.Add(ns, name)
		} else if rc == 0 {
			r.writeCount++
			fmt.Printf("  [reconcile %d] %s: updated ready=%d (rv=%d, watch event will refresh cache)\n",
				r.reconcileCount, key, updated.Ready, updated.ResourceVersion)
		}
	} else {
		fmt.Printf("  [reconcile %d] %s: in sync, no action\n", r.reconcileCount, key)
	}
}

// Demo 1
func demo1CacheWarmup() {
	fmt.Println("\n========== Demo 1: List+watch warm-up (cache initial sync) ==========")
	api := &APIserver{}
	cache := NewIndexer()
	wq := NewWorkqueue()
	api.Create(Object{Ns: "default", Name: "web", Replicas: 3, Ready: 3})
	api.Create(Object{Ns: "default", Name: "api", Replicas: 2, Ready: 1})
	api.Create(Object{Ns: "default", Name: "db", Replicas: 1, Ready: 1})

	fmt.Println("[reflector] LIST: populate cache from initial snapshot")
	for _, o := range api.objs {
		cache.Set(o)
		fmt.Printf("  cached %s/%s replicas=%d ready=%d rv=%d\n",
			o.Ns, o.Name, o.Replicas, o.Ready, o.ResourceVersion)
	}
	for _, e := range api.ConsumeEvents() {
		for i := 0; i < len(e.Target); i++ {
			if e.Target[i] == '/' {
				wq.Add(e.Target[:i], e.Target[i+1:])
				break
			}
		}
	}

	fmt.Println("[reconciler] start worker, drain queue:")
	r := &Reconciler{api: api, cache: cache, wq: wq}
	for {
		k, ok := wq.Get()
		if !ok {
			break
		}
		r.Reconcile(k)
	}
	fmt.Printf("[result] reconcile_count=%d, write_count=%d\n", r.reconcileCount, r.writeCount)
}

// Demo 2
func demo2WorkqueueDedup() {
	fmt.Println("\n========== Demo 2: Workqueue dedup (5 events to same key → 1 enqueue) ==========")
	wq := NewWorkqueue()
	fmt.Println("Emit 5 UPDATE events to default/web:")
	for i := 1; i <= 5; i++ {
		fmt.Printf("event %d: UPDATE default/web (replicas changed)\n", i)
		wq.Add("default", "web")
	}
	fmt.Printf("Final queue.count = %d (expected 1)\n", wq.Count())
}

// Demo 3
func demo3StaleRead() {
	fmt.Println("\n========== Demo 3: Stale read after write (eventually consistent) ==========")
	api := &APIserver{}
	cache := NewIndexer()
	wq := NewWorkqueue()
	api.Create(Object{Ns: "default", Name: "web", Replicas: 3, Ready: 2})
	for _, o := range api.objs {
		cache.Set(o)
	}
	api.ConsumeEvents()

	cached, _ := cache.Get("default", "web")
	apiIdx := api.find("default", "web")
	fmt.Printf("Initial state: cache.rv=%d, api.rv=%d\n",
		cached.ResourceVersion, api.objs[apiIdx].ResourceVersion)

	r := &Reconciler{api: api, cache: cache, wq: wq}
	r.Reconcile("default/web")

	cached, _ = cache.Get("default", "web")
	apiIdx = api.find("default", "web")
	fmt.Printf("Right after write: cache.rv=%d (still old), api.rv=%d (new) — STALE!\n",
		cached.ResourceVersion, api.objs[apiIdx].ResourceVersion)

	fmt.Println("[watch] UPDATE event delivered, refresh cache:")
	apiIdx = api.find("default", "web")
	cache.Set(api.objs[apiIdx])
	cached, _ = cache.Get("default", "web")
	fmt.Printf("After watch event: cache.rv=%d, api.rv=%d — CONSISTENT\n",
		cached.ResourceVersion, api.objs[apiIdx].ResourceVersion)

	r.Reconcile("default/web")
}

// Demo 4
func demo4ConflictRetry() {
	fmt.Println("\n========== Demo 4: 409 Conflict retry on concurrent write ==========")
	api := &APIserver{}
	cache := NewIndexer()
	wq := NewWorkqueue()
	api.Create(Object{Ns: "default", Name: "web", Replicas: 3, Ready: 2})
	for _, o := range api.objs {
		cache.Set(o)
	}
	api.ConsumeEvents()

	r := &Reconciler{api: api, cache: cache, wq: wq}
	fmt.Println("Controller A reads cache: rv=1")
	// Another controller writes first
	cached, _ := cache.Get("default", "web")
	other := Object{Ns: "default", Name: "web", Replicas: 3, Ready: 3, ResourceVersion: cached.ResourceVersion}
	api.Update(other)
	apiIdx := api.find("default", "web")
	fmt.Printf("Controller B updates: api.rv=%d (A's view is stale)\n", api.objs[apiIdx].ResourceVersion)

	// A writes with stale rv → 409
	cached, _ = cache.Get("default", "web")
	aWrite := Object{Ns: "default", Name: "web", Replicas: 3, Ready: 3, ResourceVersion: cached.ResourceVersion}
	rc := api.Update(aWrite)
	fmt.Printf("Controller A writes with stale rv: rc=%d (expected -2)\n", rc)
	wq.Add("default", "web")
	fmt.Println("Re-enqueue, waiting for next watch event...")
	if k, ok := wq.Get(); ok {
		apiIdx = api.find("default", "web")
		cache.Set(api.objs[apiIdx])
		r.Reconcile(k)
	}
}

func main() {
	demo1CacheWarmup()
	demo2WorkqueueDedup()
	demo3StaleRead()
	demo4ConflictRetry()
}
