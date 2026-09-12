# ELF 文件解析(手工解析 Ehdr / Shdr / Sym)

> 逆向静态分析的第一步永远是"读懂文件格式"。本 demo 依据 `elf(5)` 手册页手工解析 ELF64 头部、节头表与符号表,不借助任何解析库 —— 这正是 IDA / Ghidra 载入 ELF 时幕后所做的事。

## 简介

- **实现**:`elf_parser.py`(Python 标准库版)+ `elf_parser.c`(C 版,逐字段等价)
- **语言**:Python / C
- **输入**:任意 ELF64 小端文件(`.o` / 可执行 / `.so`;Linux 上可直接解析 demo 自身)

## 原理详解

### 文件三层结构

```text
偏移 0      Elf64_Ehdr(64B, 固定)
e_phoff     程序头表(Elf64_Phdr × e_phnum, 每项 56B)   ← 内核加载器看的是它
e_shoff     节头表(Elf64_Shdr × e_shnum, 每项 64B)      ← 链接器/静态工具看的是它
```

**同一个文件有两张"目录"**:程序头描述"段"(LOAD 段,加载进内存的粒度),节头描述"节"(`.text/.data/.symtab`,链接与静态分析的粒度)。**逆向工具优先看节头表,而 strip 只删 `.symtab` 不删节头表本身** —— 这就是为什么 stripped 二进制仍能列出 `.text/.rodata` 却没有函数名。

### Elf64_Ehdr 关键字段(偏移依据 elf(5))

| 偏移 | 大小 | 字段 | 逆向意义 |
| --- | --- | --- | --- |
| 0x00 | 4 | 魔数 `\x7fELF` | 最快的文件类型嗅探 |
| 0x04 | 1 | EI_CLASS(2=64 位) | 决定后续所有结构体宽度 |
| 0x05 | 1 | EI_DATA(1=小端) | 决定多字节字段的解码方式 |
| 0x10 | 2 | e_type(2=EXEC/3=DYN) | DYN 且有 INTERP 段 → PIE 可执行;DYN 无 INTERP → 共享库 |
| 0x12 | 2 | e_machine(62=x86-64) | 选择反汇编器的指令集 |
| 0x18 | 8 | e_entry | 入口点地址(OEP 定位起点) |
| 0x28 | 8 | e_shoff / e_shnum / e_shstrndx | 节头表位置、项数、节名表索引 |

### Elf64_Shdr(每项 64B)与节名间接寻址

`sh_name` 不是字符串而是**在 `.shstrtab` 中的偏移**;`.shstrtab` 由 `e_shstrndx` 定位。这是 ELF 通用的"字符串表 + 偏移"模式,符号表同理(`.symtab` 的 `sh_link` 指向 `.strtab`)。

| 常见节 | 类型 | flags | 说明 |
| --- | --- | --- | --- |
| `.text` | PROGBITS | ALLOC+EXECINSTR | 代码,反汇编主战场 |
| `.data` / `.bss` | PROGBITS / NOBITS | WRITE | NOBITS **不占文件空间**,只占内存 |
| `.symtab` / `.strtab` | SYMTAB / STRTAB | 无 ALLOC | 完整符号表,strip 删除对象 |
| `.dynsym` / `.dynstr` | DYNSYM / STRTAB | ALLOC | 动态符号,**strip 后仍保留** |
| `.rela.plt` / `.got.plt` | RELA / PROGBITS | — | 延迟绑定,见同目录 GOT-PLT demo |

### Elf64_Sym(每项 24B)

`st_info` 一个字节打包两个属性:`bind = st_info >> 4`(LOCAL/GLOBAL/WEAK),`type = st_info & 0xF`(OBJECT/FUNC/SECTION/FILE)。**`STT_FILE` 符号(源文件名)之后到下一个 FILE 符号之前,都是该编译单元的 LOCAL 符号** —— 逆向时可用它恢复"这个二进制由哪些源文件构成"。

## 运行方式

```bash
# Python 版
python elf_parser.py /bin/ls
# C 版(Linux/macOS 编译)
gcc -O2 -o elf_parser elf_parser.c && ./elf_parser ./elf_parser
```

预期输出:ELF 头字段 → 节头表(名称/类型/flags/偏移)→ 前 25 个符号(bind/type/地址/名字)→ strip 判定。

## 关键代码说明

- `struct.unpack_from("<HHIQQQIHHHHHH", data, 16)`:Python 版用格式串逐字段解码 `e_ident` 之后的 48 字节,`<` 显式小端 —— 与文件字节序解耦。
- C 版 `memcpy(&eh, d, sizeof eh)` 之所以合法:小端主机上 ELF 字节布局即结构体内存布局;跨端需逐字段 `le16toh/le64toh`(常见坑,见下)。

## 性能边界

- 单次 `fread` 全量读入 + 线性遍历,复杂度 O(文件大小);百万级符号表(如含调试信息的 Chromium)会占内存 ≈ 文件大小,工业级实现(readelf)改为按需 `pread`。
- 未实现 e_shnum ≥ 0xff00 时的**溢出转存**(真实节数存节头表首项 `sh_size`,Linux 扩展)—— 节超过 65280 个的文件会解析失败。

## 注意事项与常见坑

1. **只查节头表会被"段内偏移"骗**:节头 `sh_offset` 是文件偏移,`sh_addr` 是链接视图虚拟地址;而 `e_shoff` 之后节头数组要求连续,手工解析器常忘记乘 `e_shentsize` 而非 `sizeof(Shdr)`(规范允许两者不等)。
2. **`.bss` 用 `sh_offset` 读会读到下一个节**:NOBITS 节不占文件空间,读它必须返回全零缓冲。
3. **大端固件**(某些 MIPS/PowerPC 路由器固件)`EI_DATA=2`,本 demo 显式拒绝而非输出乱码 —— 对未知格式先检查 `EI_CLASS/EI_DATA` 再解码。
4. **PIE 可执行与共享库在 e_type 上无法区分**(都是 DYN),需进一步看是否有 `PT_INTERP` 段。

## 参考资料(实际读过)

- [elf(5) — Linux user's manual(man7.org)](https://man7.org/linux/man-pages/man5/elf.5.html):Ehdr/Phdr/Shdr/Sym/Dyn 全部字段偏移、节类型与动态标签定义
- [readelf(1) 对照验证输出](https://man7.org/linux/man-pages/man1/readelf.1.html)
