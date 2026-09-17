// mongo_pipeline_check.go — 自检:重排/合并规则、ESR 索引选择、结构校验
//
// 与 mongo_pipeline.go / mongo_optimizer.go / mongo_esr.go 同属 package main。
// 断言逐条对应官方文档示例或明文规则(出处见另三个文件的文件头)。
//
// 运行: go run mongo_pipeline.go mongo_optimizer.go mongo_esr.go mongo_pipeline_check.go
package main

import (
	"fmt"
	"os"
)

// ---------------------------------------------------------------- 自检

var pass, fail int

// check 的 detail 用变参:本机无 Go 工具链,固定三参签名会让"少传一个实参"变成
// 硬编译错误却无法本地发现(与 C 版 check(label,cond,detail) 同一教训)。
func check(label string, cond bool, detail ...string) {
	if cond {
		pass++
		fmt.Println("  PASS ", label)
		return
	}
	fail++
	extra := ""
	if len(detail) > 0 {
		extra = detail[0]
	}
	fmt.Println("  FAIL ", label, extra)
}

func main() {
	fmt.Println("[1] R1 投影下推(官方示例)")
	src := []Stage{
		st("$addFields", map[string]any{"maxTime": "$max(times)", "minTime": "$min(times)"}),
		st("$project", map[string]any{"name": 1, "times": 1, "maxTime": 1, "minTime": 1, "avgTime": "$avg"}),
		st("$match", map[string]any{"name": "Joe Schmoe", "maxTime": 20, "minTime": 5, "avgTime": 7}),
	}
	out := Optimize(src)
	check("R1 阶段数 3 → 5(拆出两个新 $match)", len(out) == 5, fmt.Sprint(len(out)))
	check("R1 首阶段为 $match{name}", out[0].Name == "$match" && len(out[0].Spec) == 1 && out[0].Spec["name"] == "Joe Schmoe", dumpStages(out))
	check("R1 依赖投影计算值的 avgTime 留在末尾",
		out[4].Name == "$match" && out[4].Spec["avgTime"] == 7, dumpSpec(out[4].Spec))
	check("R1 中间新 $match 同时含 maxTime/minTime",
		len(out[2].Spec) == 2 && out[2].Spec["maxTime"] == 20 && out[2].Spec["minTime"] == 5, dumpSpec(out[2].Spec))

	fmt.Println("[2] R1 边界:不得跨越 $unset 删除的字段")
	u := []Stage{st("$unset", map[string]any{"$unset": []string{"x"}}), st("$match", map[string]any{"x": 5})}
	check("R1 跨 $unset 保持原样", dumpStages(Optimize(u)) == dumpStages(u), dumpStages(Optimize(u)))

	fmt.Println("[3] R2 / R4 序列重排")
	check("R2 $match 移到 $sort 之前",
		dumpStages(Optimize([]Stage{st("$sort", map[string]any{"age": -1}), st("$match", map[string]any{"status": "A"})})) ==
			dumpStages([]Stage{st("$match", map[string]any{"status": "A"}), st("$sort", map[string]any{"age": -1})}))
	check("R4 $skip 移到 $project 之前",
		Optimize([]Stage{st("$project", map[string]any{"a": 1}), st("$skip", map[string]any{"skip": 5})})[0].Name == "$skip")

	fmt.Println("[4] R5~R8 合并")
	s5 := Optimize([]Stage{st("$sort", map[string]any{"age": -1}), st("$skip", map[string]any{"skip": 10}), st("$limit", map[string]any{"limit": 5})})
	check("R5 $skip 夹中间:limit = 5+10 = 15", s5[0].Spec["limit"] == 15, dumpSpec(s5[0].Spec))
	check("R5 合并后仅剩 $sort + $skip", len(s5) == 2 && s5[1].Name == "$skip", dumpStages(s5))
	s5b := Optimize([]Stage{st("$sort", map[string]any{"age": -1}), st("$group", map[string]any{"_id": "$x"}), st("$limit", map[string]any{"limit": 5})})
	check("R5 中间有 $group 则不合并", len(s5b) == 3 && s5b[2].Name == "$limit", dumpStages(s5b))
	check("R6 相邻 $limit 取较小值",
		Optimize([]Stage{st("$limit", map[string]any{"limit": 100}), st("$limit", map[string]any{"limit": 10})})[0].Spec["limit"] == 10)
	check("R7 相邻 $skip 取和",
		Optimize([]Stage{st("$skip", map[string]any{"skip": 5}), st("$skip", map[string]any{"skip": 2})})[0].Spec["skip"] == 7)
	check("R8 相邻 $match 合并为 $and",
		Optimize([]Stage{st("$match", map[string]any{"a": 1}), st("$match", map[string]any{"b": 2})})[0].Spec["$and"] != nil)

	fmt.Println("[5] 幂等")
	check("Optimize 幂等", dumpStages(Optimize(out)) == dumpStages(out))

	fmt.Println("[6] ESR 谓词分类")
	check("精确值 → equality", classify("x") == "equality")
	check("$eq → equality", classify(map[string]any{"$eq": 1}) == "equality")
	small := []any{}
	for i := 0; i < inEqualityThreshold-1; i++ {
		small = append(small, i)
	}
	big := append([]any{}, small...)
	big = append(big, 999)
	check("$in 200 → equality", classify(map[string]any{"$in": small}) == "equality")
	check("$in 201 → range", classify(map[string]any{"$in": big}) == "range")
	check("$ne → range", classify(map[string]any{"$ne": 1}) == "range")

	fmt.Println("[7] ESR 索引选择(官方 movies 示例)")
	ixs := []Index{
		{Name: "directors_1_runtime_1_year_1", Fields: []string{"directors", "runtime", "year"}},
		{Name: "directors_1_year_1_runtime_1", Fields: []string{"directors", "year", "runtime"}},
	}
	q := map[string]any{"directors": "David Lynch", "runtime": map[string]any{"$lt": 130}}
	chosen := Choose(ixs, q, []string{"year"}, []string{"title", "year", "runtime"})
	check("选中 ESR 索引 directors_1_year_1_runtime_1", chosen.Index == "directors_1_year_1_runtime_1", chosen.Index)
	check("界为 [directors, runtime]", len(chosen.Bounds) == 2 && chosen.Bounds[0] == "directors" && chosen.Bounds[1] == "runtime", fmt.Sprint(chosen.Bounds))
	check("ESR 索引免内存排序", chosen.SortOK)
	ersOK := false
	for _, ix := range ixs {
		if p := Analyze(ix, q, []string{"year"}, nil); p.Index == "directors_1_runtime_1_year_1" {
			ersOK = !p.SortOK
		}
	}
	check("ERS 排布(range 在 sort 前)需要内存排序", ersOK)
	check("覆盖查询判定", Analyze(Index{Name: "a_1_b_1", Fields: []string{"a", "b"}}, map[string]any{"a": 1}, []string{"b"}, []string{"a", "b"}).Cover)
	check("缺字段 → 非覆盖", !Analyze(Index{Name: "a_1_b_1", Fields: []string{"a", "b"}}, map[string]any{"a": 1}, []string{"b"}, []string{"a", "z"}).Cover)
	check("无可用界 → 无候选索引", Choose(ixs, map[string]any{"zzz": 1}, nil, []string{"zzz"}).Index == "")

	fmt.Println("[8] 结构校验")
	check("$out 非末位 → 报错", len(Validate([]Stage{st("$out", map[string]any{"into": "t"}), st("$match", map[string]any{})})) == 1)
	check("$merge 居末 → 通过", len(Validate([]Stage{st("$match", map[string]any{}), st("$merge", map[string]any{"into": "t"})})) == 0)
	check("$geoNear 非首位 → 报错", len(Validate([]Stage{st("$match", map[string]any{}), st("$geoNear", map[string]any{})})) == 1)

	fmt.Println("")
	fmt.Printf("断言总数 %d,失败 %d\n", pass+fail, fail)
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
