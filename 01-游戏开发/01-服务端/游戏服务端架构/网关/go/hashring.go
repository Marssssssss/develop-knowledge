// hashring.go — 一致性哈希环 + 重映射率 / 负载均衡度量
//
// 与 python/gateway.py 的 RingPicker / remap_rate / ring_load、
// c/hashring.c 一一对应。这里的哈希函数用 FNV-1a 32 位: 无依赖、分布均匀。
// 整体背景与运行方式见 gateway.go。
package main

import (
	"fmt"
	"sort"
)


func fnv1a32(s string) uint32 {
	h := uint32(0x811C9DC5)
	for i := 0; i < len(s); i++ {
		h ^= uint32(s[i])
		h *= 0x01000193
	}
	return h
}

// -------------------------------------------------- 二、实例选择
func moduloPick(n, uid int) int { return uid % n }

func instNames(n int) []string {
	out := make([]string, n)
	for i := range out {
		out[i] = fmt.Sprintf("inst%d", i)
	}
	return out
}

// ringPicker: 一致性哈希环。物理节点与虚拟节点按哈希排在环上,
// 取 uid 的顺时针后继; 增删节点只影响相邻区间(约 1/N 的 key 迁移)。
type ringPicker struct {
	nodes  []string
	hashes []uint32
	owners []string
}

type vnode struct {
	h    uint32
	node string
}

func newRingPicker(nodes []string, vn int) *ringPicker {
	r := &ringPicker{nodes: append([]string(nil), nodes...)}
	vs := make([]vnode, 0, len(nodes)*vn)
	for _, node := range r.nodes {
		for v := 0; v < vn; v++ {
			vs = append(vs, vnode{fnv1a32(fmt.Sprintf("%s#%d", node, v)), node})
		}
	}
	sort.Slice(vs, func(i, j int) bool {
		if vs[i].h != vs[j].h {
			return vs[i].h < vs[j].h
		}
		return vs[i].node < vs[j].node
	})
	for _, v := range vs {
		r.hashes = append(r.hashes, v.h)
		r.owners = append(r.owners, v.node)
	}
	return r
}

func (r *ringPicker) owner(uid int) string {
	h := fnv1a32(fmt.Sprintf("uid:%d", uid))
	i := sort.Search(len(r.hashes), func(i int) bool { return r.hashes[i] >= h })
	if i == len(r.hashes) { // 越过环尾 -> 回绕到最小哈希
		i = 0
	}
	return r.owners[i]
}

// pick 返回物理节点下标, 便于与 moduloPick 直接比较。
func (r *ringPicker) pick(uid int) int {
	o := r.owner(uid)
	for i, n := range r.nodes {
		if n == o {
			return i
		}
	}
	return -1
}

type remapResult struct {
	uids                 int
	modulo, ring, ideal  float64
}

// remapRate: 扩容/缩容前后有多少比例的 uid 换了实例。
func remapRate(nBefore, nAfter, uids int) remapResult {
	a := newRingPicker(instNames(nBefore), vnodes)
	b := newRingPicker(instNames(nAfter), vnodes)
	moved, rMoved := 0, 0
	for u := 0; u < uids; u++ {
		if moduloPick(nBefore, u) != moduloPick(nAfter, u) {
			moved++
		}
		if a.pick(u) != b.pick(u) {
			rMoved++
		}
	}
	bigger := nBefore
	if nAfter > bigger {
		bigger = nAfter
	}
	return remapResult{uids,
		float64(moved) / float64(uids), float64(rMoved) / float64(uids),
		1.0 / float64(bigger)}
}

type loadStat struct {
	nodes                    int
	mean                     float64
	ringMaxDev, moduloMaxDev float64
}

func ringLoad(nodes, uids int) loadStat {
	names := instNames(nodes)
	rp := newRingPicker(names, vnodes)
	ringCnt := make([]int, nodes)
	modCnt := make([]int, nodes)
	for u := 0; u < uids; u++ {
		ringCnt[rp.pick(u)]++
		modCnt[moduloPick(nodes, u)]++
	}
	mean := float64(uids) / float64(nodes)
	maxDev := func(cnt []int) float64 {
		d := 0.0
		for _, c := range cnt {
			if v := abs(float64(c) - mean); v > d {
				d = v
			}
		}
		return d / mean
	}
	return loadStat{nodes, mean, maxDev(ringCnt), maxDev(modCnt)}
}

func abs(f float64) float64 {
	if f < 0 {
		return -f
	}
	return f
}
