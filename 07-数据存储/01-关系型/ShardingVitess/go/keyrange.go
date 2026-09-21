// Package main 逐行转写 Vitess 的键范围语义(官方文档 reference/features/sharding)。
package main

import (
	"encoding/hex"
	"fmt"
	"math/big"
	"strings"
)

const ksidLen = 8

// canonical 左对齐: 右侧补 0 到 8 字节。文档原文: "Vitess always converts
// sharding keys to a left-justified binary string ... the right-most zeroes are
// insignificant and optional"。
func canonical(ksid []byte) []byte {
	if len(ksid) > ksidLen {
		panic(fmt.Sprintf("keyspace id too long: %x", ksid))
	}
	out := make([]byte, ksidLen)
	copy(out, ksid)
	return out
}

func hexs(b []byte) string {
	if len(b) == 0 {
		return ""
	}
	return hex.EncodeToString(b)
}

// KeyRange 是 [start, end): 含起点, 不含终点。
type KeyRange struct {
	Start []byte
	End   []byte
}

func (kr KeyRange) Contains(ksid []byte) bool {
	k := canonical(ksid)
	if len(kr.Start) > 0 && string(k) < string(canonical(kr.Start)) {
		return false
	}
	if len(kr.End) > 0 && string(k) >= string(canonical(kr.End)) {
		return false
	}
	return true
}

func (kr KeyRange) Name() string {
	return hexs(kr.Start) + "-" + hexs(kr.End)
}

// parseShardName 解析 "80-c0" / "-80" / "c0-" / "-"。
func parseShardName(name string) (KeyRange, error) {
	if strings.Count(name, "-") != 1 {
		return KeyRange{}, fmt.Errorf("bad shard name: %q", name)
	}
	parts := strings.Split(name, "-")
	var kr KeyRange
	var err error
	if parts[0] != "" {
		kr.Start, err = hex.DecodeString(parts[0])
		if err != nil {
			return KeyRange{}, err
		}
	}
	if parts[1] != "" {
		kr.End, err = hex.DecodeString(parts[1])
		if err != nil {
			return KeyRange{}, err
		}
	}
	return kr, nil
}

// validatePartition 判断一组分片名是否构成完整分区。
func validatePartition(names []string) (bool, string) {
	ranges := make([]KeyRange, 0, len(names))
	for _, n := range names {
		kr, err := parseShardName(n)
		if err != nil {
			return false, err.Error()
		}
		ranges = append(ranges, kr)
	}
	for i := 1; i < len(ranges); i++ {
		for j := i; j > 0; j-- {
			if string(canonical(ranges[j].Start)) < string(canonical(ranges[j-1].Start)) {
				ranges[j], ranges[j-1] = ranges[j-1], ranges[j]
			}
		}
	}
	if len(ranges[0].Start) > 0 {
		return false, "最左分片的起点必须为空"
	}
	if len(ranges[len(ranges)-1].End) > 0 {
		return false, "最右分片的终点必须为空"
	}
	for i := 0; i+1 < len(ranges); i++ {
		if string(canonical(ranges[i].End)) != string(canonical(ranges[i+1].Start)) {
			return false, fmt.Sprintf("分片 %s 与 %s 不相接", ranges[i].Name(), ranges[i+1].Name())
		}
	}
	return true, fmt.Sprintf("完整分区(%d 个分片)", len(ranges))
}

// generateShardRanges 按 2 的幂等分; 非幂次在 2^64 空间不整除, 官方未规定, 直接报错。
func generateShardRanges(n int) ([]string, error) {
	if n <= 0 || n&(n-1) != 0 {
		return nil, fmt.Errorf("只支持 2 的幂分片数: %d", n)
	}
	shift := 64
	for n > 1 {
		n >>= 1
		shift--
	}
	step := uint64(1) << shift
	out := make([]string, 0, n)
	for i := 0; i < n; i++ {
		var startHex, endHex string
		if i > 0 {
			startHex = hexs(leftJustified(uint64(i) * step))
		}
		if i < n-1 {
			endHex = hexs(leftJustified(uint64(i+1) * step))
		}
		out = append(out, startHex+"-"+endHex)
	}
	return out, nil
}

// leftJustified 把 8 字节整数写成去掉右侧 0 的最短字节串。
func leftJustified(v uint64) []byte {
	raw := make([]byte, ksidLen)
	for i := 0; i < ksidLen; i++ {
		raw[ksidLen-1-i] = byte(v >> (8 * i))
	}
	end := ksidLen
	for end > 1 && raw[end-1] == 0 {
		end--
	}
	return raw[:end]
}

// splitShard 对半切分一个分片(resharding 的切分动作)。
func splitShard(name string) ([]string, error) {
	kr, err := parseShardName(name)
	if err != nil {
		return nil, err
	}
	// 端点可达 2^64, uint64 装不下, 用大整数做中点。
	lo := new(big.Int).SetBytes(canonical(kr.Start))
	var hi *big.Int
	if len(kr.End) > 0 {
		hi = new(big.Int).SetBytes(canonical(kr.End))
	} else {
		hi = new(big.Int).Lsh(big.NewInt(1), 64)
	}
	mid := new(big.Int).Add(lo, hi)
	mid.Div(mid, big.NewInt(2))
	midU := mid.Uint64()
	left := hexs(kr.Start) + "-" + hexs(leftJustified(midU))
	right := hexs(leftJustified(midU)) + "-" + hexs(kr.End)
	return []string{left, right}, nil
}

// locate 返回包含该 keyspace id 的全部分片名。
func locate(shards []string, ksid []byte) []string {
	hits := []string{}
	for _, s := range shards {
		kr, err := parseShardName(s)
		if err != nil {
			continue
		}
		if kr.Contains(ksid) {
			hits = append(hits, s)
		}
	}
	return hits
}
