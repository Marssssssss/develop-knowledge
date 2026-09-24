// poolthr.go — 与 poolthr.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

func fdLimitNeeded(maxClientConn, poolSize, databases, users int) int {
	return maxClientConn + poolSize*databases*users
}

func littleN(throughput, responseTime float64) float64 {
	return throughput * responseTime
}

// ThroughputCurve:≤容量线性;超出付上下文切换税(模型)。
func ThroughputCurve(n, capacity int, demand, switchCost float64) float64 {
	if n <= capacity {
		return float64(n) / demand
	}
	overload := float64(n - capacity)
	return float64(capacity) / (demand * (1.0 + switchCost*overload/float64(capacity)))
}

func main() {
	fmt.Println("fd(1 user):", fdLimitNeeded(1000, 20, 4, 1))       // 1080
	fmt.Println("fd(8 users):", fdLimitNeeded(1000, 20, 4, 8))      // 1640
	fmt.Println("little: N =", littleN(500, 0.2))                   // 100
	x8 := ThroughputCurve(8, 8, 0.02, 0.15)
	x16 := ThroughputCurve(16, 8, 0.02, 0.15)
	fmt.Printf("X(8)=%.0f X(16)=%.0f → 非单调,峰值池=容量\n", x8, x16)
}
