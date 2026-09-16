package main

// BinDiff 结构指纹的 Go 实现:prime signature、signature 三元组、MD index。
//
// 依据(与 bindiff_match.py 同一批本轮实读资料):
//   * google/bindiff docs/concepts.md —— signature 三元组(基本块数 / 块间边数 /
//     调用子函数次数)、prime(每条助记符分配唯一小素数,整个函数的素数**相乘**)、
//     「An attribute is unique in both binaries → matched」。
//   * google/bindiff match/graph_util.h(经镜像读取)的 CalculateMdIndexInternal:
//       md_index(edge) = sqrt(w0)*in_deg(src) + sqrt(w1)*out_deg(src)
//                      + sqrt(w2)*in_deg(tgt) + sqrt(w3)*out_deg(tgt)
//                      + sqrt(w4)*level(src) + sqrt(w5)*level(tgt)
//     返回 1.0 / md_index;kDefaultWeightsNode = {2,3,5,7,0,0};
//     顶点值 = 其所有关联边之和,求和前**先排序**(官方注释:Summation is not
//     commutative for doubles)。
//
// 与 Python 版的差异(刻意保留便于对照):uint64 的乘法回绕是 Go 的确定行为,
// 所以 product 口径会**静默溢出** —— 这正是实现改用求和(sum)的动机。

import (
	"fmt"
	"math"
	"sort"
	"strings"
)

// keysOf:Go 的 map 迭代顺序随机,凡会进入输出的遍历一律先排序,保证可复现。
func keysOf[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ---------------------------------------------------------------- prime signature

var primes = []uint64{2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47,
	53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101}

var mnemonicTable = map[string]uint64{}

func mnemonicPrime(mn string) uint64 {
	if p, ok := mnemonicTable[mn]; ok {
		return p
	}
	p := primes[len(mnemonicTable)%len(primes)]
	mnemonicTable[mn] = p
	return p
}

// primeSignature:助记符序列 → 结构不变量,与指令顺序无关(乘法/加法交换律)。
func primeSignature(mnemonics []string, sumMode bool) uint64 {
	var acc uint64
	if !sumMode {
		acc = 1
	}
	for _, mn := range mnemonics {
		p := mnemonicPrime(mn)
		if sumMode {
			acc += p
		} else {
			acc *= p // 溢出被静默回绕,这就是实现改用求和的原因
		}
	}
	return acc
}

// ---------------------------------------------------------------- 函数与三种指纹

type Func struct {
	Insns      map[string][]string
	Blocks     map[string][]string
	Strings    []string
	Calls      map[string]int
	ImmChanged bool
}

// flowgraphHash:CFG 形状 + 块内助记符序列的摘要,不认识名字。
func flowgraphHash(f Func) string {
	var sb strings.Builder
	for _, b := range keysOf(f.Blocks) {
		sb.WriteString(b + ":" + strings.Join(f.Insns[b], ",") + ";")
	}
	for _, b := range keysOf(f.Blocks) {
		succ := append([]string(nil), f.Blocks[b]...)
		sort.Strings(succ)
		sb.WriteString(b + ">" + strings.Join(succ, ",") + ";")
	}
	return sb.String()
}

// byteHash:字节级哈希的模型 —— 精度最高,但只要改一个立即数就整体失配。
func byteHash(f Func) string {
	if f.ImmChanged {
		return flowgraphHash(f) + "|imm+1"
	}
	return flowgraphHash(f)
}

// structuralSignature:官方 signature 三元组。
func structuralSignature(f Func) [3]int {
	edges, calls := 0, 0
	for _, b := range keysOf(f.Blocks) {
		edges += len(f.Blocks[b])
	}
	for _, n := range f.Calls {
		calls += n
	}
	return [3]int{len(f.Blocks), edges, calls}
}

// ---------------------------------------------------------------- MD index

var defaultNodeWeights = []float64{2, 3, 5, 7, 0, 0} // kDefaultWeightsNode
var fullWeights = []float64{2, 3, 5, 7, 11, 13}      // 拓扑层系数非零,作为对照
var proximityWeights = []float64{2, 3, 5, 7}         // relaxed:丢掉拓扑层

// bfsLevels:top-down 从入口点分层,reverse=true 则从出口点(bottom-up)。
func bfsLevels(g map[string][]string, roots []string, reverse bool) map[string]int {
	level := map[string]int{}
	queue := []string{}
	for _, r := range roots {
		if _, ok := level[r]; !ok {
			level[r] = 0
			queue = append(queue, r)
		}
	}
	for len(queue) > 0 {
		n := queue[0]
		queue = queue[1:]
		outs := g[n]
		if reverse {
			outs = nil
			for _, u := range keysOf(g) {
				for _, s := range g[u] {
					if s == n {
						outs = append(outs, u)
					}
				}
			}
		}
		for _, m := range outs {
			if _, ok := level[m]; !ok {
				level[m] = level[n] + 1
				queue = append(queue, m)
			}
		}
	}
	return level
}

func inDeg(g map[string][]string, x string) float64 {
	c := 0
	for _, succ := range g {
		for _, t := range succ {
			if t == x {
				c++
			}
		}
	}
	return float64(c)
}

// mdIndexEdge:官方公式逐项实现,权重取平方根,最后取倒数。
func mdIndexEdge(g map[string][]string, u, v string, level map[string]int, w []float64) float64 {
	if len(w) == 4 {
		w = append(append([]float64(nil), w...), 0, 0)
	}
	total := math.Sqrt(w[0])*inDeg(g, u) + math.Sqrt(w[1])*float64(len(g[u])) +
		math.Sqrt(w[2])*inDeg(g, v) + math.Sqrt(w[3])*float64(len(g[v])) +
		math.Sqrt(w[4])*float64(level[u]) + math.Sqrt(w[5])*float64(level[v])
	if total == 0 {
		return 0
	}
	return 1.0 / total
}

// mdIndexNode:顶点值 = 所有入边与出边之和,求和前先排序。
func mdIndexNode(g map[string][]string, v string, inverted bool, w []float64, roots []string) float64 {
	if roots == nil {
		roots = []string{keysOf(g)[0]}
	}
	level := bfsLevels(g, roots, inverted)
	vals := []float64{}
	for _, p := range keysOf(g) {
		for _, s := range g[p] {
			if s == v {
				vals = append(vals, mdIndexEdge(g, p, v, level, w))
			}
		}
	}
	for _, m := range g[v] {
		vals = append(vals, mdIndexEdge(g, v, m, level, w))
	}
	sort.Float64s(vals) // 官方注释:Summation is not commutative for doubles
	sum := 0.0
	for _, x := range vals {
		sum += x
	}
	return sum
}

// mdOfFunctions:对整张图(调用图或 flow graph)算一遍顶点 MD index。
func mdOfFunctions(cg map[string][]string, inverted bool, w []float64) map[string]float64 {
	roots := []string{}
	for _, n := range keysOf(cg) {
		if inDeg(cg, n) == 0 {
			roots = append(roots, n)
		}
	}
	out := map[string]float64{}
	for _, n := range keysOf(cg) {
		out[n] = mdIndexNode(cg, n, inverted, w, roots)
	}
	return out
}

func fmtFloat(x float64) string { return fmt.Sprintf("%.6f", x) }
