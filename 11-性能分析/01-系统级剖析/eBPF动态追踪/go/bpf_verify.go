// bpf_verify.go — eBPF 寄存器类型验证器(Go 版,教学用)
//
// 与 Python 版的分工:这里强调【验证器的类型格与调用约定】——
//   * 寄存器类型:Uninit -> Scalar / PtrToStack / PtrToMapValue
//   * r10 只读帧指针;栈区间固定 [-512, -1];LDX 指针在 src,STX 指针在 dst
//   * helper 调用会把 r1-r5 打成 Uninit(调用者保存),r6-r9 跨调用保持(被调用者保存)
//   * 反向跳转必须能证明有界循环,否则拒绝
//
// 权威依据:docs.kernel.org/bpf/standardization/instruction-set.html、
//           ebpf.io/what-is-ebpf(验证器四条保证:不崩溃 / 不越界 / 运行至完成 / 复杂度有限)
//
// 运行: go run .   (同目录 bpf_insn.go 提供编码与汇编工具)
package main

import "fmt"

// ---------------- 寄存器类型格 ----------------
type RegType int

const (
	Uninit RegType = iota
	Scalar
	PtrToStack
	PtrToMapValue
)

type RegState struct {
	Type RegType
	Off  int16 // 仅 PtrToStack 有意义:相对 r10 的偏移
}

// State 是抽象解释的一个状态点;PC + 全部寄存器类型构成去重键
type State struct {
	PC  int
	Reg [11]RegState
}

func (s State) key() string {
	k := fmt.Sprintf("%d", s.PC)
	for _, r := range s.Reg {
		k += fmt.Sprintf("|%d:%d", r.Type, r.Off)
	}
	return k
}

type verifier struct {
	prog []Ins
	err  string
}

func (v *verifier) fail(format string, a ...interface{}) {
	if v.err == "" {
		v.err = fmt.Sprintf(format, a...)
	}
}

func (v *verifier) verify() (bool, string) {
	if len(v.prog) == 0 || v.prog[len(v.prog)-1].Class() != JMP ||
		v.prog[len(v.prog)-1].Code() != BPFExit {
		return false, "last instruction must be EXIT"
	}
	var init State
	init.Reg[FP] = RegState{Type: PtrToStack, Off: 0} // r10 指向栈顶
	worklist := []State{init}
	seen := map[string]bool{}
	for len(worklist) > 0 {
		st := worklist[len(worklist)-1]
		worklist = worklist[:len(worklist)-1]
		if st.PC >= len(v.prog) {
			return false, fmt.Sprintf("fallthrough off the end at pc=%d", st.PC)
		}
		if seen[st.key()] {
			continue // 不动点:该状态已分析过
		}
		seen[st.key()] = true
		in := v.prog[st.PC]
		next := st
		next.PC = st.PC + 1

		// ① r10 是只读帧指针
		if (in.Class() == ALU || in.Class() == ALU64) && in.Dst == FP {
			v.fail("pc=%d: r10 is a read-only frame pointer", st.PC)
		}

		// ② 读的寄存器必须先初始化:MOV 只读 src;CALL/JA 不读 dst
		switch {
		case in.Class() == JMP:
			if in.Code() != BPFCall && in.Code() != BPFJa &&
				st.Reg[in.Dst].Type == Uninit {
				v.fail("pc=%d: use of uninitialized r%d", st.PC, in.Dst)
			}
		case in.Class() == ALU || in.Class() == ALU64:
			if in.Code() != BPFMov && st.Reg[in.Dst].Type == Uninit {
				v.fail("pc=%d: use of uninitialized r%d", st.PC, in.Dst)
			}
		}
		if in.Source() == BPFX && in.Class() != STX && st.Reg[in.Src].Type == Uninit {
			v.fail("pc=%d: use of uninitialized r%d", st.PC, in.Src)
		}

		// ③ 栈访存:LDX 指针在 src,STX 指针在 dst;STX 还要读待存的 src
		if in.Class() == LDX || in.Class() == STX {
			ptr := in.Src
			if in.Class() == STX {
				ptr = in.Dst
				if st.Reg[in.Src].Type == Uninit {
					v.fail("pc=%d: use of uninitialized r%d", st.PC, in.Src)
				}
			}
			base := st.Reg[ptr]
			if base.Type == Uninit {
				v.fail("pc=%d: use of uninitialized r%d", st.PC, ptr)
			}
			if base.Type == PtrToStack {
				lo, hi := -StackSize, -in.Size()
				if int(in.Offset) < lo || int(in.Offset) > hi {
					v.fail("pc=%d: stack access %d outside [-512,-1]", st.PC, in.Offset)
				}
			}
		}

		if v.err != "" {
			return false, v.err
		}

		writeType := func(r uint8, t RegType, off int16) {
			if r <= FP { // 编码里 regs 字段是 4 bit,越界的寄存器号直接忽略
				next.Reg[r] = RegState{Type: t, Off: off}
			}
		}

		switch in.Class() {
		case ALU, ALU64:
			if in.Code() == BPFMov {
				switch {
				case in.Source() == BPFK:
					writeType(in.Dst, Scalar, 0)
				case st.Reg[in.Src].Type == Scalar:
					writeType(in.Dst, Scalar, 0)
				default: // 拷贝指针:保留类型与偏移(mov r2, r10 得到另一个栈指针)
					writeType(in.Dst, st.Reg[in.Src].Type, st.Reg[in.Src].Off)
				}
			} else {
				writeType(in.Dst, Scalar, 0)
			}
		case LDX:
			if st.Reg[in.Src].Type == PtrToStack {
				writeType(in.Dst, Scalar, 0)
			} else {
				writeType(in.Dst, PtrToMapValue, 0)
			}
		case STX:
			// 写内存,不改寄存器类型
		case JMP:
			switch in.Code() {
			case BPFExit:
				if st.Reg[0].Type == Uninit {
					v.fail("pc=%d: EXIT with uninitialized r0", st.PC)
				}
				continue
			case BPFCall:
				// helper 是稳定 ABI,但按调用约定打掉 r1-r5(r6-r9 保持)
				next.Reg[0] = RegState{Type: Scalar}
				for r := 1; r <= 5; r++ {
					next.Reg[r] = RegState{Type: Uninit}
				}
			case BPFJa:
				if in.Offset < 0 {
					if ok, why := boundedLoop(v.prog, st.PC); !ok {
						return false, fmt.Sprintf("pc=%d: %s", st.PC, why)
					}
				}
				target := next
				target.PC = st.PC + 1 + int(in.Offset)
				worklist = append(worklist, target)
				continue
			default:
				// 条件跳转两条路径都走;反向即循环,必须能证明有界
				if in.Offset < 0 {
					if ok, why := boundedLoop(v.prog, st.PC); !ok {
						return false, fmt.Sprintf("pc=%d: %s", st.PC, why)
					}
				}
				branch := next
				branch.PC = st.PC + 1 + int(in.Offset)
				worklist = append(worklist, branch)
			}
		}
		worklist = append(worklist, next)
	}
	if v.err != "" {
		return false, v.err
	}
	return true, "ok"
}

// boundedLoop:反向跳转前必须存在对被计数寄存器的 SUB imm(常量上界)
func boundedLoop(prog []Ins, pc int) (bool, string) {
	target := pc + 1 + int(prog[pc].Offset)
	for i := target; i < pc; i++ {
		in := prog[i]
		if in.Class() != ALU64 || in.Code() != BPFSub || in.Source() != BPFK {
			continue
		}
		for j := target - 4; j < target; j++ {
			if j < 0 {
				continue
			}
			m := prog[j]
			if m.Class() == ALU64 && m.Code() == BPFMov && m.Source() == BPFK && m.Dst == in.Dst {
				return true, ""
			}
		}
	}
	return false, "unbounded loop: no decrementing loop counter"
}

// ---------------- 测试程序 ----------------
func progOK() []Ins {
	p := []Ins{movImm(6, 0), movImm(7, 64), movImm(8, 0)}
	loop := len(p)
	p = append(p, aluImm(BPFAdd, 8, 1), aluImm(BPFSub, 7, 1))
	p = append(p, jmpImm(BPFJne, 7, int16(loop-(len(p)+1)), 0))
	p = append(p, stxDW(FP, -8, 8), ldxDW(9, FP, -8), movReg(0, 9), exitIns)
	return p
}

func progUseAfterHelper() []Ins {
	return []Ins{movImm(1, 0), call(5), movReg(0, 1), exitIns}
}

func progStackOOB() []Ins {
	return []Ins{movImm(1, 1), stxDW(FP, -520, 1), movImm(0, 0), exitIns}
}

func progWriteFP() []Ins {
	return []Ins{aluImm(BPFAdd, FP, 1), movImm(0, 0), exitIns}
}

func progUnbounded() []Ins {
	return []Ins{movImm(0, 0), ja(-1), exitIns}
}

func main() {
	cases := []struct {
		name string
		prog []Ins
		want bool
	}{
		{"count_loop(合法)", progOK(), true},
		{"r1_use_after_helper_call", progUseAfterHelper(), false},
		{"stack_oob([r10-520])", progStackOOB(), false},
		{"write_readonly_r10", progWriteFP(), false},
		{"unbounded_loop(ja -1)", progUnbounded(), false},
	}

	fmt.Println("=== eBPF 指令编码(8 字节定长,小端)===")
	for _, c := range cases[:2] {
		fmt.Printf("  %s: %d 条指令\n", c.name, len(c.prog))
		for i, in := range c.prog {
			raw := in.Encode()
			fmt.Printf("    pc=%d opcode=0x%02x class=%d code=0x%x dst=r%d src=r%d off=%d imm=%d"+
				" bytes=% x 往返=%v\n", i, in.Opcode, in.Class(), in.Code(), in.Dst, in.Src,
				in.Offset, in.Imm, raw, decode(raw) == in)
		}
	}

	fmt.Println("\n=== 验证器(寄存器类型格 + helper 调用约定)===")
	pass := 0
	for _, c := range cases {
		v := &verifier{prog: c.prog}
		ok, why := v.verify()
		if ok == c.want {
			pass++
		}
		verdict := "REJECT"
		if ok {
			verdict = "PASS"
		}
		fmt.Printf("  %-26s -> %-6s (%s) [期望 %v]\n", c.name, verdict, why, c.want)
	}
	fmt.Printf("\n%d/%d 用例符合预期\n", pass, len(cases))
	fmt.Println("提示:验证器是 safety tool——它保证\"程序本身跑起来安全\",")
	fmt.Println("      不保证\"程序在干什么\",后者要靠安全检查与权限模型。")
}
