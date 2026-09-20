package main

// HPACK 静态表与动态表（RFC 7541 §2.3 / §4）
//
// 与 h2hpack.go 同属 package main，拆出来只为控制单文件行数。
// go run 时用 `go run *.go` 或直接 `go run .`。

import "errors"

// StaticTable RFC 7541 Appendix A 的 61 项，从 RFC 全文逐条抽取
var staticTable = [][2]string{
	{":authority", ""}, {":method", "GET"}, {":method", "POST"},
	{":path", "/"}, {":path", "/index.html"}, {":scheme", "http"},
	{":scheme", "https"}, {":status", "200"}, {":status", "204"},
	{":status", "206"}, {":status", "304"}, {":status", "400"},
	{":status", "404"}, {":status", "500"}, {"accept-charset", ""},
	{"accept-encoding", "gzip, deflate"}, {"accept-language", ""},
	{"accept-ranges", ""}, {"accept", ""}, {"access-control-allow-origin", ""},
	{"age", ""}, {"allow", ""}, {"authorization", ""}, {"cache-control", ""},
	{"content-disposition", ""}, {"content-encoding", ""},
	{"content-language", ""}, {"content-length", ""}, {"content-location", ""},
	{"content-range", ""}, {"content-type", ""}, {"cookie", ""}, {"date", ""},
	{"etag", ""}, {"expect", ""}, {"expires", ""}, {"from", ""}, {"host", ""},
	{"if-match", ""}, {"if-modified-since", ""}, {"if-none-match", ""},
	{"if-range", ""}, {"if-unmodified-since", ""}, {"last-modified", ""},
	{"link", ""}, {"location", ""}, {"max-forwards", ""},
	{"proxy-authenticate", ""}, {"proxy-authorization", ""}, {"range", ""},
	{"referer", ""}, {"refresh", ""}, {"retry-after", ""}, {"server", ""},
	{"set-cookie", ""}, {"strict-transport-security", ""},
	{"transfer-encoding", ""}, {"user-agent", ""}, {"vary", ""}, {"via", ""},
	{"www-authenticate", ""},
}

const staticSize = 61

// EntrySize RFC 7541 §4.1：name + value + 32
func EntrySize(name, value string) int { return len(name) + len(value) + 32 }

// Table 动态表：Dyn[0] 最新，尾部最旧（索引 62 起）
type Table struct {
	MaxSize int
	Dyn     [][2]string
	Size    int
}

func (t *Table) evictTo(target int) {
	for t.Size > target && len(t.Dyn) > 0 {
		last := t.Dyn[len(t.Dyn)-1]
		t.Dyn = t.Dyn[:len(t.Dyn)-1]
		t.Size -= EntrySize(last[0], last[1])
	}
}

// SetMaxSize 改小上限时从尾部逐出（RFC 7541 §4.3）
func (t *Table) SetMaxSize(n int) {
	t.MaxSize = n
	t.evictTo(n)
}

// Add 返回是否真的插入；新条目大于 MaxSize 时整表清空且不算错误（§4.4）
func (t *Table) Add(name, value string) bool {
	esz := EntrySize(name, value)
	if esz > t.MaxSize {
		t.Dyn = nil
		t.Size = 0
		return false
	}
	t.evictTo(t.MaxSize - esz)
	t.Dyn = append([][2]string{{name, value}}, t.Dyn...)
	t.Size += esz
	return true
}

// Lookup 索引 1..61 静态，62.. 动态
func (t *Table) Lookup(index int) ([2]string, error) {
	if index <= 0 {
		return [2]string{}, errors.New("索引 0 必须判为解码错误")
	}
	if index <= staticSize {
		return staticTable[index-1], nil
	}
	off := index - staticSize - 1
	if off >= len(t.Dyn) {
		return [2]string{}, errors.New("动态表索引越界")
	}
	return t.Dyn[off], nil
}
