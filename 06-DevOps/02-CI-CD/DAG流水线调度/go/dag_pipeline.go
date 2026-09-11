// dag_pipeline.go — DAG 流水线调度(Kahn 拓扑排序 + 关键路径 + 失败传播)
//
// 模拟 GitLab CI needs / GitHub Actions needs 的 DAG 调度核心:
//
//	demo 1 菱形依赖分层  demo 2 环检测  demo 3 barrier vs DAG  demo 4 失败传播
package main

import (
	"fmt"
	"sort"
)

// Job 模拟一个 CI job;needs 为依赖的 job 名(GitLab needs / GitHub needs)。
type Job struct {
	Name     string
	Stage    string
	Duration int      // 模拟执行时长(分钟)
	Needs    []string // 依赖的 job 名列表
}

var jobs []Job

func byName(name string) *Job {
	for i := range jobs {
		if jobs[i].Name == name {
			return &jobs[i]
		}
	}
	return nil
}

func resetJobs(j ...Job) { jobs = j }

// topoLayers Kahn 分层拓扑排序:返回按层分组的 job 名;有环返回 error。
// 每层内 job 互不依赖,可并行执行;层数 = 无限并发下的最少"轮次"。
func topoLayers() ([][]string, error) {
	succ := map[string][]string{}   // need -> 依赖它的 job(邻接表)
	inDeg := map[string]int{}
	for _, j := range jobs {
		if _, ok := inDeg[j.Name]; !ok {
			inDeg[j.Name] = 0
		}
	}
	for _, j := range jobs {
		for _, n := range j.Needs {
			if _, ok := inDeg[n]; !ok {
				return nil, fmt.Errorf("job '%s' depends on unknown job '%s'", j.Name, n)
			}
			succ[n] = append(succ[n], j.Name) // 边 need -> j
			inDeg[j.Name]++
		}
	}
	var queue []string
	for n, d := range inDeg {
		if d == 0 {
			queue = append(queue, n)
		}
	}
	var layers [][]string
	done := 0
	for len(queue) > 0 {
		sort.Strings(queue) // 排序保证输出可复现
		layers = append(layers, queue)
		done += len(queue)
		var next []string
		for _, u := range queue { // 这一批 job "完成"
			for _, v := range succ[u] { // 删出边:后继入度减 1
				inDeg[v]--
				if inDeg[v] == 0 {
					next = append(next, v)
				}
			}
		}
		queue = next
	}
	if done != len(jobs) { // 出队数 < 节点数 => 有环
		var cyc []string
		for n, d := range inDeg {
			if d > 0 {
				cyc = append(cyc, n)
			}
		}
		sort.Strings(cyc)
		return nil, fmt.Errorf("cycle detected among jobs: %v", cyc)
	}
	return layers, nil
}

// earliestFinish 关键路径递推:ef[j] = dur[j] + max(ef[need])。
func earliestFinish() map[string]int {
	ef := map[string]int{}
	layers, err := topoLayers()
	if err != nil {
		return ef
	}
	for _, layer := range layers { // 拓扑序保证 need 先于 j 计算
		for _, name := range layer {
			j := byName(name)
			best := 0
			for _, n := range j.Needs {
				if ef[n] > best {
					best = ef[n]
				}
			}
			ef[name] = j.Duration + best
		}
	}
	return ef
}

// criticalPath 从 ef 最大的 job 回溯 max 来源,得到关键路径。
func criticalPath() []string {
	ef := earliestFinish()
	end := ""
	for n, v := range ef {
		if end == "" || v > ef[end] {
			end = n
		}
	}
	path := []string{end}
	for {
		j := byName(end)
		if len(j.Needs) == 0 {
			return path
		}
		best := ""
		for _, n := range j.Needs {
			if best == "" || ef[n] > ef[best] {
				best = n
			}
		}
		path = append([]string{best}, path...)
		end = best
	}
}

// stageBarrierTime stage barrier:每个 stage 取最大时长再求和。
func stageBarrierTime() int {
	var order []string
	for _, j := range jobs { // stage 按首次出现顺序
		known := false
		for _, s := range order {
			if s == j.Stage {
				known = true
			}
		}
		if !known {
			order = append(order, j.Stage)
		}
	}
	total := 0
	for _, st := range order {
		maxDur := 0
		for _, j := range jobs {
			if j.Stage == st && j.Duration > maxDur {
				maxDur = j.Duration
			}
		}
		total += maxDur
	}
	return total
}

// simulateRun 按拓扑序执行;fail 集合内的 job 失败,传递依赖者被跳过。
func simulateRun(fail map[string]bool) {
	layers, err := topoLayers()
	if err != nil {
		fmt.Println("  ", err)
		return
	}
	status := map[string]string{}
	for _, layer := range layers {
		for _, name := range layer {
			j := byName(name)
			bad := ""
			for _, n := range j.Needs {
				if status[n] == "FAILED" || status[n] == "SKIPPED" {
					bad = n
				}
			}
			switch {
			case bad != "":
				status[name] = "SKIPPED"
				fmt.Printf("  [skip]  %-10s SKIPPED (need '%s')\n", name, bad)
			case fail[name]:
				status[name] = "FAILED"
				fmt.Printf("  [fail]  %-10s FAILED  (exit code 1)\n", name)
			default:
				status[name] = "SUCCESS"
				fmt.Printf("  [ok]    %-10s SUCCESS (%d min)\n", name, j.Duration)
			}
		}
	}
}

func main() {
	// demo 1: 菱形依赖
	fmt.Println("== demo 1: 菱形依赖(DAG) ==")
	resetJobs(
		Job{"build", "build", 3, nil},
		Job{"test_unit", "test", 2, []string{"build"}},
		Job{"test_intg", "test", 4, []string{"build"}},
		Job{"test_perf", "test", 5, []string{"build"}},
		Job{"deploy", "deploy", 2, []string{"test_unit", "test_intg", "test_perf"}},
	)
	if layers, err := topoLayers(); err == nil {
		for i, layer := range layers {
			fmt.Printf("  layer %d: %v\n", i, layer)
		}
		ef := earliestFinish()
		maxV := 0
		for _, v := range ef {
			if v > maxV {
				maxV = v
			}
		}
		names := make([]string, 0, len(ef)) // 排序保证输出可复现
		for k := range ef {
			names = append(names, k)
		}
		sort.Strings(names)
		fmt.Printf("  earliest_finish: map[")
		for _, k := range names {
			fmt.Printf("%s:%d ", k, ef[k])
		}
		fmt.Println("]")
		fmt.Printf("  critical path: %v (wall-clock lower bound = %d min)\n",
			criticalPath(), maxV)
	}

	// demo 2: needs 成环
	fmt.Println("\n== demo 2: needs 成环(创建即失败) ==")
	resetJobs(
		Job{"build", "build", 1, []string{"deploy"}},
		Job{"test", "test", 1, []string{"build"}},
		Job{"deploy", "deploy", 1, []string{"test"}},
	)
	if _, err := topoLayers(); err != nil {
		fmt.Println("  pipeline creation failed:", err)
	}

	// demo 3: stage barrier vs DAG(GitLab 博客算例)
	fmt.Println("\n== demo 3: stage barrier vs DAG ==")
	resetJobs(
		Job{"build_a", "build", 1, nil},
		Job{"build_b", "build", 5, nil},
		Job{"test_c", "test", 2, []string{"build_a"}},
		Job{"deploy", "deploy", 1, []string{"test_c"}},
	)
	{
		barrier := stageBarrierTime()
		ef := earliestFinish()
		dag := 0
		for _, v := range ef {
			if v > dag {
				dag = v
			}
		}
		fmt.Printf("  stage barrier: %d min  (build 5 + test 2 + deploy 1)\n", barrier)
		fmt.Printf("  DAG(needs):    %d min   (critical path %v)\n", dag, criticalPath())
		fmt.Printf("  节省: %d min — test_c 无需等 build_b\n", barrier-dag)
	}

	// demo 4: 失败传播
	fmt.Println("\n== demo 4: 失败传播(test_a 失败 -> deploy_a 跳过) ==")
	resetJobs(
		Job{"build", "build", 1, nil},
		Job{"test_a", "test", 2, []string{"build"}},
		Job{"test_b", "test", 2, []string{"build"}},
		Job{"deploy_a", "deploy", 1, []string{"test_a"}},
		Job{"deploy_b", "deploy", 1, []string{"test_b"}},
	)
	simulateRun(map[string]bool{"test_a": true})
}
