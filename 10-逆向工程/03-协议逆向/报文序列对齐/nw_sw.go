// 报文序列对齐：Needleman-Wunsch 全局对齐 + Smith-Waterman 局部对齐 + consensus 字段切分。
// 用法: go run nw_sw.go   (自检失败即 panic)
package main

import (
	"bytes"
	"fmt"
)

const (
	mScore = 3
	sScore = -1
	dScore = -4
)

type alignResult struct {
	score int
	a     []int // 字节值或 -1 表示 gap
	b     []int
}

// ---------- NW 全局对齐 ----------
func nwAlign(a, b []byte) alignResult {
	la, lb := len(a), len(b)
	F := make([][]int, la+1)
	P := make([][]byte, la+1)
	for i := range F {
		F[i] = make([]int, lb+1)
		P[i] = make([]byte, lb+1)
	}
	for i := 1; i <= la; i++ {
		F[i][0] = F[i-1][0] + dScore
		P[i][0] = 'U'
	}
	for j := 1; j <= lb; j++ {
		F[0][j] = F[0][j-1] + dScore
		P[0][j] = 'L'
	}
	for i := 1; i <= la; i++ {
		for j := 1; j <= lb; j++ {
			sc := sScore
			if a[i-1] == b[j-1] {
				sc = mScore
			}
			best, ptr := F[i-1][j-1]+sc, byte('D')
			if v := F[i-1][j] + dScore; v > best {
				best, ptr = v, 'U'
			}
			if v := F[i][j-1] + dScore; v > best {
				best, ptr = v, 'L'
			}
			F[i][j], P[i][j] = best, ptr
		}
	}
	// 回溯 (优先级 D > U > L, 可复现)
	i, j := la, lb
	var ra, rb []int
	for i > 0 || j > 0 {
		switch P[i][j] {
		case 'D':
			ra = append(ra, int(a[i-1])); rb = append(rb, int(b[j-1])); i--; j--
		case 'U':
			ra = append(ra, int(a[i-1])); rb = append(rb, -1); i--
		default:
			ra = append(ra, -1); rb = append(rb, int(b[j-1])); j--
		}
	}
	reverse(ra); reverse(rb)
	return alignResult{F[la][lb], ra, rb}
}

// ---------- SW 局部对齐 ----------
func swAlign(a, b []byte) alignResult {
	la, lb := len(a), len(b)
	F := make([][]int, la+1)
	P := make([][]byte, la+1)
	for i := range F {
		F[i] = make([]int, lb+1)
		P[i] = make([]byte, lb+1)
	}
	bi, bj, bs := 0, 0, 0
	for i := 1; i <= la; i++ {
		for j := 1; j <= lb; j++ {
			best, ptr := 0, byte(0)
			sc := sScore
			if a[i-1] == b[j-1] {
				sc = mScore
			}
			if v := F[i-1][j-1] + sc; v > best {
				best, ptr = v, 'D'
			}
			if v := F[i-1][j] + dScore; v > best {
				best, ptr = v, 'U'
			}
			if v := F[i][j-1] + dScore; v > best {
				best, ptr = v, 'L'
			}
			F[i][j], P[i][j] = best, ptr
			if best > bs {
				bs, bi, bj = best, i, j
			}
		}
	}
	i, j := bi, bj
	var ra, rb []int
	for i > 0 && j > 0 && F[i][j] > 0 {
		switch P[i][j] {
		case 'D':
			ra = append(ra, int(a[i-1])); rb = append(rb, int(b[j-1])); i--; j--
		case 'U':
			ra = append(ra, int(a[i-1])); rb = append(rb, -1); i--
		default:
			ra = append(ra, -1); rb = append(rb, int(b[j-1])); j--
		}
	}
	reverse(ra); reverse(rb)
	return alignResult{bs, ra, rb}
}

// ---------- consensus 字段切分 ----------
type seg struct {
	kind        string
	start, end int
}

func consensusSegments(msgs [][]byte) []seg {
	ref := msgs[0]
	base := make([]map[int]bool, len(ref))
	for k := range base {
		base[k] = map[int]bool{int(ref[k]): true}
	}
	inserts := make([]map[int]bool, len(ref)+1)
	for k := range inserts {
		inserts[k] = map[int]bool{}
	}
	for _, msg := range msgs[1:] {
		r := nwAlign(ref, msg)
		k := 0
		for idx, ca := range r.a {
			cb := r.b[idx]
			if ca < 0 { // 插入列
				inserts[k][cb] = true
			} else {
				base[k][cb] = true // -1(gap) 也是取值
				k++
			}
		}
	}
	var cols []map[int]bool
	for k := 0; k <= len(ref); k++ {
		if len(inserts[k]) > 0 {
			cols = append(cols, inserts[k])
		}
		if k < len(ref) {
			cols = append(cols, base[k])
		}
	}
	var segs []seg
	start, kind := 0, ""
	close := func(end int) {
		if kind != "" {
			segs = append(segs, seg{kind, start, end})
		}
	}
	for k, col := range cols {
		kc := "var"
		if len(col) == 1 && !col[-1] {
			kc = "const"
		}
		if kind == "" {
			kind, start = kc, k
		} else if kc != kind {
			close(k - 1)
			kind, start = kc, k
		}
	}
	close(len(cols) - 1)
	return segs
}

func reverse(xs []int) {
	for i, j := 0, len(xs)-1; i < j; i, j = i+1, j-1 {
		xs[i], xs[j] = xs[j], xs[i]
	}
}

func mkMsg(t byte, payload string) []byte {
	l := len(payload)
	return append(append([]byte{'Z', 'M', 0x01, t, byte(l), 0x00}, []byte(payload)...), 0x12, 0x34)
}

func main() {
	// 自检
	r := nwAlign([]byte("ABCD"), []byte("ABC"))
	if r.score != 3*mScore+dScore || len(r.a) != 4 || r.b[3] != -1 {
		panic("nw self-test failed")
	}
	r2 := swAlign([]byte("XXABCDYY"), []byte("QQABCDZZ"))
	if r2.score != 4*mScore {
		panic("sw self-test failed")
	}
	if s := swAlign([]byte("hello"), []byte("shell")).score; s != 4*mScore {
		panic("sw self-test2 failed")
	}

	msgs := [][]byte{
		mkMsg(0x10, "PING"), mkMsg(0x10, "HELLO-WORLD"),
		mkMsg(0x11, "AUTH"), mkMsg(0x10, "X"), mkMsg(0x11, "READ-ME-PLEASE-OK"),
	}
	fmt.Println("== 全局对齐示例 ==")
	g := nwAlign(msgs[0], msgs[1])
	fmt.Println(" a:", render(g.a))
	fmt.Println(" b:", render(g.b))
	fmt.Println(" score =", g.score)
	fmt.Println()
	fmt.Println("== 局部对齐示例 (只保留最高分公共片段) ==")
	l := swAlign(msgs[0], msgs[4])
	fmt.Println(" a:", render(l.a))
	fmt.Println(" b:", render(l.b))
	fmt.Println(" score =", l.score)
	fmt.Println()
	fmt.Println("== 字段段切分假设 ==")
	for _, s := range consensusSegments(msgs) {
		fmt.Printf("  %-5s [%2d..%2d] len=%d\n", s.kind, s.start, s.end, s.end-s.start+1)
	}
}

func render(xs []int) string {
	var buf bytes.Buffer
	for _, x := range xs {
		switch {
		case x < 0:
			buf.WriteByte('-')
		case x >= 32 && x < 127:
			buf.WriteByte(byte(x))
		default:
			buf.WriteByte('.')
		}
	}
	return buf.String()
}
