package main

import "math"

// demo 515 Go 侧:IncrementalPCA 与 Youngs-Cramer 增量均值/方差更新。
// 语义与 python/ipca.py 逐条对应;python 侧已与 sklearn 1.9.1 对拍到 114/114。

// genBatches 复刻 sklearn.utils.gen_batches:尾部不足 minBatchSize 时并进最后一块。
func genBatches(n, batchSize, minBatchSize int) [][2]int {
	var out [][2]int
	start := 0
	for i := 0; i < n/batchSize; i++ {
		end := start + batchSize
		if end+minBatchSize > n {
			continue
		}
		out = append(out, [2]int{start, end})
		start = end
	}
	if start < n {
		out = append(out, [2]int{start, n})
	}
	return out
}

// incrementalMeanAndVar 是 Chan/Golub/LeVeque 的校正两趟法。
// lastCount 为 0 时必须走 zeros 分支 —— 否则 lastSum/(lastCount/newCount) 是 0/0。
func incrementalMeanAndVar(X Mat, lastMean, lastVar []float64, lastCount []float64) (
	[]float64, []float64, []float64) {
	nSamples, nFeatures := len(X), len(X[0])
	lastSum := make([]float64, nFeatures)
	newSum := make([]float64, nFeatures)
	for j := 0; j < nFeatures; j++ {
		lastSum[j] = lastMean[j] * lastCount[j]
		s := 0.0
		for i := 0; i < nSamples; i++ {
			s += X[i][j]
		}
		newSum[j] = s
	}
	newCount := make([]float64, nFeatures)
	updCount := make([]float64, nFeatures)
	updMean := make([]float64, nFeatures)
	for j := 0; j < nFeatures; j++ {
		newCount[j] = float64(nSamples)
		updCount[j] = lastCount[j] + newCount[j]
		updMean[j] = (lastSum[j] + newSum[j]) / updCount[j]
	}
	if lastVar == nil {
		return updMean, nil, updCount
	}
	updVar := make([]float64, nFeatures)
	for j := 0; j < nFeatures; j++ {
		T := newSum[j] / newCount[j]
		correction := 0.0
		sq := 0.0
		for i := 0; i < nSamples; i++ {
			d := X[i][j] - T
			correction += d
			sq += d * d
		}
		newUnnorm := sq - correction*correction/newCount[j]
		var updUnnorm float64
		if lastCount[j] == 0 {
			updUnnorm = newUnnorm
		} else {
			lastOverNew := lastCount[j] / newCount[j]
			delta := lastSum[j]/lastOverNew - newSum[j]
			updUnnorm = lastVar[j]*lastCount[j] + newUnnorm +
				lastOverNew/updCount[j]*delta*delta
		}
		updVar[j] = updUnnorm / updCount[j]
	}
	return updMean, updVar, updCount
}

// IncrementalPCA 分块 partial_fit 的 PCA。
type IncrementalPCA struct {
	NComponents      int // < 0 表示 None
	NComponentsUsed  int
	BatchSize        int // < 0 表示 None
	BatchSizeUsed    int
	Components       Mat
	SingularValues   []float64
	Mean             []float64
	Var              []float64
	ExplainedVar     []float64
	ExplainedVarRat  []float64
	NoiseVariance    float64
	NSamplesSeen     int
	NFeaturesIn      int
	componentsInited bool
}

func repeat(v float64, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}

// PartialFit 处理一块数据。首块会把状态初始化。
func (p *IncrementalPCA) PartialFit(X Mat) {
	firstPass := !p.componentsInited
	if firstPass {
		p.Components = nil
		p.componentsInited = true
		p.NSamplesSeen = 0
		p.Mean = []float64{0}
		p.Var = []float64{0}
	}
	nSamples, nFeatures := len(X), len(X[0])
	if firstPass {
		p.NFeaturesIn = nFeatures
	} else if nFeatures != p.NFeaturesIn {
		panic("X has different number of features than previous partial_fit calls")
	}
	k := p.NComponents
	if k < 0 {
		if p.Components == nil {
			k = minInt(nSamples, nFeatures)
		} else {
			k = len(p.Components)
		}
	} else if k > nFeatures {
		panic("n_components must be <= n_features")
	} else if k > nSamples && firstPass {
		panic("n_components must be <= the first batch size")
	}
	p.NComponentsUsed = k

	if len(p.Mean) < nFeatures {
		p.Mean = repeat(p.Mean[0], nFeatures)
		p.Var = repeat(p.Var[0], nFeatures)
	}
	colMean, colVar, cntVec := incrementalMeanAndVar(X,
		repeat(p.Mean[0], nFeatures), repeat(p.Var[0], nFeatures), repeat(float64(p.NSamplesSeen), nFeatures))
	nTotal := cntVec[0]

	var stacked Mat
	if p.NSamplesSeen == 0 {
		stacked = zeros(nSamples, nFeatures)
		for i := 0; i < nSamples; i++ {
			for j := 0; j < nFeatures; j++ {
				stacked[i][j] = X[i][j] - colMean[j]
			}
		}
	} else {
		batchMean := make([]float64, nFeatures)
		for j := 0; j < nFeatures; j++ {
			s := 0.0
			for i := 0; i < nSamples; i++ {
				s += X[i][j]
			}
			batchMean[j] = s / float64(nSamples)
		}
		scale := math.Sqrt(float64(p.NSamplesSeen)/nTotal) * math.Sqrt(float64(nSamples))
		prev := len(p.SingularValues)
		stacked = zeros(prev+nSamples+1, nFeatures)
		for t := 0; t < prev; t++ {
			for j := 0; j < nFeatures; j++ {
				stacked[t][j] = p.SingularValues[t] * p.Components[t][j]
			}
		}
		for i := 0; i < nSamples; i++ {
			for j := 0; j < nFeatures; j++ {
				stacked[prev+i][j] = X[i][j] - batchMean[j]
			}
		}
		for j := 0; j < nFeatures; j++ {
			stacked[prev+nSamples][j] = scale * (p.Mean[j] - batchMean[j])
		}
	}

	U, S, Vt := jacobiSVD(stacked, 1e-14, 60)
	_, Vt = svdFlip(U, Vt, false)

	explainedVar := make([]float64, len(S))
	ratio := make([]float64, len(S))
	denom := 0.0
	for j := 0; j < nFeatures; j++ {
		denom += colVar[j] * nTotal
	}
	for i, s := range S {
		explainedVar[i] = s * s / (nTotal - 1)
		ratio[i] = s * s / denom
	}

	p.NSamplesSeen = int(nTotal)
	p.Components = sliceRows(Vt, k)
	p.SingularValues = append([]float64(nil), S[:k]...)
	p.Mean = colMean
	p.Var = colVar
	p.ExplainedVar = append([]float64(nil), explainedVar[:k]...)
	p.ExplainedVarRat = append([]float64(nil), ratio[:k]...)
	if k != nSamples && k != nFeatures {
		tailSum := 0.0
		tail := explainedVar[k:]
		for _, v := range tail {
			tailSum += v
		}
		if len(tail) > 0 {
			p.NoiseVariance = tailSum / float64(len(tail))
		}
	} else {
		p.NoiseVariance = 0
	}
}

// Fit 按 batchSize 分块调用 PartialFit。batchSize < 0 时取 5 * n_features。
func (p *IncrementalPCA) Fit(X Mat) {
	nSamples, nFeatures := len(X), len(X[0])
	if p.BatchSize < 0 {
		p.BatchSizeUsed = 5 * nFeatures
	} else {
		p.BatchSizeUsed = p.BatchSize
	}
	minBatch := p.NComponents
	if minBatch < 0 {
		minBatch = 0
	}
	for _, b := range genBatches(nSamples, p.BatchSizeUsed, minBatch) {
		p.PartialFit(copyMat(X[b[0]:b[1]]))
	}
}

// Transform 是 (X - Mean) @ Components^T。
func (p *IncrementalPCA) Transform(X Mat) Mat {
	k := len(p.Components)
	nFeatures := len(X[0])
	out := zeros(len(X), k)
	for i := range X {
		for c := 0; c < k; c++ {
			s := 0.0
			for j := 0; j < nFeatures; j++ {
				s += (X[i][j] - p.Mean[j]) * p.Components[c][j]
			}
			out[i][c] = s
		}
	}
	return out
}
