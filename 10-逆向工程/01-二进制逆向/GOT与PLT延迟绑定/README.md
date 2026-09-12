# GOT 与 PLT 延迟绑定(惰性符号解析)

> 反汇编任何 Linux 二进制,最先撞上的就是 `call xxx@plt`。理解 PLT/GOT 三段式与惰性绑定,是看懂 ELF 动态链接、做 GOT 劫持检测与反分析对抗的分水岭。本 demo 用 Python 完整模拟首次调用的 5 步解析流程,C 侧提供供 `objdump` 观察的真实样本。

## 简介

- **实现**:`lazy_binding_sim.py`(状态机模拟器,任何平台可跑)+ `plt_subject.c`(被观察的真实程序,Linux 编译)
- **语言**:Python / C
- **依据**:Ian Lance Taylor《Linkers》part 4(i386 经典结构,思想与 x86-64 一致)+ `elf(5)` 的 JMP_SLOT / DT_JMPREL 定义

## 原理详解

### 为什么需要 PLT/GOT

共享库加载地址运行时才确定,代码段又必须保持只读且位置无关。解法:**把"跳到哪"这一可变信息从代码搬到数据**——代码里只写死一条 `jmp *GOT槽`,GOT 位于可写的 `.got.plt` 节,由动态链接器在运行期填。代价:每次跨库调用多一次内存间接跳转(Taylor 原文:每个全局函数调用在首次解析后多一条指令)。

### PLT 三段式结构(Taylor part 4 原文汇编)

```asm
PLT[n]:  jmp  *offset(%ebx)     ; ① 经 GOT[n+2] 间接跳(初始值=本条目第二条指令地址)
         push $index            ; ② 把自己的重定位索引压栈
         jmp  PLT[0]            ; ③ 跳公共解析入口
PLT[0]:  push 4(%ebx)          ; 压 link_map(GOT[1], 动态链接器加载时填)
         jmp  *8(%ebx)          ; 跳 _dl_runtime_resolve(GOT[2])
```

### 首次调用的 5 步(模拟器逐步演示)

1. `jmp *GOT[n+2]` → 槽里是"未解析"初值,**恰好指回本 PLT 条目的第二条指令**;
2. `push $index` → 重定位索引入栈(这是解析器唯一需要的线索);
3. `jmp PLT[0]` → 压 link_map、跳 `_dl_runtime_resolve`;
4. 解析器用 `(link_map, index)` 定位 `.rela.plt` 中的 JMP_SLOT 条目 → 取符号名 → 查 `.dynsym` 与各库符号表;
5. **回填 GOT[n+2] = 函数真实地址**,跳过去执行。此后调用在第 ① 步直达,解析器不再醒来。

### 关键档案(elf(5))

- `JMP_SLOT`:PLT 对应的 GOT 槽重定位类型;`GLOB_DAT`:普通数据引用的 GOT 重定位(**非惰性**,启动时全部解析)。
- `DT_JMPREL` / `DT_PLTRELSZ` / `DT_PLTREL`:惰性重定位表(`.rela.plt`)的位置、大小与类型 —— 动态链接器靠这三个动态标签而非节名定位它。
- `DT_BIND_NOW` / 环境变量 `LD_BIND_NOW`:关闭惰性,启动时全量解析(Full RELRO 强制之)。

### 逆向视角

- **`call func@plt` ≠ 已调用**:PLT 是声明,只有运行到才解析 —— hook 框架(frida Interceptor)、LD_PRELOAD 劫持都发生在 GOT 槽被填之后。
- **GOT 劫持/GOT overwrite**:攻击者把 GOT 槽改写为恶意函数,下次合法调用即被重定向;防御即 Full RELRO(绑定后把 `.got` 段 mprotect 为只读)。
- **识别入口点**:`__libc_start_main@plt` 的交叉引用几乎总能带出 `main` 的真实地址。

## 运行方式

```bash
python lazy_binding_sim.py          # 任意平台:完整 5 步 + BIND_NOW 对比
# C 样本(Linux/WSL):
gcc -no-pie -o plt_subject plt_subject.c
objdump -d -j .plt plt_subject      # PLT 条目三段式
objdump -R plt_subject | grep JUMP_SLOT   # 5 个 JMP_SLOT 对应 5 个外部函数
```

## 关键代码说明

- 模拟器把"GOT[0..2] 被 PLT0 占用、GOT[3..] 按序对应 PLT[0..]"这一布局编码进 `plt_entry()`;
- `self.resolved` 计数器量化惰性收益:5 次调用只唤醒解析器 2 次(printf 与 strcmp 首次),未调用的符号(如未触发的错误分支中的函数)永不付出解析成本 —— 这正是 glibc 数千个函数也能秒开进程的原因。

## 性能边界

- 现代发行版默认 **Full RELRO + BIND_NOW**(安全优先),惰性绑定在主流发行版上已被事实性禁用 —— 但理解它依旧是阅读老二进制、CTF 题与 RELRO 降级样本的必修课。
- `.plt.sec`(IBT/CET 硬件防护)与 x86-64 的 `endbr64` 前缀会让实际汇编与教科书三段式略有差异,不影响语义。

## 注意事项与常见坑

1. **x86-64 的 GOT 基址不再是 %ebx**:改用 RIP 相对寻址(`jmp *offset(%rip)`),PLT 第二段 push 的索引仍走栈;Taylor 的 %ebx 版本是 i386 叙事,看 x64 反汇编别找 %ebx。
2. **`.got` 与 `.got.plt` 是两张表**:前者 GLOB_DAT(启动即解析,RELRO 保护对象),后者 JMP_SLOT(惰性);Full RELRO 时代两者最终都只读。
3. **`objdump -R` 里看到的不是地址而是待填槽**:重定位表描述的是"哪里要填",不是"填了什么" —— 运行期才见分晓。
4. **静态链接二进制没有 PLT**:找不到 `.plt` 节时先 `file` 一下确认是不是 statically linked,别怀疑工具坏了。

## 参考资料(实际读过)

- [Linkers part 4: Shared Libraries — Ian Lance Taylor(airs.com/archives/41)](https://www.airs.com/blog/archives/41):PLT/GOT 全部机制、i386 汇编三段式、惰性绑定流程、LD_BIND_NOW
- [elf(5) — man7.org](https://man7.org/linux/man-pages/man5/elf.5.html):`.rela.plt` 命名约定、JMP_SLOT/GLOB_DAT、DT_JMPREL/DT_PLTRELSZ/DT_BIND_NOW 动态标签
- [Linkers 系列目录(20 篇,part 4-6 覆盖共享库与重定位)](https://www.airs.com/blog/archives/38)
