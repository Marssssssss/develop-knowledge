package main

import (
	"fmt"
	"math/bits"
	"strconv"
	"strings"
)

// irq_affinity.go — IRQ 亲和性(smp_affinity 位掩码 / smp_affinity_list)与
// /proc/stat 的 intr、softirq 两行解析。
//
// 与 irq_check.go 同属 package main;解析 /proc/interrupts 等文件的部分在 irq_parse.go。

// ---------------------------------------------------------------- IRQ 亲和性

// ParseSmpAffinity 十六进制位掩码 -> 整数。支持 'f' / '0x3' / 'ffffffff,ffffffff'。
// 逗号分组时【低位组在前】:第 1 组代表 CPU 0..31,第 2 组代表 32..63。
func ParseSmpAffinity(text string) (uint64, error) {
	s := strings.ToLower(strings.TrimSpace(text))
	s = strings.ReplaceAll(s, "0x", "")
	if s == "" {
		return 0, fmt.Errorf("空的 affinity")
	}
	var mask uint64
	for i, g := range strings.Split(s, ",") {
		v, err := strconv.ParseUint(strings.TrimSpace(g), 16, 64)
		if err != nil {
			return 0, err
		}
		mask |= v << (32 * uint(i))
	}
	return mask, nil
}

// MaskToCPUs 位掩码 -> CPU 列表(升序)。
func MaskToCPUs(mask uint64) []int {
	var out []int
	for i := 0; i < bits.Len64(mask); i++ {
		if mask>>uint(i)&1 == 1 {
			out = append(out, i)
		}
	}
	return out
}

// FormatCPUList [0,1,2,3,7] -> "0-3,7"。
func FormatCPUList(cpus []int) string {
	var parts []string
	for i := 0; i < len(cpus); {
		j := i
		for j+1 < len(cpus) && cpus[j+1] == cpus[j]+1 {
			j++
		}
		if j == i {
			parts = append(parts, strconv.Itoa(cpus[i]))
		} else {
			parts = append(parts, fmt.Sprintf("%d-%d", cpus[i], cpus[j]))
		}
		i = j + 1
	}
	return strings.Join(parts, ",")
}

// ParseSmpAffinityList '0-1' / '0,3' / '1024-1031' -> CPU 列表。这是给人看的接口,
// 避免掩码要写 32 个零。
func ParseSmpAffinityList(text string) ([]int, error) {
	var out []int
	for _, part := range strings.Split(strings.TrimSpace(text), ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if i := strings.Index(part, "-"); i >= 0 {
			lo, err1 := strconv.Atoi(part[:i])
			hi, err2 := strconv.Atoi(part[i+1:])
			if err1 != nil || err2 != nil {
				return nil, fmt.Errorf("区间非法: %q", part)
			}
			for v := lo; v <= hi; v++ {
				out = append(out, v)
			}
		} else {
			v, err := strconv.Atoi(part)
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
	}
	return out, nil
}

// CheckAffinity 内核不允许把所有 CPU 都关掉。返回错误说明,合法则返回 nil。
func CheckAffinity(mask uint64) error {
	if mask == 0 {
		return fmt.Errorf("不能把 affinity 设成全 0 —— 内核会拒绝,这个 IRQ 会无核可用")
	}
	return nil
}

// ---------------------------------------------------------------- /proc/stat

// ParseProcStat 取出 intr 与 softirq 两行。结构一样:第一个数是总数,后面是分项。
// 但口径不同:intr 的总数【大于】分项之和(未编号架构向量只计入总数),
// 而 softirq 的总数【严格等于】分项之和。
func ParseProcStat(text string) map[string][]int {
	out := map[string][]int{}
	for _, line := range strings.Split(text, "\n") {
		tok := strings.Fields(line)
		if len(tok) == 0 || (tok[0] != "intr" && tok[0] != "softirq") {
			continue
		}
		vals := make([]int, 0, len(tok)-1)
		for _, t := range tok[1:] {
			v, err := strconv.Atoi(t)
			if err != nil {
				break
			}
			vals = append(vals, v)
		}
		out[tok[0]] = vals
	}
	return out
}

