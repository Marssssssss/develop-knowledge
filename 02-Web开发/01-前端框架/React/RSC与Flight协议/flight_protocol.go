// React Flight（RSC 线格式）行状态机与引用解析的 Go 复刻，与 flight_protocol.py 同题。
// 依据：packages/react-client/src/ReactFlightClient.js（行状态机 + parseModelString 引用前缀表）
package main

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// 带「字节长度」前缀的 tag 集合（源码 ROW_TAG 分支）
const lengthTags = "TAOobUSsLlGgMmV"

// 其余按换行切分的 tag：A-Z 以及 '#' 'r' 'x'
const newlineTags = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#rx"

const (
	rowID = iota
	rowTag
	rowLength
	byNewline
	byLength
)

type Chunk struct {
	ID      int
	Status  string // "pending" | "fulfilled"
	Value   interface{}
	Raw     string
	waiters []func(interface{})
}

func (c *Chunk) Resolve(v interface{}) {
	c.Value = v
	c.Status = "fulfilled"
	for _, w := range c.waiters {
		w(v)
	}
	c.waiters = nil
}

func (c *Chunk) Then(fn func(interface{})) {
	if c.Status == "fulfilled" {
		fn(c.Value)
		return
	}
	c.waiters = append(c.waiters, fn)
}

type Lazy struct{ C *Chunk }

func (l *Lazy) Resolved() bool { return l.C.Status == "fulfilled" }
func (l *Lazy) Value() interface{} { return l.C.Value }

type Decoder struct {
	State   int
	RowID   int
	Tag     byte
	Length  int
	Buf     []byte
	Chunks  map[int]*Chunk
	Rows    map[int]string
}

func NewDecoder() *Decoder {
	return &Decoder{Chunks: map[int]*Chunk{}, Rows: map[int]string{}}
}

func (d *Decoder) chunk(id int) *Chunk {
	c, ok := d.Chunks[id]
	if !ok {
		c = &Chunk{ID: id, Status: "pending"}
		d.Chunks[id] = c
	}
	return c
}

func hexVal(b byte) int {
	if b > 96 {
		return int(b) - 87
	}
	return int(b) - 48
}

// Write 可以喂任意切分的字节片段，状态机跨调用保持。
func (d *Decoder) Write(data []byte) {
	i, n := 0, len(data)
	for i < n {
		switch d.State {
		case rowID:
			b := data[i]
			i++
			if b == ':' {
				d.State = rowTag
			} else {
				d.RowID = d.RowID<<4 | hexVal(b)
			}
		case rowTag:
			t := data[i]
			if strings.IndexByte(lengthTags, t) >= 0 {
				i++
				d.Tag = t
				d.State = rowLength
			} else if strings.IndexByte(newlineTags, t) >= 0 {
				i++
				d.Tag = t
				d.State = byNewline
			} else {
				// 未知 tag：不消费该字节，它就是数据的一部分
				d.Tag = 0
				d.State = byNewline
			}
		case rowLength:
			b := data[i]
			i++
			if b == ',' {
				d.State = byLength
			} else {
				d.Length = d.Length<<4 | hexVal(b)
			}
		case byLength:
			take := d.Length - len(d.Buf)
			if take > n-i {
				take = n - i
			}
			d.Buf = append(d.Buf, data[i:i+take]...)
			i += take
			if len(d.Buf) == d.Length {
				d.finish()
			}
		case byNewline:
			j := -1
			for k := i; k < n; k++ {
				if data[k] == '\n' {
					j = k
					break
				}
			}
			if j == -1 {
				d.Buf = append(d.Buf, data[i:]...)
				i = n
			} else {
				d.Buf = append(d.Buf, data[i:j]...)
				i = j + 1
				d.finish()
			}
		}
	}
}

func (d *Decoder) finish() {
	raw := string(d.Buf)
	id := d.RowID
	d.Rows[id] = raw
	c := d.chunk(id)
	c.Raw = raw
	var v interface{}
	if err := json.Unmarshal([]byte(raw), &v); err != nil {
		v = raw
	}
	c.Resolve(d.resolve(v))
	d.State = rowID
	d.RowID, d.Tag, d.Length = 0, 0, 0
	d.Buf = d.Buf[:0]
}

func (d *Decoder) resolve(node interface{}) interface{} {
	switch x := node.(type) {
	case string:
		return d.resolveRef(x)
	case []interface{}:
		out := make([]interface{}, len(x))
		for i, e := range x {
			out[i] = d.resolve(e)
		}
		return out
	case map[string]interface{}:
		out := map[string]interface{}{}
		for k, v := range x {
			out[k] = d.resolve(v)
		}
		return out
	}
	return node
}

func (d *Decoder) resolveRef(v string) interface{} {
	if !strings.HasPrefix(v, "$") {
		return v
	}
	if v == "$" {
		return "REACT_ELEMENT_TYPE"
	}
	kind := v[1]
	rest := v[2:]
	switch kind {
	case '$':
		return v[1:] // 转义：源码 value.slice(1)
	case 'L':
		return &Lazy{C: d.chunk(parseHex(rest))}
	case '@':
		return d.chunk(parseHex(rest))
	case 'S':
		return "Symbol(" + rest + ")"
	case 'h':
		return "ServerRef(" + rest + ")"
	case 'I':
		return math.Inf(1)
	case 'N':
		return math.NaN()
	case 'u':
		return "<undefined>"
	case 'n':
		i, _ := strconv.ParseInt(rest, 10, 64)
		return i
	case 'D':
		return rest
	case '-':
		if v == "$-0" {
			return math.Copysign(0, -1)
		}
		return math.Inf(-1)
	}
	return v
}

func parseHex(s string) int {
	v, _ := strconv.ParseInt(s, 16, 32)
	return int(v)
}

// EncodeRow 生成 <hexRowID>:<TAG><hexByteLen>,<payload>
func EncodeRow(id int, tag byte, payload string) string {
	return fmt.Sprintf("%x:%c%x,%s", id, tag, len([]byte(payload)), payload)
}

func main() {
	d := NewDecoder()
	d.Write([]byte(EncodeRow(0, 'T', `{"shell":1,"comments":"$L1"}`)))
	m := d.resolve(d.Chunks[0].Value)
	fmt.Println("shell:", m)
	lz := d.Chunks[0].Value.(map[string]interface{})["comments"].(*Lazy)
	fmt.Println("before row 1 arrives, resolved =", lz.Resolved())
	d.Write([]byte(EncodeRow(1, 'T', `["c1","c2"]`)))
	fmt.Println("after  row 1 arrives, resolved =", lz.Resolved(), lz.Value())
}
