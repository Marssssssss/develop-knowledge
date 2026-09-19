# 线程局部存储（TLS）

> `__thread int x;` 背后是一整套 ABI：一个"模块号 + 块内偏移"的二元组、一张每线程独立的 DTV、以及**四种代价相差极大的访问模型**。本 demo 把四种模型落成可执行实现，并断言它们**必须算出同一个地址**。

## 一、简介

TLS 的困难不在"每个线程一份"这个想法，而在 **变量所在模块的加载时机不确定**：

- 可执行文件里的 TLS 变量，偏移在**链接期**就能定死；
- 启动时就已加载的共享库，偏移可以在**启动期**由动态链接器算好；
- `dlopen` 进来的模块，偏移只能在**运行时**现算。

于是有了四种访问模型（文档 §4）：**General Dynamic / Local Dynamic / Initial Exec / Local Exec**。编译器按可见性挑一种，链接器还可能再做松弛优化——但松弛不是本文档覆盖的范围（见 §八.5）。

本 demo 覆盖：

1. 模块编号规则与 DTV 结构（§3.1）
2. `__tls_get_addr` 与延迟分配（§3.2）
3. x86-64 上四种模型的指令序列、重定位与 GOT 使用量（§4.1.6 / 4.2.6 / 4.3.6 / 4.4.6）
4. 四种模型的可用性边界

## 二、原理详解

### 2.1 模块编号与 DTV（§3.1）

文档原文给的定义：

> Given the dynamic thread vector data structure we can define the module reference as an integer **starting with 1 (one)** which can be used to index the `dtvt` array. … Only the executable itself must receive a fixed number, **1 (one)**, and all other loaded modules must have different numbers.

于是每个线程持有一个 `dtv`，`dtv[m]` 指向模块 m 在本线程的 TLS 块基址；`dtv[0]` 是 generation counter（真实实现用它判断 dtv 是否因新增模块而过期）。

### 2.2 `__tls_get_addr` 与延迟分配（§3.2）

文档直接给了原型：

```c
void *__tls_get_addr (size_t m, size_t offset)
{
  char *tls_block = dtv[thread_id][m];
  return tls_block + offset;
}
```

加上**延迟分配**后变成：

```c
void *__tls_get_addr (size_t m, size_t offset)
{
  char *tls_block = dtv[thread_id][m];
  if (tls_block == UNALLOCATED_TLS_BLOCK)
    tls_block = dtv[thread_id][m] = allocate_tls (m);
  return tls_block + offset;
}
```

这解释了为什么动态模型"每次访问都要调一次函数"：它得先确认块已经存在。也解释了为什么**静态 TLS 区的块不走这条路**——它们在线程创建时就已铺好（`dtv[1]` 一开始就有效）。

文档 §3 还区分了 **variant I / variant II** 两种布局：前者 TLS 块在线程指针之上、偏移为正；后者在之下、偏移为负。x86-64 属后者，所以后面看到的 TPOFF 都是负数。

### 2.3 General Dynamic（§4.1.6）

```text
0x00 .byte 0x66                       ; data16 前缀
0x01 leaq x@tlsgd(%rip),%rdi          R_X86_64_TLSGD      x
0x08 .word 0x6666
0x0a rex64
0x0b call tls_get_addr@plt            R_X86_64_PLT32      tls_get_addr

Outstanding Relocations:
  GOT[n]   R_X86_64_DTPMOD64   x      ← 模块 ID
  GOT[n+1] R_X86_64_DTPOFF64   x      ← 块内偏移
```

三条关键细节：

1. **两个 GOT 槽必须连续**（`GOT[n]` 与 `GOT[n+1]`），因为它们合起来是一个 `tls_index` 结构体。
2. **整段必须正好 16 字节**：文档原话是 "The instruction must be preceeded by a `data16` prefix and immediately followed by the call instruction at offset 0x08. The call instruction has to be preceeded by two `data16` prefixes and one `rex64` prefix to increase the total size of the whole sequence to 16 bytes."
3. **为什么用前缀而不是 nop 填充**："Prefixes and not no-op instructions are used since the former have no negative impact in the code." —— 填充用的是无副作用的指令前缀，不是会占用发射带宽的 nop。

### 2.4 Local Dynamic（§4.2.6）

```text
0x00 leaq x1@tlsld(%rip),%rdi   R_X86_64_TLSLD     x1
0x07 call tls_get_addr@plt      R_X86_64_PLT32     tls_get_addr
...
0x10 leaq x1@dtpoff(%rax),%rcx  R_X86_64_DTPOFF32  x1
...
0x20 leaq x2@dtpoff(%rax),%r9   R_X86_64_DTPOFF32  x2

Outstanding Relocations:
  GOT[n]   R_X86_64_DTPMOD64   x1
```

与 GD 的差别只有一处却很值钱：**`x@tlsld` 生成的 index 引用的是"当前模块、偏移为 0"**，因此链接器只需创建一个 `R_X86_64_DTPMOD64`，**`R_X86_64_DTPOFF64` 完全不需要**。

文档原话：

> The benefit of using the local dynamic model is that for every additional variable only three new instructions have to be added and **no additional GOT entries or run-time relocations**.

也就是说：**同模块 N 个变量，GD 是 N 次调用 + 2N 个 GOT 槽，LD 是 1 次调用 + 1 个 GOT 槽 + 3N 条指令**。文档甚至说 "it might be even preferable to use this model even for one variable"（只为了省掉那条运行时重定位的处理）。

### 2.5 Initial Exec（§4.3.6）

```text
0x00 movq %fs:0,%rax                        ; 取线程指针（x86-64 用 %fs）
0x09 addq x@gottpoff(%rip),%rax  R_X86_64_GOTTPOFF  x

Outstanding Relocations:
  GOT[n]   R_X86_64_TPOFF64   x
```

文档："The `R_X86_64_TPOFF64` relocation is processed **at program startup time** by the dynamic linker by looking up the symbol x in the modules loaded at that point."

所以 IE 的代价是：启动期一次符号查找（每模块每变量一次，之后不再发生），运行时只剩"一次段寄存器读 + 一次加法"。想直接取值而不是地址，还有一条等长的变体：

```text
0x00 movq x@gottpoff(%rip),%rax    R_X86_64_GOTTPOFF  x
0x07 movq %fs:(%rax),%rax
```

### 2.6 Local Exec（§4.4.6）

```text
0x00 movq %fs:0,%rax
0x09 leaq x@tpoff(%rax),%rax       R_X86_64_TPOFF32   x
```

**Outstanding Relocations: 空**。偏移作为 immediate 编码进指令，链接期定死。文档还给了两种等价写法：把 `leaq` 换成 `movq` 即"取值而非取址"，或压到一行 `movq %fs:x@tpoff,%rax`。

文档对 LE 的定性："is only an addition of the offset which is available as an immediate value to the thread pointer."

### 2.7 四种模型的可用性排序

| 模型 | 偏移何时定死 | GOT 槽/符号 | 访问路径调用 | 适用 |
| --- | --- | --- | --- | --- |
| GD | 运行时 | 2 | 1 次 | 一切场景（含 dlopen） |
| LD | 运行时（只算模块） | 0（额外变量） | 1 次/模块 | 本模块内、非静态区 |
| IE | 启动期 | 1 | 0 | 启动时已加载的模块 |
| LE | 链接期 | 0 | 0 | 只有可执行文件 |

**"同一个变量，四条路径必须给出同一个地址"** —— 这是本 demo 的核心断言。任何一条不等，就意味着某个模型把"模块基址"和"块内偏移"的折算搞反了。

## 三、对比

| 维度 | 静态 TLS 区 | 动态分配区 |
| --- | --- | --- |
| 成员 | 可执行文件的 TLS 块 | `dlopen` 进来的模块 |
| 何时有 | 线程创建时就有 | 首次访问（延迟分配） |
| 地址 | `TP + delta`（variant II 下为负） | `allocate_tls` 给 |
| 可用模型 | 四种全可用 | 只能 GD / LD |

## 四、环境

- Python 3.8+（标准库）、C99 编译器；无真实二进制依赖

## 五、运行方式

```bash
python tls_check.py                                    # 9 组断言
gcc -std=c99 -Wall -o /tmp/tls tls_models.c && /tmp/tls
```

## 六、关键代码

```python
def tls_get_addr(self, module, offset):
    block = self.dtv.get(module.id, Thread.UNALLOCATED)
    if block is Thread.UNALLOCATED:
        block = self.allocate_tls(module)      # §3.2 的延迟分配分支
    return block + offset
```

```python
# 四条路径必须殊途同归：这是最有鉴别力的一条断言
check(gd == ld == ie == le,
      "GD/LD/IE/LE 四条路径同一地址：gd=0x%x ld=0x%x ie=0x%x le=0x%x" % (gd, ld, ie, le))
```

## 七、性能边界

- **GD 的每次访问 = 一次 PLT 调用 + 一次函数内查表 + 可能的分配检查**。在紧循环里访问 TLS 变量，GD 与 LE 可以差一个数量级——这也是 CMake 里 `CMAKE_POSITION_INDEPENDENT_CODE` 与 `-ftls-model` 会显著影响性能的原因。
- **LD 的收益随变量数线性增长**，且省的是**运行时重定位的处理**（`R_X86_64_DTPOFF64` 需要动态链接器在每次 `dlopen` 后重新处理）。
- **IE 把成本从"每次访问"搬到了"启动时一次"**：模块越多、变量越多，启动时的 `TPOFF64` 处理就越重；这与 `-z now` 的取舍是同构的。
- **延迟分配意味着首次访问可能触发 `malloc`**，所以在信号处理器里访问动态模块的 TLS 变量是有风险的——这是文档没有明说、但由"首次才分配"这一条直接推出的结论。
- 本 demo 的 `allocate_tls` 用 `bytearray` 模拟内存，纯为可断言，**不是性能模型**。

## 八、注意事项与常见坑

1. **模块号从 1 开始**，不是 0；0 号槽让给了 generation counter。把可执行文件记成 0 会让所有 `dtv` 查找整体错一位。
2. **`DTPMOD` 与 `DTPOFF` 是两件不同的事**：前者是"哪个模块"，后者是"模块块内偏移"。LD 只需要前者。
3. **`TPOFF` 与 `DTPOFF` 也是两件不同的事**：`TPOFF` 相对**线程指针**，`DTPOFF` 相对**模块块起始**。二者不可互换。
4. **`%fs:0` 读到的是线程指针本身**（x86-64），所以 IE/LE 的第一步永远是 `movq %fs:0,%rax`。
5. **TLS 松弛（GD → IE / LE）不在本文档范围内**：这是 GNU ld 的扩展优化，文档（v0.20, 2005）里没有对应章节；本 demo 因此也不实现它，只实现"哪个模型可用"的静态判定。
6. **变体与架构强相关**：本文档里 s390/s390x/SPARC/Alpha/SH 各有自己的序列，照 x86-64 的写法套到别的架构上必然出错。
7. **延迟分配 ≠ 线程安全免费**：`__tls_get_addr` 的分配路径需要自身可重入保护；这不是本 demo 建模的部分。

## 九、参考资料（本轮实测下载并提取正文）

- [Ulrich Drepper — ELF Handling For Thread-Local Storage, Version 0.20 (2005-12-21)](https://www.uclibc.org/docs/tls.pdf) — §3.1 模块编号、§3.2 `__tls_get_addr` 与延迟分配、§3 variant I/II、§4.1.6 GD、§4.2.6 LD、§4.3.6 IE、§4.4.6 LE，各模型"Outstanding Relocations"逐条列出
- [System V AMD64 psABI Draft 0.99.6 Table 4.10](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf) — `R_X86_64_TLSGD=19 / TLSLD=20 / DTPMOD64=16 / DTPOFF64=17 / DTPOFF32=21 / GOTTPOFF=22 / TPOFF64=18 / TPOFF32=23` 的编号与 `R_X86_64_TLSDESC` 的语义
