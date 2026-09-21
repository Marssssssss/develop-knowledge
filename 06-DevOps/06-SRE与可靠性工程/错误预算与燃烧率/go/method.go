package main

// OpenSLO v1 的三种 budgetingMethod,与 python/method.py 同构。
//
// 规范原文(实读):
//   - Occurrences method uses a ratio of counts of good events to the total count
//     of the events.
//   - Timeslices method uses a ratio of good time slices to total time slices in a
//     budgeting period.
//   - RatioTimeslices method uses an average of all time slices' success ratios in
//     a budgeting period.

import "fmt"

// Occurrences sum(good) / sum(total),按事件数加权。
func Occurrences(goodCounts, totalCounts []int) (float64, bool) {
	g, t := 0, 0
	for i := range totalCounts {
		g += goodCounts[i]
		t += totalCounts[i]
	}
	if t == 0 {
		return 0, false
	}
	return float64(g) / float64(t), true
}

// sliceRatios 每个切片的成功率;0 事件的切片用 ok=false 标记,既不按 0 也不按 1 计。
func sliceRatios(goodCounts, totalCounts []int) []struct {
	v  float64
	ok bool
} {
	out := make([]struct {
		v  float64
		ok bool
	}, len(totalCounts))
	for i := range totalCounts {
		if totalCounts[i] == 0 {
			out[i].ok = false
			continue
		}
		out[i].v = float64(goodCounts[i]) / float64(totalCounts[i])
		out[i].ok = true
	}
	return out
}

// Timeslices 先用 timeSliceTarget 把每个切片二值化,再 good_slices / total_slices。
// 规范给的 timeSliceTarget 取值域是 (0.0, 1.0]。
func Timeslices(goodCounts, totalCounts []int, timeSliceTarget float64) (float64, bool, error) {
	if !(timeSliceTarget > 0.0 && timeSliceTarget <= 1.0) {
		return 0, false, fmt.Errorf("timeSliceTarget must be in (0.0, 1.0]")
	}
	ratios := sliceRatios(goodCounts, totalCounts)
	good, usable := 0, 0
	for _, r := range ratios {
		if !r.ok {
			continue
		}
		usable++
		if r.v >= timeSliceTarget {
			good++
		}
	}
	if usable == 0 {
		return 0, false, nil
	}
	return float64(good) / float64(usable), true, nil
}

// RatioTimeslices 所有切片成功率的等权平均。
func RatioTimeslices(goodCounts, totalCounts []int) (float64, bool) {
	ratios := sliceRatios(goodCounts, totalCounts)
	sum, usable := 0.0, 0
	for _, r := range ratios {
		if !r.ok {
			continue
		}
		sum += r.v
		usable++
	}
	if usable == 0 {
		return 0, false
	}
	return sum / float64(usable), true
}

// AllMethods 一次算出三种口径便于对比。
func AllMethods(goodCounts, totalCounts []int, timeSliceTarget float64) (map[string]float64, error) {
	ts, okTS, err := Timeslices(goodCounts, totalCounts, timeSliceTarget)
	if err != nil {
		return nil, err
	}
	oc, okOC := Occurrences(goodCounts, totalCounts)
	rt, okRT := RatioTimeslices(goodCounts, totalCounts)
	out := map[string]float64{}
	if okOC {
		out["occurrences"] = oc
	}
	if okRT {
		out["ratio_timeslices"] = rt
	}
	if okTS {
		out["timeslices"] = ts
	}
	return out, nil
}
