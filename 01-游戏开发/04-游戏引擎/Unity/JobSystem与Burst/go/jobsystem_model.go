// jobsystem_model.go — 从 jobsystem.go 拆出的模型段：并行分批 / work stealing /
// 分配器寿命 / Burst HPC# 类型子集（同 package，无需 import）。
package main

import "fmt"

func batchDuration(idx int) int { return 1 + (idx*7)%29 }

type stealResult struct {
	steals    int
	processed int
	left      int
}

// stealSchedule 模拟 work stealing：闲下来的 worker 一次只偷别人剩余批次的一半。
func stealSchedule(batchCount, workers int) stealResult {
	per := (batchCount + workers - 1) / workers
	queues := make([][]int, workers)
	durs := make([][]int, workers)
	cursor := 0
	for w := 0; w < workers; w++ {
		end := cursor + per
		if end > batchCount {
			end = batchCount
		}
		for i := cursor; i < end; i++ {
			queues[w] = append(queues[w], i)
			durs[w] = append(durs[w], batchDuration(i))
		}
		cursor = end
	}
	res := stealResult{}
	for guard := 0; guard < 1000000; guard++ {
		alive := false
		for w := 0; w < workers; w++ {
			if len(durs[w]) > 0 {
				alive = true
				durs[w][0]--
				if durs[w][0] == 0 {
					durs[w] = durs[w][1:]
					queues[w] = queues[w][1:]
					res.processed++
				}
				continue
			}
			victim := 0
			for j := 1; j < workers; j++ {
				if len(queues[j]) > len(queues[victim]) {
					victim = j
				}
			}
			remaining := len(queues[victim])
			if remaining < 2 {
				continue
			}
			take := remaining / 2
			if take < 1 {
				take = 1
			}
			res.steals++
			queues[w] = append(queues[w], queues[victim][:take]...)
			durs[w] = append(durs[w], durs[victim][:take]...)
			queues[victim] = queues[victim][take:]
			durs[victim] = durs[victim][take:]
		}
		if !alive {
			break
		}
	}
	for w := 0; w < workers; w++ {
		res.left += len(queues[w])
	}
	return res
}

type allocatorTracker struct {
	born     map[*nativeArray]int
	warnings []string
	frame    int
}

func (t *allocatorTracker) track(a *nativeArray) {
	if t.born == nil {
		t.born = map[*nativeArray]int{}
	}
	t.born[a] = t.frame
}

func (t *allocatorTracker) endFrame() {
	t.frame++
	for a, born := range t.born {
		limit := 0
		switch a.allocate {
		case "Temp":
			limit = 1
		case "TempJob":
			limit = 4
		default:
			limit = 0 // Persistent：不告警
		}
		if limit > 0 && t.frame-born > limit {
			t.warnings = append(t.warnings, fmt.Sprintf("%s 存活 %d 帧 > %d", a.allocate, t.frame-born, limit))
		}
	}
}

var burstSupported = map[string]bool{
	"bool": true, "byte": true, "sbyte": true, "double": true, "float": true,
	"int": true, "uint": true, "long": true, "ulong": true, "short": true, "ushort": true,
}

func burstFieldOK(t string) bool {
	switch t {
	case "struct", "struct+fixed", "generic struct", "LayoutKind.Explicit struct", "static readonly managed array":
		return true
	}
	return burstSupported[t]
}
