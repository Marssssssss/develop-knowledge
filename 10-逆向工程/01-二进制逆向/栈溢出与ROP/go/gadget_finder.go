// gadget_finder.go —— ROP gadget 扫描器（含非对齐 gadget 的成因演示）
//
// 反汇编器只在"指令边界"上解码；而 ROP 攻击者可以从**任意字节**开始解码。
// x86 是密集变长指令集（1-15 字节、无对齐要求），因此从每个 0xC3（ret）
// 向前回溯若干个字节，往往能拼出编译器从未打算生成的合法序列 ——
// 这就是 "unintended / unaligned gadget"，也是可用 gadget 数量远超预期的原因。
//
// 用法：
//   go run gadget_finder.go            # 扫描内置示例代码段
//   go run gadget_finder.go file.bin   # 扫描真实二进制文件
//
// 参考：Shacham, "The Geometry of Innocent Flesh on the Bone" (CCS 2007)
package main

import (
	"encoding/hex"
	"fmt"
	"os"
	"sort"
	"strings"
)

// maxGadgetLen：回溯窗口。真实工具（ROPgadget / ropper）默认回溯到上一个
// 明确的分支/调用指令，这里简化为固定窗口。
const maxGadgetLen = 8

const retOpcode = 0xC3

// pattern 描述一个以 ret 结尾的指令序列的字节编码
type pattern struct {
	name  string
	bytes []byte
}

// 常见的"有用" gadget。字节编码取自 Intel SDM 的整数指令编码表。
var patterns = []pattern{
	{"pop rdi; ret", []byte{0x5F, 0xC3}},
	{"pop rsi; ret", []byte{0x5E, 0xC3}},
	{"pop rdx; ret", []byte{0x5A, 0xC3}},
	{"pop rax; ret", []byte{0x58, 0xC3}},
	{"pop rbp; ret", []byte{0x5D, 0xC3}},
	{"xor eax, eax; ret", []byte{0x31, 0xC0, 0xC3}},
	{"add rsp, 8; ret", []byte{0x48, 0x83, 0xC4, 0x08, 0xC3}},
	{"leave; ret (stack pivot)", []byte{0xC9, 0xC3}},
	{"mov [rdi], rsi; ret", []byte{0x48, 0x89, 0x37, 0xC3}},
	{"syscall; ret", []byte{0x0F, 0x05, 0xC3}},
	{"inc rdi; ret", []byte{0x48, 0xFF, 0xC7, 0xC3}},
	{"ret", []byte{0xC3}},
}

// gadget 是一次成功匹配的结果
type gadget struct {
	addr       uint64
	off        int
	name       string
	onBoundary bool // 起点是否落在"编译器认定的指令边界"上
}

func (g gadget) String() string {
	tag := "unaligned"
	if g.onBoundary {
		tag = "aligned  "
	}
	return fmt.Sprintf("  0x%06x (+%3d)  %-26s %s", g.addr, g.off, g.name, tag)
}

// boundaries 模拟 objdump 给出的"真实指令起始偏移"集合。
// 只有落在这个集合里的 gadget 才是编译器"打算"生成的。
func boundariesOf(code []byte) map[int]bool {
	// 简化模型：把代码段切成 4 字节对齐的伪指令，起点即视为合法边界。
	// 真实场景下这份集合来自一次线性/递归下降反汇编。
	b := make(map[int]bool, len(code)/4+1)
	for i := 0; i < len(code); i += 4 {
		b[i] = true
	}
	return b
}

// findGadgets 穷举所有以 0xC3 结尾、长度不超过 maxGadgetLen 的已知模式
func findGadgets(code []byte, base uint64) []gadget {
	bound := boundariesOf(code)
	var out []gadget

	for end := 0; end < len(code); end++ {
		if code[end] != retOpcode {
			continue
		}
		lo := end - maxGadgetLen + 1
		if lo < 0 {
			lo = 0
		}
		// 同一个 ret 可能被多个长度不同的模式命中；都收进来
		for start := lo; start <= end; start++ {
			seq := code[start : end+1]
			for _, p := range patterns {
				if len(p.bytes) != len(seq) {
					continue
				}
				if bytesEqual(seq, p.bytes) {
					out = append(out, gadget{
						addr:       base + uint64(start),
						off:        start,
						name:       p.name,
						onBoundary: bound[start],
					})
				}
			}
		}
	}

	sort.Slice(out, func(i, j int) bool { return out[i].addr < out[j].addr })
	return dedupe(out)
}

// dedupe 同一个地址只保留"信息量最大"的那条（名字最长的）
func dedupe(in []gadget) []gadget {
	best := map[uint64]gadget{}
	order := []uint64{}
	for _, g := range in {
		cur, ok := best[g.addr]
		if !ok {
			best[g.addr] = g
			order = append(order, g.addr)
			continue
		}
		if len(g.name) > len(cur.name) {
			best[g.addr] = g
		}
	}
	sort.Slice(order, func(i, j int) bool { return order[i] < order[j] })
	out := make([]gadget, 0, len(order))
	for _, a := range order {
		out = append(out, best[a])
	}
	return out
}

func bytesEqual(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// canBuildExecveChain 检查"拼出 execve('/bin/sh',0,0)"所需的关键 gadget 是否齐备
func canBuildExecveChain(gs []gadget) (ok bool, missing []string) {
	need := []string{"pop rdi; ret", "pop rsi; ret", "pop rdx; ret", "syscall; ret"}
	have := map[string]bool{}
	for _, g := range gs {
		have[g.name] = true
	}
	for _, n := range need {
		if !have[n] {
			missing = append(missing, n)
		}
	}
	return len(missing) == 0, missing
}

// 内置示例：一段"看起来只有加法"的代码，但其中夹着不少 0xC3 字节。
// 注意其中的 5F C3 / 5E C3 并非编译器生成的指令边界 —— 它们是数据/立即数字节。
const sampleHex = `
48 83 c4 08 c3 5f 5e 5a 58
31 c0 c3 0f 05 c3 c9 c3
48 89 37 c3 55 48 89 e5 5d c3
ba 5f c3 00 00 4c 89 d0 b8
0f 05 c3 90 90 90 90 c3`

func loadSample() ([]byte, uint64, string, error) {
	// 去掉全部空白后再做 hex 解码
	compact := strings.Join(strings.Fields(sampleHex), "")
	b, err := hex.DecodeString(compact)
	return b, 0x401000, "内置示例代码段（.text 中假定基址 0x401000）", err
}

func loadFile(path string) ([]byte, uint64, string, error) {
	b, err := os.ReadFile(path)
	return b, 0x401000, "文件 " + path, err
}

func main() {
	var (
		code []byte
		base uint64
		desc string
		err  error
	)

	if len(os.Args) > 1 {
		code, base, desc, err = loadFile(os.Args[1])
		if err != nil {
			fmt.Fprintf(os.Stderr, "读取失败: %v\n", err)
			os.Exit(1)
		}
	} else {
		code, base, desc, err = loadSample()
		if err != nil {
			fmt.Fprintf(os.Stderr, "示例解码失败: %v\n", err)
			os.Exit(1)
		}
	}

	fmt.Printf("扫描目标：%s（%d 字节，基址 0x%x）\n\n", desc, len(code), base)

	gs := findGadgets(code, base)

	aligned, unaligned := 0, 0
	perName := map[string]int{}
	for _, g := range gs {
		if g.onBoundary {
			aligned++
		} else {
			unaligned++
		}
		perName[g.name]++
	}

	fmt.Printf("共发现 %d 个 gadget（按地址去重后）\n", len(gs))
	fmt.Printf("  - 落在编译器认定的指令边界上：%d\n", aligned)
	fmt.Printf("  - 非对齐（unintended gadget）：%d\n\n", unaligned)

	fmt.Println("全部命中：")
	for _, g := range gs {
		fmt.Println(g.String())
	}

	fmt.Println("\n按类型统计：")
	names := make([]string, 0, len(perName))
	for n := range perName {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		fmt.Printf("  %-26s x%d\n", n, perName[n])
	}

	fmt.Println("\n链可行性检查（execve(\"/bin/sh\", NULL, NULL)）：")
	if ok, missing := canBuildExecveChain(gs); ok {
		fmt.Println("  ✅ 所需 gadget 齐备，原则上可拼出一条 execve 链")
	} else {
		fmt.Println("  ❌ 缺少：", strings.Join(missing, ", "))
		fmt.Println("     真实场景下会转向更大体积的模块（libc 通常能补上全部缺口）")
	}

	fmt.Println("\n结论：")
	fmt.Println("  · 大量 gadget 的起点并非编译器生成的指令边界 —— 它们由数据/立即数")
	fmt.Println("    字节偶然拼成，这使可用 gadget 数量远大于\"故意\"生成的那些。")
	fmt.Println("  · 因此 NX 只挡代码注入；防御 ROP 必须转向地址随机化与返回地址完整性")
	fmt.Println("    （ASLR/PIE、Full RELRO、CET SHSTK）。")
}
