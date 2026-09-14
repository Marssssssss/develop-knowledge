# 栈溢出与 ROP（返回导向编程）

## 简介

栈缓冲区溢出是漏洞利用的"入门第一课"，而 ROP 是它面对现代防护（NX/DEP）时的续命方案。本 demo 把这条链条拆成三段可观察的事实：**栈帧布局**（为什么溢出能改写返回地址）→ **NX 与 ASLR 的作用边界**（为什么 code injection 死了但 code reuse 没死）→ **ROP 链的执行模型**（`ret` 如何变成解释器的分派循环）。

关键概念：

- **栈帧**：`[局部变量][保存的 rbp][返回地址][第 7 个及以后的参数]`
- **NX / DEP / W^X**：内存页可写与可执行二者不可兼得，注入 shellcode 失效
- **gadget / ROP 链**：以 `ret` 结尾的短指令序列是 ROP 的"指令"；把它们 + 数据伪造在栈上就是一整条链，`rsp` 就是它的程序计数器
- **CET SHSTK**：硬件影子栈，`ret` 时比对栈上返回地址，直接让 ROP 链失效

历史背景：1996 年 Aleph1 的 *Smashing The Stack For Fun And Profit* 把栈溢出写成了教科书；1997 年 Solar Designer 提出 `ret2libc`（不注入代码，直接返回 `system()`）。2007 年 Hovav Shacham 证明：只要代码量足够大，gadget 组合是**图灵完备**的——"W^X 防护远没有想象中有用"。

## 原理详解

### 1. 进程内存布局与栈生长方向

```
   高地址 ┌──────────────┐
          │    Stack     │  ← 向低地址生长（Intel/Motorola/SPARC/MIPS 的典型）
          ├──────────────┤
          │  Heap        │  ← brk(2) 向高地址扩张
          ├──────────────┤
          │ bss / data   │  静态变量（未初始化 / 已初始化）
          ├──────────────┤
          │    Text      │  代码 + 只读数据，写入即段错误
   低地址 └──────────────┘
```

栈底在**固定地址**，`sp` 指向栈顶。栈帧随函数调用压入、随返回弹出。

### 2. x86-64 栈帧与参数传递

System V AMD64 ABI 规定：**前 6 个整型/指针参数走寄存器** `rdi, rsi, rdx, rcx, r8, r9`，**第 7 个起才上栈**。因此 64 位下的栈帧是：

```
高地址
   ┌────────────────────────┐
   │ 第 7+ 个参数            │ ← 调用者压栈
   ├────────────────────────┤
   │ 返回地址 (call 压入)    │ ← 溢出攻击的靶点
   ├────────────────────────┤
   │ 保存的 rbp (SFP)        │ ← 帧指针链：调试器据此回溯调用栈
   ├────────────────────────┤ ← 进入函数时 rbp 指向此处
   │ 局部变量 / 数组缓冲区   │   （现代编译器多为 rsp 相对，rbp 可省略）
   ├────────────────────────┤
   │ red zone（128 字节）    │ ← rsp 以下，叶子函数可直接用，无需调整 rsp
低地址
```

**red zone** 是 AMD64 ABI 独有的优化：`rsp` 以下的 128 字节保证不会被信号/中断处理程序异步破坏，于是**叶子函数**可以把整个栈帧放在这里，省掉 prologue/epilogue 两条指令。代价是：一旦发生函数调用，red zone 立刻被踩。

返回值 < 8 字节走 `rax`；`call` 压入的是 **8 字节**返回地址（32 位下是 4 字节，这是两个架构栈布局最大的差异）。

### 3. 溢出为什么能改写返回地址

```c
void function(char *str) {
    char buffer[16];      /* 16 字节，但 strcpy 不检查长度 */
    strcpy(buffer, str);  /* 拷贝到遇到 '\0' 为止 */
}
```

`buffer` 紧邻 SFP 与 RET。写入 256 字节时，`buffer` 之后的 240 字节被顺次覆盖：先 SFP，再 RET。函数返回时 `ret` 从栈上弹出这个被改写的值到 `rip`，控制流就被劫持了——**攻击者改的不是指令，是"下一条指令在哪"**。

### 4. NX/DEP 关掉了什么、没关掉什么

| 防护 | 机制 | 挡住了什么 | 没挡住什么 |
| --- | --- | --- | --- |
| NX / DEP | 页表 NX 位：W^X | 栈/堆上注入的 shellcode 被执行（触发 fault） | 把 `rip` 指向**已在可执行页里的现成代码** |
| ASLR | 随机化模块基址 | 硬编码 gadget 地址 | 相对偏移不变；一次信息泄露即可重定位 |
| Stack Canary | 返回地址前放哨兵值，返回前校验 | 连续覆盖到 RET 的写法 | 只改写函数指针 / 数据（data-only attack） |
| RELRO | 保护 GOT | GOT 覆写 | 其他函数指针（vtable、回调） |

**关键洞察**：NX 只管"这块内存能不能当指令取"。"本来就是指令"的代码段永远可执行——ROP 就是在这个缺口里工作的。

### 5. ROP 链的执行模型

`ret` 在 x86-64 上就是 `pop rip`：

```
① 被劫持函数执行它自己的 ret  → 弹出栈上第一个地址，跳过去
② 该地址指向的 gadget（如 pop rdi; ret）执行
③ gadget 尾部的 ret 再弹下一个地址 → 跳到下一个 gadget
... 直到最后一个 gadget 返回进目标函数（如 execve）
```

一个典型的 `execve("/bin/sh", NULL, NULL)` 链：

```
  rsp → 0x4011a3  pop rdi; ret    → 下一槽 0x601060 = "/bin/sh"
  rsp → 0x401080  pop rsi; ret    → 下一槽 0x00000000 = NULL
  rsp → 0x4010d0  pop rdx; ret    → 下一槽 0x00000000 = NULL
  rsp → 0x7f...c0 execve          ← ret 直接跳进 libc 的 execve
```

**`rsp` 就是程序计数器**：每执行一次 `ret`，它前进 8 字节，指向"下一条指令的地址"。栈从数据变成了程序。

**为什么 gadget 取之不尽**：x86 是密集变长指令集（1-15 字节、无对齐要求），从 `ret` 字节（`0xC3`）向前回溯任意字节偏移，很可能拼出一段合法的、以 `ret` 结尾的指令序列。这些**非对齐 gadget（unintended gadgets）** 让可用 gadget 数量远超编译器"故意"生成的那些。工具 `ROPgadget` / `ropper` 就是穷举这件事。

### 6. 变体与对抗

| 技术 | 控制流载体 | 备注 |
| --- | --- | --- |
| ret2libc | 返回到 libc 函数 | 1997 Solar Designer；需知道 libc 基址 |
| ret2plt + 泄露 | 调用 `puts@plt(GOT 地址)` 打印 libc 运行时地址 | 绕过 ASLR：`libc_base = 泄露值 - 符号偏移` |
| SROP | `sigreturn` 系统调用（单条 `syscall; ret` gadget） | 伪造 signal frame 一次性设置**全部**寄存器 |
| JOP / Blind ROP | 间接 `jmp` 结尾的 gadget / 靠崩溃与否探知地址空间 | CET SHSTK 不管 `jmp`，需靠 IBT 挡；Blind ROP 针对 `fork` 型服务 |

**CET 的两道锁**：

- **SHSTK（影子栈）**：把返回地址另外存在用户态不可写的硬件保护页；`ret` 时比对普通栈上的目标是否与影子栈一致，不一致抛 `#CP`。伪造的链从未被 `call` 压入过，因此每一次 `ret` 都失败。
- **IBT**：间接跳转目标必须以 `ENDBR64`（`F3 0F 1E FA`）开头，否则抛 `#CP`。非 CET CPU 上 `ENDBR64` 就是无害的 `REP NOP`，因此二进制可向后兼容。

SHSTK 上线后攻击面转向**不劫持返回地址**的方向：数据型攻击（改 UID/权限标志/长度字段）、堆上的函数指针（vtable、回调）——这些影子栈保护不了。

## 对比 / 选型

| 场景 | 推荐做法 |
| --- | --- |
| 通用二进制加固 / 高危进程（浏览器、内核） | ASLR + PIE + Full RELRO + Stack Canary + `-fstack-protector-strong`；高危进程追加 CET SHSTK + IBT（`-fcf-protection=full`） |
| 检测 ROP / 研究教学 | 栈上返回地址完整性校验、异常返回地址启发式、`auditd` 监控异常 `execve`；gdb + pwntools `ROP()` + `checksec` 查看开关矩阵 |

## 环境准备

- 操作系统：Linux（C 版用 `__builtin_frame_address` 走 rbp 链，架构相关部分用条件编译保护）；C gcc ≥ 9（`-fno-omit-frame-pointer` 便于观察帧链）；Python 3.8+ / Go 1.18+
- 建议工具：`checksec`、`ROPgadget`、`objdump -d`

## 运行方式

```bash
# C —— 观察真实栈帧与帧指针链
gcc -O0 -fno-omit-frame-pointer -Wall -Wextra -pedantic stack_layout.c -o stack_layout
./stack_layout
# Python —— ROP 链执行模型（含影子栈对比）
python3 rop_chain_sim.py
# Go —— gadget 扫描器（含非对齐 gadget）
cd go && go run gadget_finder.go
```

## 关键代码片段

```c
/* stack_layout.c —— 用 rbp 链手工回溯调用栈（gdb bt 的底层） */
static void walk_frames(void *rbp, int depth) {
    for (int i = 0; i < depth; i++) {
        if (rbp == NULL || (uintptr_t)rbp < 0x1000) break;
        void **frame   = (void **)rbp;
        void  *next_rbp = frame[0];   /* [rbp+0] = 保存的上层 rbp */
        void  *ret_addr = frame[1];   /* [rbp+8] = 返回地址        */
        printf("  frame %d: rbp=%p  ret=%p\n", i, rbp, ret_addr);
        rbp = next_rbp;
    }
}
```

```python
# rop_chain_sim.py —— ret 即 pop rip；rsp 即程序计数器
def run_chain(stack: list[int], gadgets: dict[int, Gadget],
              shadow: bool = False) -> list[str]:
    rsp = 0                                  # 从栈顶开始，每步 ret 前进一个槽位
    log = []
    while rsp < len(stack):
        target = stack[rsp]; rsp += 1              # ret: pop rip
        if shadow and target not in legitimate_calls:
            log.append(f"#CP: 影子栈拒绝 {target:#x} —— ROP 链在此折断")
            break
        gadget = gadgets[target]
        log.append(gadget.describe())
        rsp += gadget.pop_count                    # gadget 内部 pop 消耗的栈槽
        if gadget.name == "execve":
            break
    return log
```

## 性能与边界

- 一次 `ret` 消耗 8 字节栈空间并做一次间接跳转，现代 CPU 的返回栈预测器（RSB）对"非配对 ret"预测失败，因此 ROP 链执行比正常代码慢一个数量级——但**能用**远比"快"重要；gadget 数量与二进制体积近似线性，libc 通常能提供数千个可用 gadget
- 本 demo 的模型是**教学抽象**（固定宽度槽位、无对齐/canary），不构成可用 exploit；栈帧布局还随优化级别变化：`-O2` 下 rbp 通常被省略（`-fomit-frame-pointer`），帧链回溯需改用 DWARF CFI（`.eh_frame`）

## 注意事项与常见坑

1. **"加了 NX 就安全了"** —— 现象：以为开 `-z noexecstack` 即高枕无忧。原因：NX 只挡代码注入，不挡代码重用。规避：同时开 ASLR + PIE + Full RELRO + canary，并考虑 CET。
2. **rbp 链回溯失败** —— 现象：`walk_frames` 输出乱码或立刻中断。原因：`-O2` 下 rbp 被当作通用寄存器复用。规避：编译加 `-fno-omit-frame-pointer`，或改读 `.eh_frame` 的 CFI 规则。
3. **`-fstack-protector` 只保护有数组的函数** —— 现象：明明开了 canary 还是被溢出。原因：默认 `-fstack-protector` 只对有 `char` 数组的函数插桩。规避：用 `-fstack-protector-strong` / `-all`。
4. **red zone 被函数调用踩掉** —— 现象：叶子函数里把数据放 `rsp-8` 后调用别的函数，值被改。原因：red zone 只在**不发生调用**时有效。规避：跨调用保存的数据必须显式 `sub rsp`。
5. **函数指针 16 字节对齐崩** —— 现象：ROP 链一切正确却在 libc 的函数序言 `movaps` 处崩。原因：System V ABI 要求 `rsp` 在 `call` 前 16 字节对齐，伪造链没算对。规避：链中插入一个额外的 `ret` gadget 微调 8 字节。
6. **ASLR 看似"随机但可预测"** —— 现象：地址低 12 位恒定。原因：页对齐使然。规避：泄露时利用这一点算基址。

## 参考资料（实际阅读过的权威来源）

- [Smashing The Stack For Fun And Profit — Aleph1, Phrack #49 file 14](http://phrack.org/issues/49/14.html) —— 进程 Text/Data/Stack 三段布局与栈生长方向的原文图示、prologue 三步（`push ebp` / `mov esp,ebp` / `sub esp,N`）、SFP/RET 在帧中的位置、`strcpy` 无边界拷贝导致 RET 被 `0x41414141` 覆盖的逐步推演，本 README 第 1、3 节来自此文
- [Stack frame layout on x86-64 — Eli Bendersky](https://eli.thegreenplace.net/2011/09/06/stack-frame-layout-on-x86-64) —— 前 6 个整型参数进 `rdi/rsi/rdx/rcx/r8/r9`、第 7 个起上栈、red zone 的 ABI 原文定义（"128-byte area beyond %rsp...shall not be modified by signal or interrupt handlers"）、叶子函数用 red zone 省掉 rsp 调整、`-fomit-frame-pointer` 与 DWARF CFI 的关系，第 2 节的栈帧图与 red zone 全部依据此文
- [Intel 64 and IA-32 SDM, Vol.2 — INT3 与 CET（SHSTK / IBT / ENDBR64）相关章节](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html) —— `ENDBR64` 编码 `F3 0F 1E FA`、阴影栈校验失败抛 `#CP`
- [Return-Oriented Programming — Wikipedia](https://en.wikipedia.org/wiki/Return-oriented_programming) —— gadget 定义（"instruction sequences already present in executable memory"）、`ret` 链的分派机制、JOP/COP 变体、CFI 与影子栈作为对策；**此页直连被阻断，本 README 依据其内容摘要（检索结果中的原文引用段落）整理，未能逐行核对原文**
- [The Geometry of Innocent Flesh on the Bone: Return-into-libc without Function Calls (on the x86) — Hovav Shacham, CCS 2007](https://hovav.net/ucsd/dist/geometry.pdf) —— 形如 `pop rdi; ret` 的 gadget 组合在图灵完备意义下的表达能力、"W-xor-X defense is much less useful than previously thought"这一结论的原始出处；通过检索摘要读到其 abstract 与 introduction 引文
- [Return-Oriented Programming 教学综述（含 ROPgadget / ret2plt / SROP / Blind ROP / CET 对拍表）](https://datafield.dev/assembly-language/part-07/chapter-37/key-takeaways.html) —— 第 6 节各变体与缓解措施对照表的素材来源；属教学整理类资料，仅用于补充细节，核心结论均以上述 official/paper 来源为准
