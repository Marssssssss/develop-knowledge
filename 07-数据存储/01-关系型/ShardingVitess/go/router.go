package main

import (
	"math/big"
	"sort"
)

// RouteResult 是 VTGate 的路由结果。
type RouteResult struct {
	Kind   string
	Shards map[string][]uint64
	Fanout int
}

// routeEqual 等值查询: 每个 id 经 vindex 映射后定位分片。
func routeEqual(vindexName string, ids []uint64, shards []string) (RouteResult, error) {
	if _, err := lookupVindex(vindexName); err != nil {
		return RouteResult{}, err
	}
	targets := map[string][]uint64{}
	for _, id := range ids {
		ksid, err := hashID(vindexName, id)
		if err != nil {
			return RouteResult{}, err
		}
		hits := locate(shards, ksid)
		if len(hits) != 1 {
			return RouteResult{}, errNonUnique{id: id, hits: hits}
		}
		targets[hits[0]] = append(targets[hits[0]], id)
	}
	kind := "multi"
	if len(targets) == 1 {
		kind = "unique"
	}
	return RouteResult{Kind: kind, Shards: targets, Fanout: len(targets)}, nil
}

type errNonUnique struct {
	id   uint64
	hits []string
}

func (e errNonUnique) Error() string {
	return "分区不完整或重叠"
}

// routeRange 范围查询: 仅 Sequential vindex 能映射成 key range。
func routeRange(vindexName string, lo, hi uint64, shards []string) (RouteResult, error) {
	vi, err := lookupVindex(vindexName)
	if err != nil {
		return RouteResult{}, err
	}
	if !vi.Sequential {
		out := map[string][]uint64{}
		for _, s := range shards {
			out[s] = nil
		}
		return RouteResult{Kind: "scatter", Shards: out, Fanout: len(shards)}, nil
	}
	// 上界可达 2^64, uint64 装不下, 与 Python 端一致用大整数求交。
	loK, _ := hashID(vindexName, lo)
	hiK, _ := hashID(vindexName, hi)
	loB := new(big.Int).SetBytes(canonical(loK))
	hiB := new(big.Int).SetBytes(canonical(hiK))
	if loB.Cmp(hiB) > 0 {
		loB, hiB = hiB, loB
	}
	space := new(big.Int).Lsh(big.NewInt(1), 64)
	hits := map[string][]uint64{}
	for _, s := range shards {
		kr, err := parseShardName(s)
		if err != nil {
			return RouteResult{}, err
		}
		sLo := new(big.Int).SetBytes(canonical(kr.Start))
		sHi := space
		if len(kr.End) > 0 {
			sHi = new(big.Int).SetBytes(canonical(kr.End))
		}
		low := new(big.Int)
		if loB.Cmp(sLo) > 0 {
			low = loB
		} else {
			low = sLo
		}
		high := hiB
		if hiB.Cmp(sHi) > 0 {
			high = sHi
		}
		if low.Cmp(high) < 0 {
			hits[s] = nil
		}
	}
	return RouteResult{Kind: "keyrange", Shards: hits, Fanout: len(hits)}, nil
}

// routeUnconstrained 无分片键条件 -> scatter 全部。
func routeUnconstrained(shards []string) RouteResult {
	out := map[string][]uint64{}
	for _, s := range shards {
		out[s] = nil
	}
	return RouteResult{Kind: "scatter", Shards: out, Fanout: len(shards)}
}

// reshard 把 target 对半切, 返回新分区与两个子分片名。
func reshard(shards []string, target string) ([]string, []string, error) {
	found := false
	for _, s := range shards {
		if s == target {
			found = true
		}
	}
	if !found {
		return nil, nil, errMissing{target: target}
	}
	kids, err := splitShard(target)
	if err != nil {
		return nil, nil, err
	}
	out := []string{}
	for _, s := range shards {
		if s == target {
			out = append(out, kids...)
		} else {
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out, kids, nil
}

type errMissing struct {
	target string
}

func (e errMissing) Error() string {
	return "no such shard: " + e.target
}
