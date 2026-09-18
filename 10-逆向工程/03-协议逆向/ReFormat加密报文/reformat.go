// ReFormat（ESORICS 2009）最小复现：相位划分 + 数据生命周期 → 定位明文缓冲区。
package main

import "fmt"

// Table 1（论文 §3.1 实测）：名字 / 算术与位运算指令数 / 指令总数。
type row struct {
	name string
	ab   int
	tot  int
}

var table1 = []row{
	{"DES", 68921, 69112}, {"CAST", 18917, 21225}, {"RC4", 2709, 3042},
	{"AES", 6892, 8475},
	{"HTTP request", 429, 3227}, {"FTP port", 421, 5898}, {"DNS response", 223, 1687},
	{"RPC bind", 186, 2342}, {"JPEG", 1112, 12898}, {"BMP", 229, 956},
}

var decryptNames = map[string]bool{"DES": true, "CAST": true, "RC4": true, "AES": true}

// threshold §3.3：取 50%（正文脚注：25%~80% 之间的任何值效果相同）。
const threshold = 50.0

func pct(ab, tot int) float64 {
	if tot == 0 {
		return 0
	}
	return float64(ab) * 100.0 / float64(tot)
}

// Inst 指令流中的一条：所属函数 / 运行期栈帧 id / 是否为算术或位运算指令。
type Inst struct {
	Func string
	Sid  int
	AB   bool
}

// cumulativePct §3.3 Step I：第 n 条指令处的累计百分比。
func cumulativePct(tr []Inst) []float64 {
	out, cnt := make([]float64, 0, len(tr)), 0
	for i, r := range tr {
		if r.AB {
			cnt++
		}
		out = append(out, pct(cnt, i+1))
	}
	return out
}

// Frag 一个 function fragment：连续、同一函数、同一运行期栈帧。
type Frag struct {
	Start, End int
	Func       string
	Sid        int
	Pct        float64
}

func functionFragments(tr []Inst) []Frag {
	var out []Frag
	for i, r := range tr {
		if len(out) > 0 && out[len(out)-1].Func == r.Func && out[len(out)-1].Sid == r.Sid {
			out[len(out)-1].End = i + 1
		} else {
			out = append(out, Frag{Start: i, End: i + 1, Func: r.Func, Sid: r.Sid})
		}
	}
	for i := range out {
		ab := 0
		for k := out[i].Start; k < out[i].End; k++ {
			if tr[k].AB {
				ab++
			}
		}
		out[i].Pct = pct(ab, out[i].End-out[i].Start)
	}
	return out
}

func argMax(v []float64) int {
	b := 0
	for i := range v {
		if v[i] > v[b] {
			b = i
		}
	}
	return b
}

func argMin(v []float64) int {
	b := 0
	for i := range v {
		if v[i] < v[b] {
			b = i
		}
	}
	return b
}

// phaseProfile §3.3：两步定位跃迁片段（跃迁点 = 该片段最后一条指令）。
func phaseProfile(tr []Inst, th float64) (*Frag, int, int) {
	cum := cumulativePct(tr)
	imax, imin := argMax(cum), argMin(cum)
	lo, hi := imax, imin
	if imin < lo {
		lo = imin
	}
	if imax > hi {
		hi = imax
	}
	var trans *Frag
	for _, f := range functionFragments(tr) {
		if f.End <= lo || f.Start > hi {
			continue
		}
		if f.Pct >= th {
			cp := f
			trans = &cp // 取最后一个超过阈值的片段
		}
	}
	return trans, imax, imin
}

// Op 内存操作：指令序号 / 操作 / 缓冲区标识。
type Op struct {
	Idx int
	Op  string // alloc | write | read | free
	Buf string
}

// dataLifetime §3.4：write set ∩ read set，结果按首次读取时序排序。
func dataLifetime(ops []Op, transition int) ([]string, []string, []string) {
	live := map[string]bool{}
	written := map[string]bool{}
	for _, o := range ops {
		if o.Idx > transition {
			break
		}
		switch o.Op {
		case "alloc":
			live[o.Buf] = true
		case "free":
			live[o.Buf] = false
		case "write":
			written[o.Buf] = true
		}
	}
	writeSet := []string{}
	for b := range written {
		if live[b] {
			writeSet = append(writeSet, b)
		}
	}
	readSet := map[string]bool{}
	order := []string{}
	for _, o := range ops {
		if o.Idx <= transition {
			continue
		}
		switch o.Op {
		case "alloc":
			live[o.Buf] = true
		case "free":
			live[o.Buf] = false
		case "read", "write":
			if o.Op == "read" && live[o.Buf] && !readSet[o.Buf] {
				readSet[o.Buf] = true
				order = append(order, o.Buf)
			}
			live[o.Buf] = false // 处理阶段：一旦被访问即失效
		}
	}
	ans := []string{}
	for _, b := range order {
		if written[b] {
			ans = append(ans, b)
		}
	}
	return sorted(writeSet), keys(readSet), ans
}

func sorted(s []string) []string {
	out := append([]string{}, s...)
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

func keys(m map[string]bool) []string {
	out := []string{}
	for k := range m {
		out = append(out, k)
	}
	return sorted(out)
}

func fmtList(s []string) string { return fmt.Sprint(s) }
