// ReFormat demo 自检入口：go run . （自检失败即 panic）
package main

import "fmt"

// fragDefs：(函数名, 指令条数, 其中算术与位运算的条数)
var fragDefs = []struct {
	name string
	n    int
	nab  int
}{
	{"ssl3_read_bytes", 40, 26},
	{"EVP_DecryptUpdate", 30, 27},
	{"AES_decrypt", 60, 55},
	{"sha1_block_asm_data_order", 50, 48}, // ← 真正的解密收尾
	{"HMAC_Init_ex", 30, 5},
	{"http_parse_request", 80, 8},
	{"build_response", 40, 4},
	{"AES_encrypt", 60, 55}, // ← 输出加密，AB 占比同样很高
}

func buildITrace() []Inst {
	tr, sid := []Inst{}, 0
	for _, d := range fragDefs {
		sid++
		for i := 0; i < d.n; i++ {
			tr = append(tr, Inst{d.name, sid, i < d.nab})
		}
	}
	return tr
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + " " + detail)
	}
	fmt.Printf("  ok  %-50s %s\n", label, detail)
}

func main() {
	decMin, plainMax := 100.0, 0.0
	for _, r := range table1 {
		p := pct(r.ab, r.tot)
		if decryptNames[r.name] {
			if p < decMin {
				decMin = p
			}
		} else if p > plainMax {
			plainMax = p
		}
	}
	check("解密算法占比全部 > 80%", decMin > 80, fmt.Sprintf("min=%.2f", decMin))
	check("明文处理占比全部 < 25%", plainMax < 25, fmt.Sprintf("max=%.2f", plainMax))

	tr := buildITrace()
	trans, imax, imin := phaseProfile(tr, threshold)
	check("累计百分比最大值落在解密阶段", imax == 0, fmt.Sprintf("imax=%d", imax))
	check("累计百分比最小值落在处理阶段", imin > 180, fmt.Sprintf("imin=%d", imin))
	check("跃迁片段 = sha1_block_asm_data_order",
		trans != nil && trans.Func == "sha1_block_asm_data_order", trans.Func)
	check("跃迁点 = 该片段最后一条指令 = 179", trans.End-1 == 179, fmt.Sprint(trans.End-1))
	check("片段级百分比 = 96%", trans.Pct-96.0 < 1e-9 && 96.0-trans.Pct < 1e-9, "")
	same := map[string]bool{}
	for _, t := range []float64{25, 50, 80} {
		f, _, _ := phaseProfile(tr, t)
		same[f.Func] = true
	}
	check("阈值 25/50/80 得到同一跃迁片段", len(same) == 1 && same["sha1_block_asm_data_order"], "")
	f97, _, _ := phaseProfile(tr, 97)
	check("阈值 97% 时无片段达标", f97 == nil, "")

	T := trans.End - 1
	ops := []Op{
		{10, "alloc", "A"}, {12, "write", "A"},
		{20, "alloc", "B"}, {22, "write", "B"}, {30, "free", "B"},
		{40, "alloc", "C"}, {42, "write", "C"},
		{50, "alloc", "D"}, {52, "write", "D"},
		{60, "alloc", "E"},
		{200, "read", "C"}, {210, "read", "A"},
		{220, "alloc", "F"}, {222, "read", "F"},
		{230, "write", "D"},
	}
	ws, rs, ans := dataLifetime(ops, T)
	check("write set = [A C D]", fmtList(ws) == "[A C D]", fmtList(ws))
	check("read set = [A C F]", fmtList(rs) == "[A C F]", fmtList(rs))
	check("交集按首次读取时序 → [C A]", fmtList(ans) == "[C A]", fmtList(ans))
	fmt.Println("ReFormat(Go): ALL ASSERTIONS PASSED")
}
