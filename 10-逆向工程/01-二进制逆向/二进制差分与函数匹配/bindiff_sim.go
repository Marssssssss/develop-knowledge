package main

// BinDiff 的置信度压扁(confidence)与相似度权重(similarity)。
//
// 依据(本轮实读的官方 manual / concepts.md):
//   * confidence「不是所有算法置信度的简单平均」:官方给的是行为描述 ——
//     少数弱匹配不该把整体拖太低,少数强匹配也不该把整体救起来。
//     「两端饱和、中间陡」正是 S 型的形状,故用归一化 S 型实现。
//   * function similarity 权重:flow graph 边 25% / 基本块 15% / 指令 10% /
//     flow graph MD index 差异 50%。
//   * binary similarity 权重:边 35% / 基本块 25% / 函数 10% / 指令 10% /
//     call graph MD index 差异 20%。
//   * 两者最后都**再乘 confidence**:置信度说"这条匹配本身可信么",
//     相似度说"两个二进制整体像么",相乘即"只统计可信匹配的相似度"。
//   * binary similarity 只统计**非库函数**,否则共用同一套运行库会虚高相似度。

import "math"

func sigmoid(x float64) float64 { return 1.0 / (1.0 + math.Exp(-x)) }

// normalizeSigmoidCDF:把 [0,1] 上的均值经 S 型映射回 [0,1],两端饱和。
func normalizeSigmoidCDF(m, k float64) float64 {
	lo, hi := sigmoid(-k/2), sigmoid(k/2)
	return (sigmoid(k*(m-0.5)) - lo) / (hi - lo)
}

func squashedConfidence(confs []float64, k float64) float64 {
	sum := 0.0
	for _, c := range confs {
		sum += c
	}
	return normalizeSigmoidCDF(sum/float64(len(confs)), k)
}

// functionSimilarity:边 25% / 块 15% / 指令 10% / flow graph MD 差异 50%,再乘 conf。
func functionSimilarity(edges, blocks, insns, mdDistance, conf float64) float64 {
	return (0.25*edges + 0.15*blocks + 0.10*insns + 0.50*(1.0-mdDistance)) * conf
}

// binarySimilarity:边 35% / 块 25% / 函数 10% / 指令 10% / call graph MD 差异 20%,再乘 conf。
func binarySimilarity(edges, blocks, funcs, insns, cgDistance, conf float64) float64 {
	s := 0.35*edges + 0.25*blocks + 0.10*funcs + 0.10*insns + 0.20*(1.0-cgDistance)
	return s * conf
}
