// Package main 转写自 scylladb/scylladb 的 dht/token.cc 与 dht/fixed_shard.cc。
// C++ 的 uint128 乘法在 Go 里用 math/big 落地，避免溢出。
package main

import (
	"fmt"
	"math"
	"math/big"
)

const mask64 = uint64(math.MaxUint64)

// Token 对应 dht::token：三态，数据部分是 int64。
type Token struct {
	Kind string // "before_all_keys" / "key" / "after_all_keys"
	Data int64
}

const (
	kindBefore = "before_all_keys"
	kindKey    = "key"
	kindAfter  = "after_all_keys"
)

func keyToken(v int64) Token { return Token{Kind: kindKey, Data: v} }
func minToken() Token        { return Token{Kind: kindBefore} }
func maxToken() Token        { return Token{Kind: kindAfter} }

func (t Token) isMinimum() bool { return t.Kind == kindBefore }
func (t Token) isMaximum() bool { return t.Kind == kindAfter }

func (t Token) raw() int64 {
	if t.isMaximum() {
		return math.MaxInt64
	}
	return t.Data
}

// unbias 对应 C++：uint64(_data) + uint64(INT64_MIN)，Go 里无符号加法天然回绕。
func (t Token) unbias() uint64 {
	if t.isMaximum() {
		return 0
	}
	return uint64(t.Data) + uint64(math.MinInt64)
}

// bias 是 unbias 的逆：n - uint64(INT64_MIN)，再按 int64 解释。
func bias(n uint64) Token {
	v := int64(n - uint64(math.MinInt64))
	return keyToken(v)
}

// zeroBasedShardOf 对应源码主函数：floor((0.token) * shards)。
func zeroBasedShardOf(token uint64, shards, msb uint) uint64 {
	shifted := token << msb // uint64 左移，高位丢弃
	prod := new(big.Int).SetUint64(shifted)
	prod.Mul(prod, big.NewInt(int64(shards)))
	prod.Rsh(prod, 64)
	return prod.Uint64()
}

func shardOf(shardCount, msb uint, t Token) uint64 {
	switch {
	case t.isMinimum():
		return 0 // token::shard_of_minimum_token()
	case t.isMaximum():
		return uint64(shardCount - 1)
	default:
		return zeroBasedShardOf(t.unbias(), shardCount, msb)
	}
}

// initZeroBasedShardStart 是 zeroBasedShardOf 的逆：ret[s] 为属于 s 的最小 token。
func initZeroBasedShardStart(shards, msb uint) []uint64 {
	if shards == 1 {
		return []uint64{0}
	}
	ret := make([]uint64, shards)
	for s := uint(0); s < shards; s++ {
		num := new(big.Int).Lsh(big.NewInt(1), 64)
		num.Mul(num, big.NewInt(int64(s)))
		num.Div(num, big.NewInt(int64(shards)))
		token := num.Uint64() >> msb
		for zeroBasedShardOf(token, shards, msb) != uint64(s) {
			token++
		}
		ret[s] = token
	}
	return ret
}

// StaticSharder 对应 dht::static_sharder。
type StaticSharder struct {
	ShardCount uint
	MSB        uint
	ShardStart []uint64
}

func NewStaticSharder(shardCount, msb uint) *StaticSharder {
	return &StaticSharder{
		ShardCount: shardCount,
		MSB:        msb,
		ShardStart: initZeroBasedShardStart(shardCount, msb),
	}
}

func (s *StaticSharder) ShardOf(t Token) uint64 {
	return shardOf(s.ShardCount, s.MSB, t)
}

func (s *StaticSharder) ShardForReads(t Token) uint64 { return s.ShardOf(t) }

// ShardForWrites tablet 迁移期要同时写旧、新两个 shard。
func (s *StaticSharder) ShardForWrites(t Token, migrating bool) []uint64 {
	sh := s.ShardOf(t)
	if !migrating {
		return []uint64{sh}
	}
	other := (sh + 1) % uint64(s.ShardCount)
	if other == sh {
		return []uint64{sh}
	}
	if other < sh {
		return []uint64{other, sh}
	}
	return []uint64{sh, other}
}

// TokenForNextShard 目标 shard 必须严格在当前之后，否则视为溢出返回 maximum。
func (s *StaticSharder) TokenForNextShard(t Token, shard, spans uint) Token {
	if t.isMaximum() || shard >= s.ShardCount {
		return maxToken()
	}
	var n uint64
	if !t.isMinimum() {
		n = t.unbias()
	}
	cur := zeroBasedShardOf(n, s.ShardCount, s.MSB)
	if s.MSB == 0 {
		n = s.ShardStart[shard]
		if spans > 1 || shard <= uint(cur) {
			return maxToken()
		}
	} else {
		left := (n >> (64 - s.MSB)) + uint64(spans)
		if uint64(shard) > cur {
			left--
		}
		if left >= uint64(1)<<s.MSB {
			return maxToken()
		}
		n = (left << (64 - s.MSB)) | s.ShardStart[shard]
	}
	return bias(n)
}

func (s *StaticSharder) NextShard(t Token) (uint64, Token, bool) {
	sh := s.ShardForReads(t)
	next := sh + 1
	if next == uint64(s.ShardCount) {
		next = 0
	}
	tok := s.TokenForNextShard(t, uint(next), 1)
	if tok.isMaximum() {
		return 0, Token{}, false
	}
	return next, tok, true
}

// FixedShardPartitioner token 编码 [shard:16][hash:48]。
type FixedShardPartitioner struct{}

const (
	fixedShardBits  = 16
	fixedShardShift = 64 - fixedShardBits
	fixedMaxShard   = math.MaxInt16
	fixedHashMask   = uint64(1<<fixedShardShift) - 1
)

func (FixedShardPartitioner) TokenForShard(shard uint16, hashBits uint64) Token {
	value := int64(uint64(shard)<<fixedShardShift | (hashBits & fixedHashMask))
	return keyToken(value)
}

func (FixedShardPartitioner) ShardOf(t Token) uint64 {
	return uint64(t.raw()) >> fixedShardShift
}

func (f FixedShardPartitioner) ShardOfClamped(t Token, shardCount uint) uint64 {
	sh := f.ShardOf(t)
	if sh > uint64(shardCount-1) {
		return uint64(shardCount - 1)
	}
	return sh
}

func (t Token) String() string {
	if t.Kind != kindKey {
		return fmt.Sprintf("Token(%s)", t.Kind)
	}
	return fmt.Sprintf("Token(key,%d)", t.Data)
}
