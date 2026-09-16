package main

// BinDiff 匹配流水线与置信度/相似度的 Go 同题实现(指纹部分见 bindiff_fp.go)。
//
// 依据(本轮实读):
//   * 官方 matching 策略:签名在**两侧都唯一**才建立匹配;多处出现 → 歧义,
//     留给下一个算法;匹配后再用 call graph 的 parents/children 缩小候选集(drill down)。
//   * 算法按 match quality 从高到低施加,高置信算法产生的 fixed point 是后续算法的输入。
//   * confidence「不是所有算法置信度的简单平均」:少数弱匹配不该把整体拖太低,
//     少数强匹配也不该把整体救起来 —— 这个两端饱和的形状用归一化 S 型实现。
//   * function similarity 权重:边 25% / 块 15% / 指令 10% / flow graph MD 差异 50%;
//     binary similarity 权重:边 35% / 块 25% / 函数 10% / 指令 10% / call graph MD 差异 20%;
//     两者最后都再乘 confidence。binary similarity 只统计**非库函数**。

import (
	"fmt"
	"math"
	"sort"
	"strings"
)

var failed []string

func check(cond bool, label string, detail interface{}) {
	tag := "PASS"
	if !cond {
		tag = "FAIL"
		failed = append(failed, label)
	}
	if detail != nil {
		fmt.Printf("  [%s] %s  <- %v\n", tag, label, detail)
	} else {
		fmt.Printf("  [%s] %s\n", tag, label)
	}
}

// ---------------------------------------------------------------- 匹配流水线

type Match struct {
	Target string
	Algo   string
	Conf   float64
}

type Algo struct {
	Name string
	Conf float64
	Sig  func(map[string]Func, string) (string, bool) // ok=false → 该函数不参与这一档
}

// stagedMatch:按置信度从高到低施加指纹;只接受两侧候选集中都唯一的签名。
func stagedMatch(A, B map[string]Func, algos []Algo, lib map[string]bool) map[string]Match {
	matched := map[string]Match{}
	for _, al := range algos {
		usedB := map[string]bool{}
		for _, m := range matched {
			usedB[m.Target] = true
		}
		ga, gb := map[string][]string{}, map[string][]string{}
		for _, f := range keysOf(A) {
			if _, ok := matched[f]; ok || lib[f] {
				continue
			}
			if v, ok := al.Sig(A, f); ok {
				ga[v] = append(ga[v], f)
			}
		}
		for _, f := range keysOf(B) {
			if usedB[f] || lib[f] {
				continue
			}
			if v, ok := al.Sig(B, f); ok {
				gb[v] = append(gb[v], f)
			}
		}
		for _, v := range keysOf(ga) {
			if len(ga[v]) == 1 && len(gb[v]) == 1 { // 两侧都唯一才接受
				matched[ga[v][0]] = Match{Target: gb[v][0], Algo: al.Name, Conf: al.Conf}
			}
		}
	}
	return matched
}

// propagate:drill down —— 用「已匹配函数的唯一未匹配 callee」继续补匹配。
func propagate(A, B map[string]Func, matched map[string]Match, lib map[string]bool) []string {
	added := []string{}
	usedB := map[string]bool{}
	for _, m := range matched {
		usedB[m.Target] = true
	}
	for _, a := range keysOf(matched) {
		b := matched[a].Target
		kidsA, kidsB := []string{}, []string{}
		for _, k := range keysOf(A[a].Calls) {
			if _, ok := matched[k]; !ok && !lib[k] {
				kidsA = append(kidsA, k)
			}
		}
		for _, k := range keysOf(B[b].Calls) {
			if !usedB[k] && !lib[k] {
				kidsB = append(kidsB, k)
			}
		}
		if len(kidsA) == 1 && len(kidsB) == 1 {
			matched[kidsA[0]] = Match{Target: kidsB[0], Algo: "drilldown", Conf: matched[a].Conf}
			added = append(added, kidsA[0]+"→"+kidsB[0])
		}
	}
	return added
}

// ---------------------------------------------------------------- 样本与断言

func main() {
	A, B := makeBinaries()
	algos := []Algo{
		{"hash", 0.98, func(D map[string]Func, f string) (string, bool) { return byteHash(D[f]), true }},
		{"prime_signature", 0.80, func(D map[string]Func, f string) (string, bool) {
			return fmt.Sprint(primeSignature(D[f].Insns["b0"], false)), true
		}},
		{"string_references", 0.60, func(D map[string]Func, f string) (string, bool) {
			if len(D[f].Strings) == 0 {
				return "", false // 不引用字符串的函数**不参与**这一档
			}
			s := append([]string(nil), D[f].Strings...)
			sort.Strings(s)
			return strings.Join(s, "|"), true
		}},
	}

	fmt.Println("== 1. 结构签名与 prime signature ==")
	check(structuralSignature(A["parse"]) == [3]int{3, 3, 1},
		"parse 签名 =(基本块 3, 边 3, 调用 1)", structuralSignature(A["parse"]))
	check(structuralSignature(A["helperAdd"]) == [3]int{1, 0, 0}, "helperAdd 无出边无调用", nil)
	check(byteHash(A["check"]) != byteHash(B["check"]), "改一个立即数 → 字节级哈希失配", nil)
	check(flowgraphHash(A["check"]) == flowgraphHash(B["check"]),
		"同一改动下 CFG/助记符摘要不变 —— prime 能补位的前提", nil)
	check(primeSignature(A["check"].Insns["b0"], false) == primeSignature(B["check"].Insns["b0"], false),
		"助记符序列不变 → prime 相同", nil)
	check(primeSignature([]string{"a", "b"}, false) == primeSignature([]string{"b", "a"}, false),
		"prime 与指令顺序无关(乘法交换律)", nil)
	// 溢出演示:助记符按「首次出现顺序」分配素数,ldr 是第一个 → 2;64 条 ldr 相乘即 2^64
	many := make([]string, 64)
	for i := range many {
		many[i] = "ldr"
	}
	check(mnemonicPrime("ldr") == 2 && primeSignature(many[:63], false) == uint64(1)<<63 &&
		primeSignature(many, false) == 0,
		"product 口径在 uint64 里静默回绕:2^63 正常,2^64 归零", primeSignature(many, false))
	check(primeSignature(many, true) == 128, "同一序列走 sum 口径不溢出:64*2 = 128",
		primeSignature(many, true))

	fmt.Println("== 2. 多阶段匹配 ==")
	m := stagedMatch(A, B, algos, nil)
	check(m["main"].Algo == "hash" && m["main"].Conf == 0.98, "main 由 hash 匹配(0.98)", m["main"])
	check(m["check"].Algo == "prime_signature" && m["check"].Conf == 0.80,
		"check 由 prime_signature 以 0.80 补上", m["check"])
	check(m["hash"].Target == "hashAlias", "改名后的函数靠指纹而非名字认出", m["hash"])
	check(m["log"].Algo == "string_references" && m["log"].Conf == 0.60,
		"指令数变了 → 只剩字符串引用这条线索(0.60)", m["log"])
	_, okA := m["helperAdd"]
	_, okV := m["validate"]
	check(!okA && !okV, "被删/新增的函数没有被配上", nil)

	fmt.Println("== 3. drill down:用调用关系解开指纹歧义 ==")
	DA, DB := drillDown()
	dm := stagedMatch(DA, DB, algos, nil)
	check(dm["entry"].Target == "entry", "entry 在两侧都唯一 → 直接匹配", dm["entry"])
	_, okW := dm["worker"]
	_, okT := dm["twin"]
	check(!okW && !okT, "worker 与 twin 指纹完全相同 → 两侧都歧义,谁都不匹配", nil)
	added := propagate(DA, DB, dm, nil)
	check(len(added) == 1 && added[0] == "worker→worker2",
		"drill down 用已匹配函数的唯一 callee 补上", added)
	_, okT2 := dm["twin"]
	check(!okT2, "没有调用关系可依托的 twin 仍留在未匹配集合", nil)

	fmt.Println("== 4. MD index ==")
	cg := map[string][]string{}
	for _, k := range keysOf(A) {
		cg[k] = keysOf(A[k].Calls)
	}
	top := mdOfFunctions(cg, false, defaultNodeWeights)
	bot := mdOfFunctions(cg, true, defaultNodeWeights)
	check(top["main"] > 0 && top["parse"] > 0, "有边的顶点 MD index > 0", fmtFloat(top["parse"]))
	check(top["helperAdd"] == 0, "孤顶点没有关联边 → MD index = 0", nil)
	same := true
	for _, k := range keysOf(top) {
		if math.Abs(top[k]-bot[k]) > 1e-12 {
			same = false
		}
	}
	check(same, "默认权重 {2,3,5,7,0,0} 拓扑层系数为 0 → top-down 与 bottom-up 一致", nil)
	top2 := mdOfFunctions(cg, false, fullWeights)
	bot2 := mdOfFunctions(cg, true, fullWeights)
	diff := []string{}
	for _, k := range keysOf(top2) {
		if math.Abs(top2[k]-bot2[k]) > 1e-12 {
			diff = append(diff, k)
		}
	}
	check(len(diff) > 0, "拓扑层系数非零后两个方向才分道扬镳", diff)
	fg := map[string][]string{"b0": {"b1", "end"}, "b1": {"end"}, "end": {}}
	relaxed := mdIndexNode(fg, "b0", false, proximityWeights, []string{"b0"})
	full := mdIndexNode(fg, "b0", false, fullWeights, []string{"b0"})
	check(math.Abs(relaxed-full) > 1e-12, "relaxed(丢掉拓扑层)与完整 MD index 不同",
		fmtFloat(relaxed)+" vs "+fmtFloat(full))
	check(math.Abs(mdIndexNode(fg, "b0", false, defaultNodeWeights, []string{"b0"})-relaxed) < 1e-12,
		"但 relaxed 与默认权重下结果一致 —— 层系数为 0 时两者等价", nil)

	fmt.Println("== 5. confidence 与 similarity ==")
	strong := []float64{0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.10}
	dSq := math.Abs(squashedConfidence(strong, 8) - normalizeSigmoidCDF(0.95, 8))
	dLin := 0.085
	check(dSq < dLin, "整体很强时,一个弱匹配对压扁值的影响小于对线性均值的影响",
		fmtFloat(dSq)+" < "+fmtFloat(dLin))
	weak := []float64{0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.90}
	check(squashedConfidence(weak, 8) < 0.30, "整体很弱时一个强匹配也救不起来",
		fmtFloat(squashedConfidence(weak, 8)))
	check(math.Abs(normalizeSigmoidCDF(0, 8)) < 1e-12 &&
		math.Abs(normalizeSigmoidCDF(1, 8)-1) < 1e-12,
		"归一化 S 型把 [0,1] 映射回 [0,1]", nil)
	check(math.Abs(functionSimilarity(1, 1, 1, 0, 1)-1) < 1e-12,
		"function similarity 权重和 = 1(0.25+0.15+0.10+0.50)", nil)
	check(math.Abs(functionSimilarity(1, 1, 1, 0, 0.5)-0.5) < 1e-12,
		"confidence 直接相乘:conf 减半 → similarity 减半", nil)
	check(math.Abs(binarySimilarity(1, 1, 1, 1, 0, 1)-1) < 1e-12,
		"binary similarity 权重和 = 1(0.35+0.25+0.10+0.10+0.20)", nil)
	check(math.Abs(binarySimilarity(1, 1, 1, 1, 1, 0.9)-0.72) < 1e-12,
		"call graph MD 差异 20% 全失配再乘 conf=0.9 → 0.8*0.9 = 0.72", nil)

	fmt.Println("== 6. 库函数不参与统计 ==")
	libFn := mk([]string{"ret"}, map[string][]string{"b0": {}})
	A2 := map[string]Func{}
	B2 := map[string]Func{}
	for k, v := range A {
		A2[k] = v
	}
	for k, v := range B {
		B2[k] = v
	}
	A2["libcStart"], B2["libcStart"] = libFn, libFn
	m3 := stagedMatch(A2, B2, algos, map[string]bool{"libcStart": true})
	m4 := stagedMatch(A2, B2, algos, nil)
	_, in3 := m3["libcStart"]
	_, in4 := m4["libcStart"]
	check(!in3 && in4, "只统计非库函数:排除后不匹配,不排除就会被当成真函数配掉",
		fmt.Sprintf("lib=() 时匹配到 %v", in4))

	fmt.Printf("\n结果: %d 项失败\n", len(failed))
	for _, x := range failed {
		fmt.Printf("  FAIL: %s\n", x)
	}
	if len(failed) > 0 {
		panic("self-check failed")
	}
}
