package main

// demo 516 Go 侧:TimeSeriesSplit,与 python/splits.py 逐条对应。
//
// 口径差异:Python 用异常,Go 用 panic(消息文本保留原文案的关键片段)。
//   * TestSize < 0      → 未指定,默认 nSamples / (NSplits+1)
//   * MaxTrainSize <= 0 → 不设上限
//   * Gap 是训练集末端与测试集开头之间**被剔除**的样本数

// Fold 一折:训练集与测试集的下标。
type Fold struct {
	Train []int
	Test  []int
}

// TimeSeriesSplit 与 sklearn.model_selection.TimeSeriesSplit 同语义。
type TimeSeriesSplit struct {
	NSplits      int
	TestSize     int
	Gap          int
	MaxTrainSize int
}

func iotaRange(lo, hi int) []int {
	if hi <= lo {
		return []int{}
	}
	out := make([]int, 0, hi-lo)
	for i := lo; i < hi; i++ {
		out = append(out, i)
	}
	return out
}

// Split 生成 nSplits 折。先把全部样本切成 NSplits+1 段,第 k 折用第 k+1 段做测试、
// 前面所有段做训练,故训练集是**超集递增**的。
func (t TimeSeriesSplit) Split(nSamples int) []Fold {
	nFolds := t.NSplits + 1
	testSize := t.TestSize
	if testSize < 0 {
		testSize = nSamples / nFolds
	}
	if nFolds > nSamples {
		panic("Cannot have number of folds greater than the number of samples")
	}
	if nSamples-t.Gap-testSize*t.NSplits <= 0 {
		panic("Too many splits for number of samples with test_size and gap")
	}
	out := make([]Fold, 0, t.NSplits)
	for k := 0; k < t.NSplits; k++ {
		testStart := nSamples - t.NSplits*testSize + k*testSize
		trainEnd := testStart - t.Gap
		var tr []int
		if t.MaxTrainSize > 0 && t.MaxTrainSize < trainEnd {
			tr = iotaRange(trainEnd-t.MaxTrainSize, trainEnd)
		} else {
			tr = iotaRange(0, trainEnd)
		}
		out = append(out, Fold{Train: tr, Test: iotaRange(testStart, testStart+testSize)})
	}
	return out
}

// RollingOriginSplits 是**部署视角**的切分:horizon 是预测跨度(不是训练/测试之间的缝隙),
// 它决定"特征在 t 时刻能看到什么";数据不够时静默少给几折,而不是像 Split 那样报错。
func RollingOriginSplits(nSamples, nSplits, horizon, minTrain int) []Fold {
	out := []Fold{}
	lastStart := nSamples - nSplits*horizon
	for k := 0; k < nSplits; k++ {
		testStart := lastStart + k*horizon
		if testStart < minTrain {
			continue
		}
		out = append(out, Fold{Train: iotaRange(0, testStart),
			Test: iotaRange(testStart, testStart+horizon)})
	}
	return out
}
