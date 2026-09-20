# iOS 砸壳与加密镜像

## 简介

App Store 下的 iOS App 的 `__TEXT` 段是**加密**的：`otool -l` 里能看到 `LC_ENCRYPTION_INFO_64` 与一个非 0 的 `cryptid`，直接拿 IPA 里的 Mach-O 去反汇编只能看到密文。砸壳（dumpdecrypted、Clutch、bfinject、frida-ios-dump 干的都是这件事）的原理只有一句话：**进程跑起来之后，内核已经把 `__TEXT` 解密进了内存；从内存里把这段读出来写回文件，再把 `cryptid` 置 0**。

本目录按 xnu 源码 `EXTERNAL_HEADERS/mach-o/loader.h` 实现：

- `macho_dump.py` — Mach-O 解析、加密信息提取、dump 计划、写回与 patch cryptid
- `selfcheck_dump.py` — 56 项断言
- `macho_dump.go` / `macho_selfcheck.go` — 同构 Go 实现（本机无 Go 工具链，走静态校验）

## 原理详解

### 1. 三个关键字段

`loader.h` 里两个结构（`cryptid` 的注释原文：`which enryption system, 0 means not-encrypted yet`）：

```c
struct encryption_info_command {      /* LC_ENCRYPTION_INFO  = 0x21 */
   uint32_t cmd, cmdsize, cryptoff, cryptsize, cryptid;        /* 20 字节 */
};
struct encryption_info_command_64 {   /* LC_ENCRYPTION_INFO_64 = 0x2C */
   uint32_t cmd, cmdsize, cryptoff, cryptsize, cryptid;
   uint32_t pad;                      /* 补到 8 的倍数 */        /* 24 字节 */
};
```

| 字段 | 含义 |
| --- | --- |
| `cryptoff` | 加密区间的**文件偏移** |
| `cryptsize` | 加密区间的**文件长度** |
| `cryptid` | 用哪套加密体系；**0 表示尚未加密**（即已砸壳或本就未加密） |

`cryptid` 在命令内的偏移是 16（`cmd` + `cmdsize` + `cryptoff` + `cryptsize`），patch 就是往这里写 0。

### 2. 文件偏移 → 虚拟地址：最容易写错的一步

`cryptoff` 是**文件偏移**，而内存里解密后的数据位于

```
vmaddr = segment.vmaddr + (cryptoff - segment.fileoff)
```

加密区间总是落在 `__TEXT` 段内，所以要先找到包含 `cryptoff` 的段，再用段内偏移换算。拿 `cryptoff` 当 vmaddr 用、或者直接用 `vmaddr == fileoff` 的假设，都会 dump 出一段错误的数据——而这个错误在后续反汇编里表现为"开头几条指令对，后面全是乱的"，极难定位。

### 3. 页对齐

加密区间是按**页**解密的，所以 dump 必须覆盖包含它的**整个页**：起始向前扩到页首，结束向上取整到页尾。`page_aligned_range()` 实现了这条规则，**页大小由调用方传入**——iOS 在不同架构/机型上页大小并不统一，本目录不假定具体数值（官方 loader.h 未给出）。

### 4. 砸壳四步

1. 读 Mach-O，找到 `LC_ENCRYPTION_INFO_64`，确认 `cryptid != 0`；
2. 算出 dump 计划（段名 / 文件偏移 / 长度 / 对应的 vmaddr）；
3. 从进程内存把 `vmaddr - image_base` 处 `cryptsize` 字节读出来，写回文件的 `cryptoff` 处；
4. 把 `cryptid` 置 0，让内核不再尝试解密。

第 3 步之后**文件长度不变**——砸壳是覆盖，不是插入。

### 5. dyld 共享缓存里的库

`loader.h` 定义了 `MH_DYLIB_IN_CACHE = 0x80000000`，注释写明 "Only for use on dylibs"：置位表示该 dylib 在 dyld 共享缓存里，**磁盘上没有独立的加密段**。对系统库谈"砸壳"没意义，要做的是从共享缓存里把对应的 Mach-O 片段提取出来。

### 6. 相关 header 字段

`mach_header_64` 是 8 个 `uint32`（32 字节），magic 为 `MH_MAGIC_64 = 0xfeedfacf`；若读到 `MH_CIGAM_64 = 0xcffaedfe` 说明字节序被交换过。`MH_PIE = 0x200000` 表示启用 ASLR，砸壳工具的 `image_base` 必须按**实际加载基址**算，不能照抄 `vmaddr`。

## 对比：三种"拿到明文"的路径

| 路径 | 适用 | 代价 |
| --- | --- | --- |
| 从内存 dump + patch cryptid | 已越狱/可注入进程 | 需要能 attach；最通用 |
| 从 dyld 共享缓存提取 | 系统 dylib | 无加密，但要处理缓存的 slide 与片段 |
| 直接抓 IPA 里的 Mach-O | 未加密（`cryptid == 0`） | 无；但 App Store 包基本都是加密的 |

## 环境

- Python 3.8+（只用标准库 `struct`）
- Go 1.21+（本机无工具链，未实跑；已过括号配平 / 参数个数 / 交叉引用校验）
- 真机砸壳还需要越狱设备或可注入的调试通道（`frida-ios-dump` 走的是后者）

## 运行方式

```bash
python selfcheck_dump.py     # 56 项断言，全部通过
# Go（有工具链时）
go run .
```

## 关键代码

```python
# cryptoff 是文件偏移，内存地址要用所在段换算（砸壳最容易写错的一步）
def dump_plan(self):
    seg = next(s for s in self.segments if s.contains_fileoff(self.crypt["cryptoff"]))
    return {"file_offset": self.crypt["cryptoff"],
            "size": self.crypt["cryptsize"],
            "vmaddr": seg.fileoff_to_vmaddr(self.crypt["cryptoff"])}

# 覆盖写回，文件长度不变
def apply_dump(self, memory, image_vm_base, plan=None):
    start_mem = plan["vmaddr"] - image_vm_base
    for i in range(plan["size"]):
        self.blob[plan["file_offset"] + i] = memory[start_mem + i]

# cryptid 在命令内偏移 16，置 0 即"不再加密"
struct.pack_into("<I", self.blob, self.crypt_cmd_off + 16, 0)
```

## 性能与边界

- 砸壳只覆盖 `[cryptoff, cryptoff+cryptsize)`，其余字节必须逐字节不变；写错边界会把 `__TEXT` 前部的明文一起冲掉。
- `cryptsize` 通常不等于 `__TEXT` 的整个 `filesize`——只有部分区间被加密，别想当然按整段处理。
- 带 `__PAGEZERO`、`__LINKEDIT` 的胖二进制（Fat / Universal）要先挑出对应架构的那一份 Mach-O，本目录只处理单架构 64 位。
- 页大小不是常量：调用方必须按目标平台传入，本目录的 `page_aligned_range()` 显式接收它就是为了不把这个假设写死。

## 注意事项与常见坑

1. **`cryptoff` 是文件偏移不是 vmaddr** —— 必须经所在段换算，这是砸壳第一大坑。
2. **`image_base` 要用实际加载基址** —— `MH_PIE` 下每次加载都不同，照抄 `vmaddr` 会整体错位。
3. **页对齐要向外取整** —— 只覆盖 `[cryptoff, cryptoff+cryptsize)` 而不到整页边界，尾部会残留半页密文。
4. **`cryptid` 必须置 0** —— 否则加载器还会再解一次密，砸完的二进制跑不起来。
5. **`segname` 是定长 16 字节** —— 本目录第一版用 `blob[off+8:off+13] = b"__TEXT"` 赋值，5 字节的切片换成 6 字节的字符串把整个文件"顶长"了 1 字节，导致后续所有偏移整体错位；正确做法是按 16 字节定长写入。
6. **共享缓存里的 dylib 不走这条路** —— 认 `MH_DYLIB_IN_CACHE`。
7. **砸壳后文件长度不变** —— 如果变了，说明写成了插入而不是覆盖。

## 参考资料（实际读过）

- [xnu 源码 `EXTERNAL_HEADERS/mach-o/loader.h`](https://github.com/apple-oss-distributions/xnu/blob/main/EXTERNAL_HEADERS/mach-o/loader.h) —— `mach_header_64` / `segment_command_64` / `encryption_info_command` 与 `_64` 的字段与 pad 注释、`MH_MAGIC_64` / `MH_CIGAM_64`、`LC_SEGMENT_64` / `LC_UUID` / `LC_ENCRYPTION_INFO` / `LC_ENCRYPTION_INFO_64`、`MH_EXECUTE` / `MH_PIE` / `MH_DYLIB_IN_CACHE`
- [Frida JavaScript API](https://frida.re/docs/javascript-api/) —— `Process.getModuleByName()` 的 `base`/`size`（砸壳脚本定位模块用）
