# ELF 重定位计算

> x86-64 下**重定位(relocation)**的三步：类型编号 → Calculation 公式 → 落地字节。本 demo 把 psABI Table 4.10 的 38 条类型逐条落成可执行实现，外加 Elf64_Rela 记录编解码与写回。

## 一、简介

编译产物的目标文件里，凡是**编译期定不下来**的地址（外部符号、GOT/PLT 槽、TLS 变量、加载基址相关的绝对地址）都不会直接写成数字，而是留出一段空白 **+ 一条重定位记录**，由链接器（`ld`）或动态链接器（`ld.so`）按下表的公式补写。

理解重定位的价值在于：逆向时你在 `objdump -dr` / `readelf -r` 里看到的每一条 `R_X86_64_*`，本质上都是一道**会被执行的小算式**。把算式读懂，`lea x@tlsgd(%rip)`、`call foo@plt` 这类表面看不出跳转目标的指令就能还原出真实目标。

本 demo 覆盖：

1. Elf64_Rela 记录的二进制布局（`r_offset` / `r_info` / `r_addend`）
2. psABI 八个记号 A / B / G / GOT / L / P / S / Z 的精确语义
3. Table 4.10 全部 38 条类型 + Table 4.11 大代码模型 5 条
4. 计算结果如何按 word8/16/32/64 宽度小端写回镜像

## 二、原理详解

### 2.1 Elf64_Rela：为什么 x86-64 只有 RELA

psABI §4.4.1 原文一句定死：

> The AMD64 ABI architectures uses only Elf64_Rela relocation entries with explicit addends. The r_addend member serves as the relocation addend.

也就是说 x86-64 **没有** REL 那种「加数藏在被重定位的 4/8 字节里」的形式，加数永远来自显式字段 `r_addend`。实际布局：

| 字段 | 宽度 | 含义 |
| --- | --- | --- |
| `r_offset` | 8 | 要改的位置（虚拟地址；`.o` 里是相对所在节的偏移） |
| `r_info` | 8 | **高 32 位 = 符号表索引，低 32 位 = 重定位类型** |
| `r_addend` | 8 | 有符号加数 A |

注意 `r_info` 的高低位是 **32 + 32** 而不是 64 位挤压——这是 ELF64 与 ELF32 同名结构的第二处差异（第一处是 `sizeof(Elf64_Rela) == 24`）。

### 2.2 八个记号

| 记号 | psABI 原文 | 直白解释 |
| --- | --- | --- |
| `A` | the addend used to compute the value of the relocatable field | 就是 `r_addend` |
| `B` | the base address at which a shared object has been loaded into memory | DSO 的加载基址；可执行文件通常不涉及 |
| `G` | the offset into the global offset table at which the relocation entry's symbol will reside | **GOT 内的偏移**（不是地址） |
| `GOT` | the address of the global offset table | GOT 的绝对地址 |
| `L` | the place of the Procedure Linkage Table entry for a symbol | 符号对应 PLT 表项的地址 |
| `P` | the place of the storage unit being relocated (computed using r_offset) | **被改的那个字节自己的地址** |
| `S` | the value of the symbol whose index resides in the relocation entry | 符号的值（通常是其虚拟地址） |
| `Z` | the size of the symbol | 符号的 `st_size` |

最常见的混淆是 **G vs GOT**：`R_X86_64_GOT32 = G + A` 得到的是「符号在 GOT 里的偏移」这个量本身，而 `R_X86_64_GOTPCREL = G + GOT + A - P` 才是「从当前 PC 走到那个 GOT 槽需要多少位移」。

### 2.3 Table 4.10 摘录（本 demo 完整实现全部 38 条）

| Name | Value | Field | Calculation |
| --- | --- | --- | --- |
| R_X86_64_64 | 1 | word64 | S + A |
| R_X86_64_PC32 | 2 | word32 | S + A - P |
| R_X86_64_PLT32 | 4 | word32 | L + A - P |
| R_X86_64_COPY | 5 | none | none |
| R_X86_64_GLOB_DAT | 6 | word64 | S |
| R_X86_64_JUMP_SLOT | 7 | word64 | S |
| R_X86_64_RELATIVE | 8 | word64 | B + A |
| R_X86_64_GOTPCREL | 9 | word32 | G + GOT + A - P |
| R_X86_64_GOTOFF64 | 25 | word64 | S + A - GOT |
| R_X86_64_TLSDESC | 36 | word64×2 | （无通用公式） |
| R_X86_64_IRELATIVE | 37 | word64 | indirect (B + A) |

**"Calculation 留空"不等于"值是 0"**。这是本 demo 刻意用 `(value, ok bool)` / `None` 双返回值建模的原因：0 是一个完全合法的计算结果，不能兼作哨兵。具体留空的那些类型各有各的走法：

- **16～23（TLS 系）**：由动态链接器在**启动期**处理，`R_X86_64_DTPMOD64`（模块 ID）、`R_X86_64_DTPOFF64`（块内偏移）、`R_X86_64_TPOFF64`（相对线程指针偏移）三者在 `__tls_get_addr` 调用序列里各司其职。
- **34～36（TLSDESC 系）**：psABI 原文：

  > `R_X86_64_TLSDESC` resolves to a pair of word64s, called TLS Descriptor, the first of which is a pointer to a function, followed by an argument. The function is passed a pointer to the this pair of entries in `%rax` and … must compute and return in `%rax` the offset from the thread pointer to the symbol referenced in the relocation, **without modifying any registers other than processor flags**.

  这条约束解释了为什么 resolver 必须是汇编写的、且要严格遵守 callee-saved：它是被**插在热路径中间**调用的，被调用方完全不知道它存在。
- **5（COPY）**：`.so` 与可执行文件同名符号冲突时的兜底——把符号的初始值**整块拷**进可执行文件自己的 `.bss`，再把引用指过去；所以 Field 是 none（不是"改某个字段"，而是"搬一段内存"）。

### 2.4 IRELATIVE 与 IFUNC

> `R_X86_64_IRELATIVE` is similar to `R_X86_64_RELATIVE` except that the value used in this relocation is the program address returned by the function, which takes no arguments, at the address of the result of the corresponding `R_X86_64_RELATIVE` relocation.

一句话：**`B + A` 不是最终答案，而是一个"不接收参数、返回真正的目标地址"的解析器函数的地址**。先把 `B+A` 算出来，再调用它，把返回值写进 `r_offset`。这就是 `STT_GNU_IFUNC` 的落地mechanism——glibc 用它做 `memcpy` 的 CPU 特性派发。

### 2.5 写回：宽度、字节序与"能塞进去"的两种口径

字段宽度取自 Figure 4.1：`word8=1B`、`word16=2B`、`word32=4B`、`word64=8B`，一律**小端**。落地前要过两道独立的检查，二者不可混为一谈：

1. **位宽检查 `Fits`**：值取模后能不能放进这么多字节。用于 `R_X86_64_64` 这类**绝对地址**。
2. **有符号位移检查 `Signed32OK`**：能不能放进 `[-2^31, 2^31-1]`。用于 `PC32` / `PLT32` 这类**相对位移**。

典型坑：`0x80000000` 能塞进 4 字节（`Fits` 为真），但作为有符号位移是越界的（`Signed32OK` 为假）。只看前者会让 `call` 的偏移量错出一个符号位。这正是大模型/大 GOT 需要 Table 4.11（`R_X86_64_GOTPLT64` 等）的原因。

## 三、对比

| 维度 | ELF64 x86-64 | ELF32 i386（对照） |
| --- | --- | --- |
| 记录格式 | **只有 `Elf64_Rela`，24 字节，加数显式** | `Elf32_Rel`（8 字节，隐式加数）与 `Elf32_Rela`（12 字节）混用 |
| `r_info` 拆分 | 高 32 位符号 / 低 32 位类型 | 高 24 位符号 / 低 8 位类型 |
| PC 相对 | 因有 RIP 相对寻址，一条指令即可从 GOT 取值（`GOTPCREL` 与 `GOT32` 语义**刻意不同**） | 需要先取 GOT 基址再算 |
| 大代码模型 | 另有 Table 4.11 五条 64 位 GOT/PLT 重定位 | 极少涉及 |

psABI 对 `GOTPCREL` 的这段说明很关键：

> because the AMD64 architecture has an addressing mode relative to the instruction pointer, it is possible to load an address from the GOT using a single instruction.

## 四、环境

- Python 3.8+（只用到标准库 `struct`）
- Go 1.17+（只用到标准库 `encoding/binary` / `math` / `sort`）
- 无需 root，不访问网络，不修改任何文件

## 五、运行方式

```bash
# Python 侧自检（10 组断言全部实跑）
python reloc_check.py

# Go 侧对照：打印 38 条类型的编号/字段/计算结果，以及两次写回演示
go run elf_reloc.go
```

`elf_reloc.py` 是纯模型；`reloc_check.py` 是自检入口（为守"单文件 ≤300 行"而拆开，断言逻辑与之同出一源）。

## 六、关键代码

```python
# Table 4.10 的一行 -> 三列齐活：编号、字段、可执行的 Calculation
RELOC_TABLE = {
    "R_X86_64_PC32":    (2,  "word32", "S + A - P", lambda r: r.sym + r.addend - r.place),
    "R_X86_64_GOTPCREL": (9, "word32", "G + GOT + A - P",
                          lambda r: r.got_off + r.got + r.addend - r.place),
    "R_X86_64_RELATIVE": (8, "word64", "B + A",    lambda r: r.base + r.addend),
}

def reloc_value(name, rel):
    """留空的类型返回 None —— 用 None 而不是 0：0 是合法结果，两者不可混为一谈。"""
    calc = (RELOC_TABLE.get(name) or LARGE_MODEL_TABLE[name])[3]
    return None if calc is None else calc(rel)
```

```go
// Go 侧同一张表。区别只有一处：Go 没有「返回 None」的写法，
// 所以用 (int64, bool) 显式区分「算出来是 0」与「没有通用公式」。
func RelocValue(name string, c RelocContext) (int64, bool) {
	fn, present := calcTable()[name]
	if !present {
		return 0, false
	}
	return fn(c), true
}
```

## 七、性能边界

- **计算本身是「查一次表 + 一次算术」**：O(1)，与镜像大小、符号数量无关。真正的开销在**符号解析 `S`**（哈希表查找）与 **TLS 首次访问的一次 `malloc`**。
- **`IRELATIVE` 每次进程启动都要跑一遍 resolver**，所以 glibc 才要求 IFUNC resolver 必须极轻（通常只读一次 CPUID 缓存标志），且**不得有副作用**——它可能被多次调用。
- **`.rela.dyn` 的处理是启动期串行热路径**：这就是 `DT_RELACOUNT`（连续的 `R_X86_64_RELATIVE`）与后来 RELR、以及 `-z now` 之间 trade-off 的来源：前者把相对重定位的遍历加速，后者把延迟绑定改成启动期全量绑定（牺牲启动时间换确定性）。
- 本 demo 的 `apply_reloc` 每次都会 `bytes` 切片拼接，是 O(len(image))；真实链接器/加载器是**原地改写**已映射的内存。此处是为了让"返回值可读"，不是性能示范。

## 八、注意事项与常见坑

1. **不要把「留空公式」当成「值是 0」**。留空的 16～23 与 34～36 各有专路，返回 None 才能发现自己没实现它们。
2. **`P` 的重音落在「被改的那个字节」**：写 `call foo@plt` 时 `A` 固定是 `-4`（x86 的 call 指令长度参与 rip 取值），初学者常把它当成 0，导致目标偏移整整差 4。
3. **G vs GOT**：看 「`G` 是偏移、`GOT` 是地址」这个对立。`R_X86_64_GOT32 = G + A` 得到的是 GOT 内偏移；要得到可用来取值的地址必须再加 `GOT`。
4. **`Fits` 不等于 `Signed32OK`**：本 demo 用 `0x80000000` 作为一组正负对照，两者结论相反。
5. **`R_X86_64_COPY` 看似无害实则危险**：它意味着符号定义在 `.so` 里、使用却在可执行文件里，**拷的是初始值的快照**，两侧后续修改互不可见——这是经典的「定义方与使用方分离两个地址」陷阱。
6. **RELA 不同于 REL**：x86-64 下 `r_addend` 永远是显式的，改它就改结果；照 i386 的习惯去"从被重定位的字节里读加数"会得到完全错误的值。
7. **`R_X86_64_JUMP_SLOT`（7）与 `R_X86_64_GLOB_DAT`（6）公式同为 `S`**，区别不在算法而在**何时**执行：前者默认走惰性绑定（首次调用才由 `_dl_runtime_resolve` 填），后者在启动期一次性填完。

## 九、参考资料（本轮实测下载并提取正文）

- [System V Application Binary Interface — AMD64 Architecture Processor Supplement, Draft 0.99.6 (July 2, 2012)](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf) — §4.4 Relocation、§4.4.1 Relocation Types（Table 4.10 / 4.11）、Figure 4.1 Relocatable Fields、八个记号 A/B/G/GOT/L/P/S/Z 的原文定义
- `elf(5)` — Linux man page（man7.org），Elf64_Rela 结构体布局与 `r_info` 拆分口径
- [Linkers part 4: Shared Libraries — Ian Lance Taylor](https://www.airs.com/blog/archives/41) — COPY 重定位与符号抢占的实际后果
