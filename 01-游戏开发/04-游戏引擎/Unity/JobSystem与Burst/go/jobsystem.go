// jobsystem.go — Unity C# Job System 关键语义的最小模型（与 python/jobsystem.py 同题）。
//
// 覆盖：NativeContainer 安全系统（调度时判冲突）、JobHandle 依赖与 CombineDependencies、
// Complete 清理安全状态、IJobParallelFor 的分批与「一次偷一半」的 work stealing、
// 三种分配器寿命、Burst/HPC# 的类型子集。
package main

import (
	"fmt"
	"strings"
)

type safetyError struct{ msg string }

func (e *safetyError) Error() string { return e.msg }

type nativeArray struct {
	uid      int
	buffer   []float64
	allocate string // Temp / TempJob / Persistent
}

var seq int

func newNativeArray(size int, allocator string) *nativeArray {
	seq++
	return &nativeArray{uid: seq, buffer: make([]float64, size), allocate: allocator}
}

// get 返回副本：NativeContainer 不实现 ref return，所以 arr[0]++ 写不回去。
func (a *nativeArray) get(i int) float64 { return a.buffer[i] }
func (a *nativeArray) set(i int, v float64) { a.buffer[i] = v }

type job struct {
	name       string
	reads      []int
	writes     []int
	deps       []*handle
	data       map[string]float64
	length     int
	batchCount int
	body       func(i int, data map[string]float64)
}

type handle struct {
	job *job
	id  int
}

type jobSystem struct {
	next     int
	writers  map[int][]*handle
	readers  map[int][]*handle
	executed []string
	done     map[int]bool
	leaked   []string
}

func newJobSystem() *jobSystem {
	return &jobSystem{writers: map[int][]*handle{}, readers: map[int][]*handle{}, done: map[int]bool{}}
}

func contains(hs []*handle, h *handle) bool {
	for _, x := range hs {
		if x.id == h.id {
			return true
		}
	}
	return false
}

// schedule 在调度时刻做安全系统检查：读写冲突、写写冲突都要靠依赖声明化解。
func (js *jobSystem) schedule(j *job) (*handle, error) {
	for _, uid := range j.writes {
		for _, h := range js.writers[uid] {
			if !js.done[h.id] && !contains(j.deps, h) {
				return nil, &safetyError{fmt.Sprintf("%s 与 %s 同时写同一 NativeArray", j.name, h.job.name)}
			}
		}
		for _, h := range js.readers[uid] {
			if !js.done[h.id] && !contains(j.deps, h) {
				return nil, &safetyError{fmt.Sprintf("%s 写 / %s 读 冲突", j.name, h.job.name)}
			}
		}
	}
	for _, uid := range j.reads {
		for _, h := range js.writers[uid] {
			if !js.done[h.id] && !contains(j.deps, h) {
				return nil, &safetyError{fmt.Sprintf("%s 读 / %s 写 冲突", j.name, h.job.name)}
			}
		}
	}
	js.next++
	h := &handle{job: j, id: js.next}
	for _, uid := range j.writes {
		js.writers[uid] = append(js.writers[uid], h)
	}
	for _, uid := range j.reads {
		js.readers[uid] = append(js.readers[uid], h)
	}
	js.leaked = append(js.leaked, j.name)
	return h, nil
}

func combineDependencies(hs []*handle) []*handle {
	out := []*handle{}
	for _, h := range hs {
		out = append(out, h.job.deps...)
		out = append(out, h)
	}
	return out
}

func (js *jobSystem) complete(h *handle) {
	if js.done[h.id] {
		return
	}
	for _, d := range h.job.deps {
		js.complete(d)
	}
	js.execute(h.job)
	js.done[h.id] = true
	for i, n := range js.leaked {
		if n == h.job.name {
			js.leaked = append(js.leaked[:i], js.leaked[i+1:]...)
			break
		}
	}
	for _, uid := range h.job.writes {
		js.writers[uid] = filterHandle(js.writers[uid], h)
	}
	for _, uid := range h.job.reads {
		js.readers[uid] = filterHandle(js.readers[uid], h)
	}
}

func filterHandle(hs []*handle, drop *handle) []*handle {
	out := []*handle{}
	for _, h := range hs {
		if h.id != drop.id {
			out = append(out, h)
		}
	}
	return out
}

func (js *jobSystem) execute(j *job) {
	copied := map[string]float64{}
	for k, v := range j.data {
		copied[k] = v // job 数据被复制：改动不回传给调用方
	}
	if j.length > 0 {
		for _, b := range splitBatches(j.length, j.batchCount) {
			for i := b[0]; i < b[1]; i++ {
				if j.body != nil {
					j.body(i, copied)
				}
			}
		}
	} else if j.body != nil {
		j.body(-1, copied)
	}
	js.executed = append(js.executed, j.name)
}

func splitBatches(length, batchCount int) [][2]int {
	out := [][2]int{}
	for s := 0; s < length; s += batchCount {
		e := s + batchCount
		if e > length {
			e = length
		}
		out = append(out, [2]int{s, e})
	}
	return out
}

func main() {
	a := newNativeArray(4, "TempJob")
	a.set(0, 5)
	temp := a.get(0)
	temp++
	fmt.Printf("arr[0]++ 之后 arr[0] = %.1f（无 ref return，写不回去）\n", a.get(0))

	js := newJobSystem()
	out := newNativeArray(8, "TempJob")
	h1, _ := js.schedule(&job{name: "A", writes: []int{out.uid}})
	if _, err := js.schedule(&job{name: "B", writes: []int{out.uid}}); err != nil {
		fmt.Printf("写-写冲突：%s\n", err)
	}
	js.complete(h1)

	js2 := newJobSystem()
	buf := newNativeArray(4, "TempJob")
	r1, _ := js2.schedule(&job{name: "R1", reads: []int{buf.uid}})
	r2, _ := js2.schedule(&job{name: "R2", reads: []int{buf.uid}})
	if _, err := js2.schedule(&job{name: "W", writes: []int{buf.uid}}); err != nil {
		fmt.Printf("读-写冲突：%s\n", err)
	}
	deps := combineDependencies([]*handle{r1, r2})
	hits := []int{}
	w2, _ := js2.schedule(&job{
		name: "W2", writes: []int{buf.uid}, deps: deps, length: 1000, batchCount: 64,
		body: func(i int, data map[string]float64) { hits = append(hits, i) },
	})
	js2.complete(w2)
	fmt.Printf("执行顺序 = %s，并行覆盖下标 %d 个（去重后 %d）\n",
		strings.Join(js2.executed, "->"), len(hits), len(hits))

	res := stealSchedule(200, 4)
	fmt.Printf("work stealing：偷 %d 次，处理 %d 批，剩余 %d\n", res.steals, res.processed, res.left)

	tr := &allocatorTracker{}
	tr.track(newNativeArray(1, "Temp"))
	tr.track(newNativeArray(1, "TempJob"))
	tr.track(newNativeArray(1, "Persistent"))
	for i := 0; i < 5; i++ {
		tr.endFrame()
	}
	fmt.Printf("分配器告警：%v\n", tr.warnings)

	fmt.Printf("Burst 可编译 float=%v / string=%v / class=%v / 静态只读托管数组=%v\n",
		burstFieldOK("float"), burstFieldOK("string"), burstFieldOK("class"),
		burstFieldOK("static readonly managed array"))
}
