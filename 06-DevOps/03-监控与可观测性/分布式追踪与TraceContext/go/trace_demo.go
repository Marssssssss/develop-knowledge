// 分布式追踪与 W3C Trace Context —— demo (Go, stdlib only)
// 依据 https://www.w3.org/TR/2020/REC-trace-context-1-20200206
package main

import (
	"fmt"
	"math/rand"
	"strings"
)

const (
	hexDigits   = "0123456789abcdef"
	traceIDLen  = 32 // 16 字节
	spanIDLen   = 16 // 8 字节
	sampledFlag = 0x01
)

// TraceParent traceparent 头的四字段
type TraceParent struct {
	Version  string
	TraceID  string
	ParentID string
	Flags    int
}

// Span 一个操作单元（OTel: name/trace_id/span_id/parent_id/...）
type Span struct {
	TraceID  string
	SpanID   string
	ParentID string // 空串 = root
	Name     string
}

// isLowerHex HEXDIGLC：仅小写
func isLowerHex(s string) bool {
	for _, c := range s {
		if !strings.ContainsRune(hexDigits, c) {
			return false
		}
	}
	return true
}

// ParseTraceparent 校验 version-32hex-16hex-2hex；全零/ff/大写均非法（MUST ignore）
func ParseTraceparent(tp string) (*TraceParent, error) {
	parts := strings.Split(tp, "-")
	if len(parts) != 4 {
		return nil, fmt.Errorf("expected 4 fields, got %d", len(parts))
	}
	if len(parts[0]) != 2 || len(parts[1]) != traceIDLen ||
		len(parts[2]) != spanIDLen || len(parts[3]) != 2 {
		return nil, fmt.Errorf("field length mismatch")
	}
	for _, p := range parts {
		if !isLowerHex(p) {
			return nil, fmt.Errorf("non-lowercase-hex characters")
		}
	}
	if parts[0] == "ff" {
		return nil, fmt.Errorf("version ff is forbidden")
	}
	if parts[1] == strings.Repeat("0", traceIDLen) || parts[2] == strings.Repeat("0", spanIDLen) {
		return nil, fmt.Errorf("all-zero trace-id/parent-id is invalid")
	}
	var flags int
	fmt.Sscanf(parts[3], "%x", &flags)
	return &TraceParent{parts[0], parts[1], parts[2], flags}, nil
}

func (tp *TraceParent) Serialize() string {
	return fmt.Sprintf("%s-%s-%s-%02x", tp.Version, tp.TraceID, tp.ParentID, tp.Flags)
}

// isSampled 位掩码；禁止 flags == 1 判等
func isSampled(flags int) bool { return flags&sampledFlag == sampledFlag }

var rng = rand.New(rand.NewSource(42)) // 固定种子，输出可复现

// newID 规范建议至少右 7 字节随机；demo 用全随机并保证非全零
func newID(nhex int) string {
	for {
		b := make([]byte, nhex)
		for i := range b {
			b[i] = hexDigits[rng.Intn(16)]
		}
		s := string(b)
		if s != strings.Repeat("0", nhex) {
			return s
		}
	}
}

// startService 一个服务节点：提取 traceparent -> 校验 -> 开 span -> 注出给下游
func startService(name string, incoming string, spans *[]Span) Span {
	s := Span{Name: name}
	var tp TraceParent
	if incoming == "" {
		// 无上游上下文：作为根，新建 trace
		s.TraceID, s.ParentID = newID(traceIDLen), ""
		tp = TraceParent{"00", s.TraceID, "", 0x01}
	} else if parsed, err := ParseTraceparent(incoming); err != nil {
		// 规范：非法 traceparent MUST ignore —— 当作无上游，另起新 trace
		fmt.Printf("    [%s] invalid traceparent ignored, starting new trace\n", name)
		s.TraceID, s.ParentID = newID(traceIDLen), ""
		tp = TraceParent{"00", s.TraceID, "", 0x01}
	} else {
		tp = *parsed
		s.TraceID, s.ParentID = tp.TraceID, tp.ParentID
	}
	s.SpanID = newID(spanIDLen) // 每跳生成新 span_id
	*spans = append(*spans, s)

	next := tp
	next.ParentID = s.SpanID // parent-id 换成自己的 span_id
	fmt.Printf("    %s: outgoing traceparent = %s\n", name, next.Serialize())
	return s
}

// renderTree 按 parent_id 关系把 Span 列表还原成树（trace = span 的 DAG）
func renderTree(spans []Span) string {
	byParent := map[string][]Span{}
	for _, s := range spans {
		byParent[s.ParentID] = append(byParent[s.ParentID], s)
	}
	var lines []string
	var walk func(Span, int)
	walk = func(s Span, depth int) {
		tag := ""
		if s.ParentID == "" {
			tag = " root"
		} else {
			tag = " parent=" + s.ParentID[:8] + "…"
		}
		lines = append(lines, strings.Repeat("  ", depth)+"└─ "+s.Name+
			" (span="+s.SpanID[:8]+"…"+tag+")")
		for _, c := range byParent[s.SpanID] {
			walk(c, depth+1)
		}
	}
	roots := byParent[""]
	if len(roots) != 1 {
		panic("expected exactly one root span")
	}
	walk(roots[0], 0)
	return strings.Join(lines, "\n")
}

func main() {
	fmt.Println("== demo 1: client -> A -> B -> C propagation ==")
	var spans []Span
	tp := ""
	for _, svc := range []string{"svc-A", "svc-B", "svc-C"} {
		last := startService(svc, tp, &spans)
		tp = "00-" + last.TraceID + "-" + last.SpanID + "-01"
	}
	fmt.Println("  span tree reconstructed from parent_id links:")
	fmt.Println(renderTree(spans))
	for i := 1; i < len(spans); i++ {
		if spans[i].TraceID != spans[0].TraceID {
			panic("trace_id must be constant across hops")
		}
	}
	fmt.Println("  trace_id constant across all hops: OK")

	fmt.Println("\n== demo 2: validation rules (MUST ignore cases) ==")
	badCases := []struct {
		tp, why string
	}{
		{"00-" + strings.Repeat("0", 32) + "-abcdef0123456789-01", "all-zero trace-id"},
		{"00-4bf92f3577b34da6a3ce929d0e0e4736-" + strings.Repeat("0", 16) + "-01", "all-zero parent-id"},
		{"ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "version ff"},
		{"00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01", "uppercase hex"},
		{"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7", "missing flags"},
	}
	for _, c := range badCases {
		if _, err := ParseTraceparent(c.tp); err == nil {
			panic("should be rejected: " + c.why)
		}
		fmt.Printf("    rejected: %s\n", c.why)
	}
	if _, err := ParseTraceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"); err != nil {
		panic(err)
	}
	fmt.Println("    accepted: spec example 00-4bf92f35…-00f067aa…-01")

	fmt.Println("\n== demo 3: trace-flags bit masking ==")
	if !isSampled(0x01) || !isSampled(0x03) || isSampled(0x00) || isSampled(0x02) {
		panic("flag masking wrong")
	}
	fmt.Println("    flags 0x01/0x03 sampled=True, 0x00/0x02 sampled=False (mask, not equality)")

	fmt.Println("\n== demo 4: invalid upstream starts a fresh trace ==")
	var spans2 []Span
	root := startService("svc-X",
		"00-"+strings.Repeat("0", 32)+"-abcdef0123456789-01", &spans2)
	if root.ParentID != "" {
		panic("fresh root expected after ignored traceparent")
	}
	fmt.Printf("    svc-X became root with new trace %s…\n", root.TraceID[:8])

	fmt.Println("\nALL CHECKS PASSED")
}
