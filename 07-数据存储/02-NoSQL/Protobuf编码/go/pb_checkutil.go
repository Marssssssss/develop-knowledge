// pb_checkutil.go — 自检工具:断言计数与构造助手(与 pb_check.go 同属 package main)
//
// 拆文件只为满足单文件 ≤300 行的约束;Go 同包共享符号,零语义改动。
package main

import (
	"fmt"
	"strings"
)

var pass, fail int

func check(label string, cond bool, detail ...string) {
	if cond {
		pass++
		fmt.Println("  PASS ", label)
		return
	}
	fail++
	extra := ""
	if len(detail) > 0 {
		extra = detail[0]
	}
	fmt.Println("  FAIL ", label, extra)
}

func raises(fn func() error) bool { return fn() != nil }

func mustRecord(number, wire int, payload []byte) []byte {
	b, err := EncodeRecord(number, wire, payload)
	if err != nil {
		panic(err)
	}
	return b
}

func mustEncode(defn *MessageDef, values map[string]interface{}, order []string) []byte {
	b, err := EncodeMessage(defn, values, order)
	if err != nil {
		panic(err)
	}
	return b
}

func mustScalar(fd *FieldDef, v interface{}) []byte {
	b, err := EncodeScalar(fd, v)
	if err != nil {
		panic(err)
	}
	return b
}

func mustDecode(defn *MessageDef, data []byte) map[string]interface{} {
	m, err := DecodeMessage(defn, data)
	if err != nil {
		panic(err)
	}
	return m
}

func hex(b []byte) string {
	parts := make([]string, len(b))
	for i, x := range b {
		parts[i] = fmt.Sprintf("%02x", x)
	}
	return strings.Join(parts, "")
}

func ints(vs ...int64) []interface{} {
	out := make([]interface{}, len(vs))
	for i, v := range vs {
		out[i] = v
	}
	return out
}
