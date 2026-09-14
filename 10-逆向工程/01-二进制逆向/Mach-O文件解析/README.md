# Mach-O 文件格式解析

## 简介

Mach-O 是 macOS / iOS 的可执行文件格式，与 ELF、PE 并列为三大主流目标文件格式。本 demo 手写解析器，把 `mach_header_64` → `load_command[]` → `segment_command_64` → `section_64` 这层层嵌套的结构完整拆解出来，并用一个**内置合成样本**保证在没有 macOS 的机器上也能运行。

关键概念：

- **mach_header_64**：固定 32 字节的文件头，`magic` 为 `0xFEEDFACF`（64 位、小端）
- **load command**：变长的"命令"数组，`ncmds` 条，总长 `sizeofcmds`
- **LC_SEGMENT_64**：把一个段映射进进程地址空间；段内可含多个节
- **LC_SYMTAB / LC_LOAD_DYLIB**：符号表位置、依赖的动态库清单
- **命名约定**：段名**全大写**（`__TEXT`），节名**全小写**（`__text`），都带两个下划线前缀；**节编号从 1 起且跨段连续**

历史背景：Mach-O 源自卡内基梅隆的 Mach 微内核（1980s），NeXTSTEP 采用，Apple 收购 NeXT 后一路沿用到今天的 macOS/iOS。它比 ELF 更"命令驱动"——文件头之后是一串自描述的 `load_command`，新增特性只需注册新命令码，不必改动头部结构。这也是 `LC_MAIN`、`LC_DYLD_CHAINED_FIXUPS` 这类新命令能在不破坏旧加载器的前提下加入的原因。

## 原理详解

### 1. 整体布局

```
0x0000 ┌────────────────────────┐
       │ mach_header_64  (32 B) │
0x0020 ├────────────────────────┤
       │ load_command #0        │  ncmds 条
       │ load_command #1  ...   │  合计 sizeofcmds 字节
       ├────────────────────────┤
       │ __TEXT / __DATA 段内容 │  ← 文件偏移对齐到页边界（4096）
       │ __LINKEDIT 段内容      │  ← 符号表 / 字符串表 / 重定位表
       └────────────────────────┘
```

### 2. mach_header_64（`<mach-o/loader.h>`）

```c
struct mach_header_64 {
   uint32_t magic;        /* MH_MAGIC_64 = 0xFEEDFACF；字节序相反时 MH_CIGAM_64 */
   cpu_type_t    cputype;    /* CPU_TYPE_X86_64 / CPU_TYPE_ARM64 */
   cpu_subtype_t cpusubtype;
   uint32_t filetype;     /* MH_OBJECT / MH_EXECUTE / MH_DYLIB / MH_BUNDLE / MH_DSYM ... */
   uint32_t ncmds;        /* 紧随其后的 load command 条数 */
   uint32_t sizeofcmds;   /* 这些 load command 的总字节数 */
   uint32_t flags;        /* MH_NOUNDEFS / MH_DYLDLINK / MH_TWOLEVEL / MH_PIE ... */
   uint32_t reserved;     /* 64 位独有，保留 */
};
```

| `filetype` | 含义 | 常见后缀 |
| --- | --- | --- |
| `MH_OBJECT` / `MH_EXECUTE` | 中间目标文件（所有节同一个段） / 可执行程序 | `.o` / 无 |
| `MH_BUNDLE` / `MH_DYLIB` | 运行时加载的插件 / 动态共享库 | `.bundle` / `.dylib` |
| `MH_DYLINKER` / `MH_CORE` / `MH_DSYM` | 动态链接器本身（dyld） / core dump / 独立符号文件 | — / `.dSYM` |

### 3. load command 通用头

```c
struct load_command {
   uint32_t cmd;       /* 命令类型，如 LC_SEGMENT_64 */
   uint32_t cmdsize;   /* 本命令的总字节数 */
};
```

**对齐规则**：32 位下 `cmdsize` 必须是 4 的倍数，**64 位下必须是 8 的倍数**，不足要补零。这是遍历命令数组时最容易踩的坑——按 `cmdsize` 步进时若忽略对齐会越界。

| 命令 | 结构 | 用途 |
| --- | --- | --- |
| `LC_SEGMENT_64` | `segment_command_64` | 定义映射到地址空间的段（64 位） |
| `LC_SYMTAB` / `LC_DYSYMTAB` | `symtab_command` / `dysymtab_command` | 符号表 + 字符串表位置 / dyld 用的附加符号信息 |
| `LC_LOAD_DYLIB` | `dylib_command` | 依赖的动态库（**顺序即库序号 ordinal**） |
| `LC_ID_DYLIB` / `LC_UNIXTHREAD` / `LC_UUID` | `dylib_command` / `thread_command` / `uuid_command` | 本文件作为 dylib 的安装名 / 主线程初始寄存器（老式入口声明） / 映像的 128 位 UUID（与 dSYM 配对） |

### 4. segment_command_64

```c
struct segment_command_64 {
   uint32_t cmd, cmdsize;
   char     segname[16];   /* 固定 16 字节，不足补 '\0' */
   uint64_t vmaddr;        /* 段的起始虚拟地址 */
   uint64_t vmsize;        /* 段占用的虚拟内存大小 */
   uint64_t fileoff;       /* 该段内容在文件中的偏移 */
   uint64_t filesize;      /* 该段在磁盘上的大小 */
   vm_prot_t maxprot;      /* 最大保护权限 */
   vm_prot_t initprot;     /* 初始保护权限 */
   uint32_t nsects;        /* 紧随其后的 section_64 个数 */
   uint32_t flags;         /* SG_HIGHVM / SG_NORELOC */
};
```

`cmdsize` = `sizeof(segment_command_64) + nsects * sizeof(section_64)`。`vmsize` 可以大于 `filesize`（例如 `__PAGEZERO`），加载器负责把多出来的部分清零。

### 5. section_64

```c
struct section_64 {
   char     sectname[16];  /* 如 "__text" */
   char     segname[16];   /* 该节归属的段名 */
   uint64_t addr, size;    /* 虚拟地址与大小 */
   uint32_t offset;        /* 文件偏移 */
   uint32_t align;         /* 对齐，以 2 的幂表示：align=3 → 8 字节 */
   uint32_t reloff, nreloc;/* 重定位表位置与条数 */
   uint32_t flags;         /* 低 8 位=节类型，高 24 位=属性 */
   uint32_t reserved1, reserved2, reserved3;
};
```

| 节类型（`flags & 0xFF`） | 含义 |
| --- | --- |
| `S_REGULAR` / `S_ZEROFILL` | 普通（`__TEXT,__text`） / 按需零填充（bss 类） |
| `S_CSTRING_LITERALS` / `S_SYMBOL_STUBS` | 仅含 C 字符串常量 / 符号 stub（跳板） |
| `S_LAZY_SYMBOL_POINTERS` / `S_NON_LAZY_SYMBOL_POINTERS` / `S_MOD_INIT_FUNC_POINTERS` | 惰性 / 非惰性符号指针 / 模块初始化函数指针（C++ 静态构造） |

节属性（`flags >> 8`）中 `S_ATTR_PURE_INSTRUCTIONS`（仅可执行指令）与 `S_ATTR_SOME_INSTRUCTIONS` 是判断"这段能不能当代码"的关键。

### 6. 段与节的命名约定

| 段 | 内容 | `filesize` 特点 |
| --- | --- | --- |
| `__PAGEZERO` | 首段，映射在虚拟地址 0，**无任何权限** | `vmsize` 很大，`filesize=0`（让解引用 NULL 立刻崩） |
| `__TEXT` | 代码 + 只读数据 | 禁止写入，可多进程共享，无需写回磁盘 |
| `__DATA` | 可写数据 | 允许读写，写时复制（COW） |
| `__OBJC` / `__LINKEDIT` | Objective-C 运行时数据 / dyld 用的原始数据 | —  |

常用节：`__TEXT,__text`（机器码）、`__TEXT,__cstring`（C 字符串）、`__TEXT,__const`、`__DATA,__data`、`__DATA,__bss`、`__DATA,__la_symbol_ptr`（惰性符号指针，等价于 ELF 的 GOT）、`__DATA,__mod_init_func`。**节编号从 1 开始**（不是 0），且跨段连续编号——这是与 ELF（`sh_index` 从 0 开始）的显著差异。

### 7. dylib_command 与库序号

```c
struct dylib_command {
   uint32_t cmd, cmdsize;
   struct dylib {
       union lc_str name;      /* 字符串表偏移（相对 load command 起点） */
       uint32_t timestamp;
       uint32_t current_version;
       uint32_t compatibility_version;
   } dylib;
};
```

**所有 `LC_LOAD_DYLIB` 按出现顺序构成有序列表**；两级命名空间（`MH_TWOLEVEL`）下，符号表条目 `nlist.n_desc` 存放的"库序号"就是索引这个列表的。运行时要成功加载，dylib 自身的兼容版本必须 **≤** 命令里声明的兼容版本。

## 对比 / 选型

| 维度 | Mach-O | ELF | PE |
| --- | --- | --- | --- |
| 描述方式 | 自描述 `load_command` 数组 | 段表 + 节表（两套头） | 节表 + 目录表 |
| 节编号 | **从 1 开始** | 从 0 开始 | 从 1 开始 |
| 惰性绑定载体 | `__DATA,__la_symbol_ptr` + stub | `.got.plt` | IAT |
| 符号/节名长度 | **固定 16 字节** | 任意（存 `.strtab`） | 8 字节 |
| 依赖库记录 | `LC_LOAD_DYLIB` 有序列表 + ordinal | `DT_NEEDED` 列表 | Import Directory |
| 解析难点 | 命令数组需按 8 字节对齐步进 | 节名表间接寻址 | RVA↔文件偏移换算 |

## 环境准备

- 操作系统：**任意**（解析器对内置合成样本运行，无需 macOS）；Python 3.8+（仅标准库 `struct`）；Go 1.18+
- 若在 macOS 上可与 `otool -l` / `otool -h` / `jtool2` 输出对拍

## 运行方式

```bash
python3 macho_parser.py                 # 解析内置合成样本
python3 macho_parser.py /usr/bin/ls     # 解析真实 Mach-O（仅 macOS）

cd go && go run macho_parser.go macho_defs.go           # 内置样本
cd go && go run macho_parser.go macho_defs.go ./a.out   # 指定文件
```

## 关键代码片段

```python
# 按 cmdsize 步进遍历 load command 数组 —— 64 位下 cmdsize 必须 8 字节对齐
def iter_load_commands(data: bytes, header: MachHeader):
    off = MAGIC_SIZE
    for _ in range(header.ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmdsize < 8 or cmdsize % 8 != 0:
            raise MachOError(f"cmdsize 非法（64 位需 8 字节对齐）: {cmdsize:#x}")
        yield off, cmd, cmdsize, data[off:off + cmdsize]
        off += cmdsize
```

## 性能与边界

- 遍历 `load_command` 是 O(ncmds)，每步 O(1)；解析全部节是 O(总节数)；节名固定 16 字节，**长名会被截断**（与 ELF 的 `.strtab` 任意长度不同）
- 本 demo 未实现：`LC_DYLD_CHAINED_FIXUPS`（现代 dyld 的链式绑定格式）、`LC_CODE_SIGNATURE` 代码签名校验、`LC_MAIN` 入口点（Apple 后续引入的新式入口声明）；大端 Mach-O（`MH_CIGAM_64`）仅作识别，不做字节序翻转解析

## 注意事项与常见坑

1. **cmdsize 不按 8 对齐** —— 现象：解析到第二条命令就全乱。原因：64 位 Mach-O 要求 `cmdsize` 为 8 的倍数。规避：步进前校验并对齐。
2. **`segname`/`sectname` 不一定有 `\0`** —— 现象：打印出乱码或越界。原因：固定 16 字节数组，短名才补 `'\0'`。规避：复制到 17 字节缓冲并强塞终止符。
3. **忘记 `__PAGEZERO`** —— 现象：把第一个段当成 `__TEXT` 导致偏移全错。原因：每个 `MH_EXECUTE` 的第一个段通常是 `__PAGEZERO`。规避：按 `segname` 匹配而非按序号。
4. **`vmsize > filesize` 属正常** —— 现象：以为文件被截断。原因：bss 类内容不占磁盘。规避：按 `filesize` 读文件，按 `vmsize` 算内存。
5. **误以为节编号从 0 开始** —— 现象：按 ELF 直觉算出的索引全部差 1。原因：Mach-O 节编号从 1 起且跨段连续。规避：用累加计数器。
6. **`lc_str` 偏移是相对 load command 起点** —— 现象：dylib 名字读成乱码。原因：`union lc_str` 存的是相对偏移，不是绝对地址。规避：以命令起始为基准相加。

## 参考资料（实际阅读过的权威来源）

- [OSX ABI Mach-O File Format Reference — aidansteele](https://github.com/aidansteele/osx-abi-macho-file-format-reference) —— `mach_header_64` 全字段与 `filetype`/`flags` 取值表、`load_command` 的 4/8 字节对齐规则、`segment_command_64` 与 `section_64` 完整字段语义、`__PAGEZERO/__TEXT/__DATA/__OBJC/__IMPORT/__LINKEDIT` 段说明、`__TEXT`/`__DATA` 各节对照表（`__text`/`__cstring`/`__const`/`__la_symbol_ptr`/`__mod_init_func` 等）、节类型 `S_*` 与属性 `S_ATTR_*` 位域、"节编号从 1 开始且跨段连续"、`symtab_command` 与 `dylib_command` 字段及"`LC_LOAD_DYLIB` 顺序即库序号、兼容版本需 ≤ 声明值"的规则——本 README 第 2-7 节全部依据此页
- 该参考文档的 `load_command` 表**未收录 `LC_MAIN`**（内容主体对应 OS X 10.0-10.3 时期，仍以 `LC_UNIXTHREAD` 声明入口），故本 demo 不解析 `LC_MAIN`/`entry_point_command`，也未在 README 中断言其字段；该页在 `dylib_module_64` 处被截断，`nlist_64` 与重定位条目的完整字段未取到，demo 中相应部分仅解析到 `LC_SYMTAB` 的偏移与计数为止
- [Apple Developer — Mach-O / `<mach-o/loader.h>` 官方头文件](https://developer.apple.com/documentation/) —— `MH_MAGIC_64`（`0xFEEDFACF`）等常量值以官方头文件为准；本 demo 按此约定实现
