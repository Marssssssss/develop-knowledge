// 信息熵字段边界检测：纵向列熵(主) + 横向滑窗熵(辅) + 序列启发式。
// 用法: go run entropy_fields.go   (自检失败即 panic)
package main

import (
	"fmt"
	"math"
	"math/rand"
	"sort"
)

const gapSentinel = 256 // 报文越界哨兵(与真实字节 0..255 区分)

// ---------- 香农熵 ----------
func shannon(xs []int) float64 {
	if len(xs) == 0 {
		return 0
	}
	sorted := append([]int(nil), xs...)
	sort.Ints(sorted)
	h, n := 0.0, len(sorted)
	i := 0
	for i < n {
		j := i
		for j < n && sorted[j] == sorted[i] {
			j++
		}
		p := float64(j-i) / float64(n)
		h -= p * math.Log2(p)
		i = j
	}
	return h
}

// ---------- 纵向: 跨报文按列熵 ----------
func colValues(msgs [][]byte, k int) []int {
	v := make([]int, len(msgs))
	for i, m := range msgs {
		if k < len(m) {
			v[i] = int(m[k])
		} else {
			v[i] = gapSentinel
		}
	}
	return v
}

func colEntropies(msgs [][]byte) []float64 {
	L := 0
	for _, m := range msgs {
		if len(m) > L {
			L = len(m)
		}
	}
	H := make([]float64, L)
	for k := range H {
		H[k] = shannon(colValues(msgs, k))
	}
	return H
}

func classifyColumns(msgs [][]byte, thresh float64) (H []float64, kinds []string, bounds []int) {
	H = colEntropies(msgs)
	for k, h := range H {
		switch {
		case h == 0:
			kinds = append(kinds, "const")
		case h < 2.0:
			kinds = append(kinds, "low")
		default:
			kinds = append(kinds, "high")
		}
		if k > 0 && math.Abs(H[k]-H[k-1]) > thresh {
			bounds = append(bounds, k)
		}
	}
	return
}

// ---------- 横向: 长流滑窗熵 ----------
func entropyBoundariesStream(msg []byte, w int, thresh float64) []int {
	var out []int
	for i := w; i+w <= len(msg); i++ {
		l := shannon(ints(msg[i-w : i]))
		r := shannon(ints(msg[i : i+w]))
		if math.Abs(r-l) > thresh {
			out = append(out, i)
		}
	}
	return out
}

func ints(bs []byte) []int {
	v := make([]int, len(bs))
	for i, b := range bs {
		v[i] = int(b)
	}
	return v
}

// ---------- 序列启发式 ----------
func monotonicProb(msgs [][]byte, k int) float64 {
	var vs []int
	for _, m := range msgs {
		if k < len(m) {
			vs = append(vs, int(m[k]))
		}
	}
	if len(vs) < 3 {
		return 0
	}
	inc := 0
	for i := 0; i+1 < len(vs); i++ {
		if vs[i+1] > vs[i] {
			inc++
		}
	}
	return float64(inc) / float64(len(vs)-1)
}

// ---------- 字段段汇总 ----------
type seg struct {
	kind        string
	start, end int
}

func segments(kinds []string) []seg {
	var segs []seg
	start := 0
	for k := 1; k <= len(kinds); k++ {
		if k == len(kinds) || kinds[k] != kinds[k-1] {
			segs = append(segs, seg{kinds[start], start, k - 1})
			start = k
		}
	}
	return segs
}

// ---------- 自检 ----------
func selfTest() {
	if shannon(ints([]byte("AAAA"))) != 0 {
		panic("const entropy != 0")
	}
	if math.Abs(shannon(ints([]byte("AB")))-1.0) > 1e-9 {
		panic("AB entropy != 1")
	}
	if math.Abs(shannon(ints([]byte("AABBCCDD")))-2.0) > 1e-9 {
		panic("4-symbol entropy != 2")
	}
	inc := make([][]byte, 8)
	dec := make([][]byte, 8)
	for i := 0; i < 8; i++ {
		inc[i] = []byte{byte(i + 1), 'x', 'x', 'x'}
		dec[i] = []byte{byte(8 - i), 'x', 'x', 'x'}
	}
	if monotonicProb(inc, 0) != 1.0 || monotonicProb(dec, 0) != 0.0 {
		panic("monotonic heuristic failed")
	}
}

// ---------- 演示 ----------
func mkMsg(rng *rand.Rand, i int, t byte, bodyLen int) []byte {
	seq := i + 1
	m := []byte("PROTO")
	m = append(m, 0x02, t, byte(seq&0xff), byte(seq>>8&0xff))
	for j := 0; j < 4; j++ { // nonce
		m = append(m, byte(rng.Intn(256)))
	}
	m = append(m, byte(bodyLen))
	for j := 0; j < bodyLen; j++ { // 半随机可打印 body
		m = append(m, byte(0x20+rng.Intn(0x5f)))
	}
	m = append(m, byte(rng.Intn(256)), byte(rng.Intn(256))) // crc
	return m
}

func main() {
	selfTest()
	rng := rand.New(rand.NewSource(7))
	var msgs [][]byte
	for i := 0; i < 24; i++ {
		msgs = append(msgs, mkMsg(rng, i, byte(1+i%3), 6+i%4))
	}
	H, kinds, bounds := classifyColumns(msgs, 1.0)
	fmt.Println("== 纵向列熵 (N=24) ==")
	for k, h := range H {
		fmt.Printf("  col %2d  H=%4.1f  %s\n", k, h, kinds[k])
	}
	fmt.Println("  熵差>1bit 边界 @", bounds)
	fmt.Println()
	fmt.Println("== 序列启发式 ==")
	for k := range H {
		if p := monotonicProb(msgs, k); p >= 0.9 {
			fmt.Printf("  col %d: P(monotonic)=%.2f → 计数器/序列号候选\n", k, p)
		}
	}
	fmt.Println()
	fmt.Println("== 横向滑窗熵边界 (明文头+随机体, w=64) ==")
	hdr := []byte("GET /index.html HTTP/1.1\r\nHost: example.com\r\nUser-Agent: demo-agent/1.0\r\n")
	hdr = append(hdr, hdr...)
	body := make([]byte, 192)
	for i := range body {
		body[i] = byte(rng.Intn(256))
	}
	hits := entropyBoundariesStream(append(hdr, body...), 64, 1.0)
	fmt.Printf("  流长 %d (明文头 %d + 随机体 192), 命中 %v… (精度 ±32)\n",
		len(hdr)+len(body), len(hdr), firstN(hits, 6))
	fmt.Println()
	fmt.Println("== 字段段汇总 ==")
	for _, s := range segments(kinds) {
		fmt.Printf("  %-5s [%2d..%2d] len=%d\n", s.kind, s.start, s.end, s.end-s.start+1)
	}
	fmt.Println("  (尾部列被 GAP 占据 → 熵被压低, 变长字段位移污染, 见 README 坑1)")
}

func firstN(xs []int, n int) []int {
	if len(xs) <= n {
		return xs
	}
	return xs[:n]
}
