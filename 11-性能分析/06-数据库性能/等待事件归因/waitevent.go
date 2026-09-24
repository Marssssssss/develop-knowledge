// waitevent.go — 与 waitevent.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"fmt"
	"sort"
)

func classifySample(state string, wet *string) string {
	if state == "active" {
		if wet == nil {
			return "running"
		}
		return *wet
	}
	return "idle-session"
}

func attribute(samples [][2]string) map[string]float64 {
	buckets := map[string]int{}
	for _, s := range samples {
		var wet *string
		if s[1] != "" {
			wet = &s[1]
		}
		buckets[classifySample(s[0], wet)]++
	}
	share := map[string]float64{}
	for k, v := range buckets {
		share[k] = float64(v) / float64(len(samples))
	}
	return share
}

func dominant(share map[string]float64) (string, float64) {
	keys := make([]string, 0, len(share))
	for k := range share {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	best, bestV := "", 0.0
	for _, k := range keys {
		if k == "idle-session" || k == "running" {
			continue
		}
		if share[k] > bestV {
			best, bestV = k, share[k]
		}
	}
	return best, bestV
}

func main() {
	samples := [][2]string{}
	for i := 0; i < 8; i++ {
		samples = append(samples, [2]string{"active", "Client"})
	}
	for i := 0; i < 2; i++ {
		samples = append(samples, [2]string{"active", ""})
	}
	share := attribute(samples)
	d, v := dominant(share)
	fmt.Printf("dominant=%s %.0f%% → 应用侧瓶颈(ClientRead think time)\n", d, v*100)
}
