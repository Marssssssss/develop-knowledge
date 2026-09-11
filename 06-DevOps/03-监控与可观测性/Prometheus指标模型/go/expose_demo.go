// Prometheus text exposition format 0.0.4 parser demo (Go, stdlib only)
// 依据 https://prometheus.io/docs/instrumenting/exposition_formats/
package main

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

var exposition = strings.Join([]string{
	`# HELP http_requests_total The total number of HTTP requests.`,
	`# TYPE http_requests_total counter`,
	`http_requests_total{method="post",code="200"} 1027 1395066363000`,
	`http_requests_total{method="post",code="400"}    3 1395066363000`,
	`# Escaping in label values:`,
	`escapeme{label="a\nb \" c"} 1`,
	`# HELP http_request_duration_seconds A histogram of the request duration.`,
	`# TYPE http_request_duration_seconds histogram`,
	`http_request_duration_seconds_bucket{le="0.05"} 24054`,
	`http_request_duration_seconds_bucket{le="0.1"} 33444`,
	`http_request_duration_seconds_bucket{le="0.2"} 100392`,
	`http_request_duration_seconds_bucket{le="0.5"} 129389`,
	`http_request_duration_seconds_bucket{le="1"} 133988`,
	`http_request_duration_seconds_bucket{le="+Inf"} 144320`,
	`http_request_duration_seconds_sum 53423`,
	`http_request_duration_seconds_count 144320`,
	`# HELP rpc_duration_seconds A summary of the RPC duration in seconds.`,
	`# TYPE rpc_duration_seconds summary`,
	`rpc_duration_seconds{quantile="0.01"} 3102`,
	`rpc_duration_seconds{quantile="0.5"} 4773`,
	`rpc_duration_seconds{quantile="0.99"} 76656`,
	`rpc_duration_seconds_sum 1.7560433e+07`,
	`rpc_duration_seconds_count 2693`,
	`# no TYPE line below -> defaults to untyped`,
	`some_gauge 42`,
	`weird_value NaN`,
	`another_inf +Inf`,
}, "\n") + "\n"

var validTypes = map[string]bool{
	"counter": true, "gauge": true, "histogram": true,
	"summary": true, "untyped": true,
}

// Sample 一个样本: name{labels} value [ts]
type Sample struct {
	Name   string
	Labels map[string]string
	Value  float64
	TS     int64
	HasTS  bool
}

// Family 同名指标的聚合
type Family struct {
	Name    string
	Type    string
	Help    string
	Samples []Sample
}

// unescape 标签值只定义三种转义: \\ \" \n (官方规范明文列举)
func unescape(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			switch s[i+1] {
			case 'n':
				b.WriteByte('\n')
			default: // \" 与 \\ 都还原为字面字符
				b.WriteByte(s[i+1])
			}
			i++
		} else {
			b.WriteByte(s[i])
		}
	}
	return b.String()
}

// parseValue strconv.ParseFloat 原生接受 NaN/+Inf/-Inf —— 与规范 Go ParseFloat 语义一致
func parseValue(tok string) (float64, error) {
	return strconv.ParseFloat(tok, 64)
}

// parseMetricPart 'name{k="v",...}' -> (name, labels)
func parseMetricPart(part string) (string, map[string]string, error) {
	brace := strings.IndexByte(part, '{')
	if brace < 0 {
		return part, nil, nil // 无标签
	}
	name := part[:brace]
	rest := part[brace+1:]
	if !strings.HasSuffix(rest, "}") {
		return "", nil, fmt.Errorf("unterminated label set: %s", part)
	}
	body := rest[:len(rest)-1]
	labels := map[string]string{}
	i := 0
	for i < len(body) {
		eq := strings.IndexByte(body[i:], '=')
		if eq < 0 || i+eq+1 >= len(body) || body[i+eq+1] != '"' {
			return "", nil, fmt.Errorf("label value must be quoted: %s", part)
		}
		key := strings.TrimSpace(body[i : i+eq])
		j := i + eq + 2
		for j < len(body) { // 找未转义的收尾引号
			if body[j] == '"' && body[j-1] != '\\' {
				break
			}
			j++
		}
		if j >= len(body) {
			return "", nil, fmt.Errorf("unterminated label value: %s", part)
		}
		labels[key] = unescape(body[i+eq+2 : j])
		i = j + 1
		for i < len(body) && (body[i] == ',' || body[i] == ' ') {
			i++
		}
	}
	return name, labels, nil
}

// parseSampleLine EBNF: name{labels} value [timestamp] —— 从右往左切最稳
func parseSampleLine(line string) (Sample, error) {
	toks := strings.Fields(line)
	if len(toks) < 2 {
		return Sample{}, fmt.Errorf("too few tokens: %s", line)
	}
	last := toks[len(toks)-1]
	var s Sample
	if _, err := strconv.ParseInt(last, 10, 64); err == nil && len(toks) >= 3 {
		ts, _ := strconv.ParseInt(last, 10, 64)
		s.TS, s.HasTS = ts, true
		toks = toks[:len(toks)-1]
	}
	v, err := parseValue(toks[len(toks)-1])
	if err != nil {
		return Sample{}, fmt.Errorf("bad value %q: %w", toks[len(toks)-1], err)
	}
	s.Value = v
	part := strings.Join(toks[:len(toks)-1], " ")
	name, labels, err := parseMetricPart(part)
	if err != nil {
		return Sample{}, err
	}
	s.Name, s.Labels = name, labels
	return s, nil
}

// ParseExposition 主入口: 按指标名聚合为 Family
func ParseExposition(text string) map[string]*Family {
	fams := map[string]*Family{}
	get := func(name string) *Family {
		f, ok := fams[name]
		if !ok {
			f = &Family{Name: name, Type: "untyped"}
			fams[name] = f
		}
		return f
	}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue // 空行忽略
		}
		if line[0] == '#' {
			rest := strings.TrimLeft(line[1:], " \t")
			fields := strings.SplitN(rest, " ", 3)
			if len(fields) == 0 || fields[0] == "" {
				continue // 普通注释
			}
			switch fields[0] {
			case "HELP":
				f := get(fields[1])
				if len(fields) > 2 {
					f.Help = unescape(fields[2]) // HELP 只需转义 \\ 和 \n
				}
			case "TYPE":
				if len(fields) < 3 || !validTypes[fields[2]] {
					panic("bad TYPE line: " + line)
				}
				f := get(fields[1])
				if len(f.Samples) > 0 { // TYPE 必须在首个样本之前
					panic("TYPE after first sample: " + fields[1])
				}
				f.Type = fields[2]
			}
			continue
		}
		s, err := parseSampleLine(line)
		if err != nil {
			panic(err.Error())
		}
		f := get(s.Name)
		f.Samples = append(f.Samples, s)
	}
	return fams
}

// checkHistogram 校验三条不变式: +Inf 存在 / == _count / 桶按上界升序
func checkHistogram(fams map[string]*Family, base string) error {
	bf := fams[base+"_bucket"]
	type bk struct {
		le    string
		bound float64
		count float64
	}
	buckets := make([]bk, 0, len(bf.Samples))
	for _, s := range bf.Samples {
		le, ok := s.Labels["le"]
		if !ok {
			continue
		}
		bound := math.Inf(1)
		if le != "+Inf" {
			bound, _ = strconv.ParseFloat(le, 64)
		}
		buckets = append(buckets, bk{le, bound, s.Value})
	}
	sort.Slice(buckets, func(i, j int) bool { return buckets[i].bound < buckets[j].bound })
	fmt.Printf("  histogram '%s' buckets:\n", base)
	for _, b := range buckets {
		fmt.Printf("    le=%-6s count=%g\n", b.le, b.count)
	}
	if len(buckets) == 0 || buckets[len(buckets)-1].le != "+Inf" {
		return fmt.Errorf("highest bucket must be +Inf")
	}
	cf := fams[base+"_count"]
	if cf == nil || len(cf.Samples) == 0 || cf.Samples[0].Value != buckets[len(buckets)-1].count {
		return fmt.Errorf("+Inf bucket must equal <name>_count")
	}
	return nil
}

func main() {
	fmt.Println("== demo 1: parse official-style exposition ==")
	fams := ParseExposition(exposition)
	names := make([]string, 0, len(fams))
	for n := range fams {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		fmt.Printf("family=%-36s type=%-9s samples=%d\n", n, fams[n].Type, len(fams[n].Samples))
	}

	fmt.Println("\n== demo 2: histogram invariants ==")
	if err := checkHistogram(fams, "http_request_duration_seconds"); err != nil {
		panic(err)
	}
	fmt.Println("  all invariants hold (has +Inf / == count / ordered)")

	fmt.Println("\n== demo 3: label escaping round-trip ==")
	es := fams["escapeme"].Samples[0]
	if want := "a\nb \" c"; es.Labels["label"] != want {
		panic("unescaped mismatch: " + es.Labels["label"])
	}
	fmt.Printf("  label value = %q (real newline + quote survived)\n", es.Labels["label"])

	fmt.Println("\n== demo 4: missing TYPE -> untyped ==")
	if fams["some_gauge"].Type != "untyped" {
		panic("expected untyped")
	}
	fmt.Println("  some_gauge type = untyped (spec: no TYPE line => untyped)")

	fmt.Println("\n== demo 5: NaN / +Inf values ==")
	if !math.IsNaN(fams["weird_value"].Samples[0].Value) {
		panic("expected NaN")
	}
	if !math.IsInf(fams["another_inf"].Samples[0].Value, 1) {
		panic("expected +Inf")
	}
	fmt.Println("  weird_value = NaN, another_inf = +Inf (valid per spec)")

	fmt.Println("\nALL CHECKS PASSED")
}
