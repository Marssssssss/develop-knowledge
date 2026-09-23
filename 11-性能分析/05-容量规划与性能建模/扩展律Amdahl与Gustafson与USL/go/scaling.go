// Package scaling 实现 Amdahl / Gustafson / USL 三条扩展律。
//
// 符号约定（与 hpc101 一致）：P 是**可并行比例**，f = 1 − P 是串行比例。
package scaling

import (
	"errors"
	"math"
)

// Amdahl 强扩展加速比：S = 1 / [(1 − P) + P/N]。
func Amdahl(P, N float64) (float64, error) {
	if P < 0 || P > 1 {
		return 0, errors.New("P must be in [0,1]")
	}
	if N < 1 {
		return 0, errors.New("N must be >= 1")
	}
	return 1.0 / ((1.0 - P) + P/N), nil
}

// AmdahlSerial 按串行比例书写：S = 1 / [f + (1 − f)/N]。
func AmdahlSerial(f, N float64) (float64, error) {
	if f < 0 || f > 1 {
		return 0, errors.New("serial fraction must be in [0,1]")
	}
	if N < 1 {
		return 0, errors.New("N must be >= 1")
	}
	return 1.0 / (f + (1.0-f)/N), nil
}

// AmdahlLimit 天花板 1/(1 − P)；P = 1 时无上限。
func AmdahlLimit(P float64) float64 {
	if P >= 1.0 {
		return math.Inf(1)
	}
	return 1.0 / (1.0 - P)
}

// Gustafson 弱扩展加速比：S = N − (1 − P)(N − 1) = 1 + P(N − 1)。
func Gustafson(P, N float64) (float64, error) {
	if P < 0 || P > 1 {
		return 0, errors.New("P must be in [0,1]")
	}
	if N < 1 {
		return 0, errors.New("N must be >= 1")
	}
	return N - (1.0-P)*(N-1.0), nil
}

// GustafsonSerial 按串行比例书写：S = f + N(1 − f)。
func GustafsonSerial(f, N float64) float64 { return f + N*(1.0-f) }

// Efficiency 效率 ε = S/N。
func Efficiency(S, N float64) float64 { return S / N }

// ParallelFractionFor 反解：N 个处理器上达到 target 加速比所需的可并行比例。
func ParallelFractionFor(target, N float64) float64 {
	return (1.0 - 1.0/target) / (1.0 - 1.0/N)
}

// SerialTimeShare 并行执行时串行部分占 wallclock 的比例。
func SerialTimeShare(P, N float64) float64 {
	s := 1.0 - P
	return s / (s + P/N)
}

// USLCapacity USL 相对容量 C(N) = γN / [1 + α(N−1) + βN(N−1)]。
func USLCapacity(N, alpha, beta, gamma float64) (float64, error) {
	d := 1.0 + alpha*(N-1.0) + beta*N*(N-1.0)
	if d <= 0 {
		return 0, errors.New("non-positive denominator")
	}
	return gamma * N / d, nil
}
