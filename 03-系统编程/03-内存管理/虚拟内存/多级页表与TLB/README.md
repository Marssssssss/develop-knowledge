# 多级页表、TLB 与缺页中断

> 目录：`03-系统编程/03-内存管理/虚拟内存/多级页表与TLB/`
> 语言：Python（`python/main.py` + `python/selfcheck_paging.py`，**62 断言实跑全绿**）/ Go（`go/paging.go` + `go/main.go` 人工审查）

## 一、简介

虚拟内存的三块硬件/内核机制放在一个模型里看：**地址怎么切**（多级页表）、
**翻译怎么缓存**（TLB）、**翻译不出来时怎么办**（缺页中断）。
本 demo 的地址常量**逐条取自** Linux 内核文档 `arch/x86/x86_64/mm.rst` 与 `5level-paging.rst`，
缺页语义取自 `userfaultfd(2)` / `mprotect(2)` / `madvise(2)` / `mincore(2)`。

## 二、原理

### 2.1 地址切分与规范地址

x86-64：4 KiB 页 ⇒ 页内偏移 12 位；每级页表 512 项（4 KiB / 8 B）⇒ 每级 9 位。

| 级数 | 各级下标 | 虚拟地址位宽 |
| --- | --- | --- |
| 4（PML4→PDPT→PD→PT） | 4 × 9 + 12 = 48 | 48 |
| 5（+PML5） | 5 × 9 + 12 = 57 | 57 |

**规范地址**要求高位是符号扩展，否则 MMU 直接 #GP：

| 地址 | 4 级 | 5 级 |
| --- | --- | --- |
| `0x00007fffffffffff`（2⁴⁷−1） | ✅ 用户 | ✅ |
| `0x0000800000000000`（2⁴⁷） | ❌ 非规范空洞 | ✅ |
| `0xffff800000000000` | ✅ 内核 | ✅ |
| `0x0100000000000000`（2⁵⁶） | ❌ | ❌ 非规范空洞 |

### 2.2 内核虚拟内存布局（`mm.rst` 原文数值）

| 区域 | 4 级 | 5 级 |
| --- | --- | --- |
| 用户空间终点 | `00007ffffffff000`（~128 TB） | `00fffffffffff000`（~64 PB） |
| guard hole | 紧随其后 **4 kB**（1 页） | 4 kB |
| 直接映射 | `ffff888000000000`，64 TB | `ff11000000000000`，32 PB |
| vmalloc/ioremap | `ffffc90000000000`，32 TB | `ffa0000000000000`，12.5 PB |
| vmemmap | `ffffea0000000000`，1 TB | `ffd4000000000000`，0.5 PB |
| 内核文本 | `ffffffff80000000`，512 MB（**映射到物理地址 0**） | 同 |

**5 级页表的收益**（`5level-paging.rst`）：虚拟地址上限 **256 TiB → 128 PiB（×512）**，
物理地址上限 **64 TiB → 4 PiB（×64）**；用户空间从 128 TB 扩到 64 PB，**正好 ×512**。
代价是页表遍历多一级 ⇒ 每次遍历多一次内存访问。

> ⚠️ 5 级启用 56 位用户态虚拟地址，但内核**默认不分配 47 位以上的地址**
> （部分 JIT 会用指针高位存元数据）；只有显式给出高于 47 位的 hint 才会用到高地址区。

### 2.3 TLB 与巨页

- 遍历代价 = **级数 + 1**（最后一次是真正的数据访问）：4 级 5 次、5 级 6 次；
- 512 项 TLB × 4 KiB = 2 MiB 覆盖；换成 **2 MiB 巨页覆盖量 ×512**；
- **`mprotect` 降权必须配 TLB shootdown**：只改页表不改 TLB，多核下陈旧条目会**放行已被禁止的写**
  （本 demo 用 `stale_allowed` 计数断言这一点）。

### 2.4 缺页的几种形态

| 场景 | 结果 | 是否涉及 I/O |
| --- | --- | --- |
| 匿名页首次访问 | **minor**（零页按需） | 否 |
| fork 后写共享页 | **minor**（COW 复制） | 否 |
| 文件页首次访问 / 换入 | **major** | 是 |
| 只读页上写 / `PROT_NONE` 访问 | **SIGSEGV** | — |
| `userfaultfd` 注册区访问 | 交给**用户态** handler | 取决于 handler |

**COW 的关键区分**：fork 只清掉两边 **PTE 的写位**，**VMA 权限仍是 `rw`**。
所以写操作触发的是 minor fault（复制后恢复写位），**不是 SIGSEGV** ——
只读 VMA（`prot == "r"`）上的写才是 SIGSEGV。本 demo 把 `prot`（VMA）与
`pte_writable`（PTE）拆成两个字段来体现这一点。

**userfaultfd**（`userfaultfd(2)`）：

| 模式 | 内核版本 | 触发时机 |
| --- | --- | --- |
| `UFFDIO_REGISTER_MODE_MISSING` | 4.10 | 页不存在（首次访问） |
| `UFFDIO_REGISTER_MODE_WP` | 5.7 | 写已存在的页 |
| `UFFDIO_REGISTER_MODE_MINOR` | 5.13 | minor fault |
| `UFFDIO_REGISTER_MODE_RWP` | 7.2 | 读/写保护 |

- **`WP` 与 `RWP` 不能同时注册**；`MISSING | WP` 是合法组合；
- handler 用 `UFFDIO_COPY` / `UFFDIO_ZEROPAGE` / `UFFDIO_CONTINUE` / `UFFDIO_WRITEPROTECT` 应答。

**`MADV_DONTNEED`** 对**匿名私有**映射：之后的访问会得到**零页**（数据丢失）；
`MADV_FREE`（4.5+）只能用于私有映射，且在真正回收前可能仍读到旧数据。

## 三、对比

| | 4 级页表 | 5 级页表 |
| --- | --- | --- |
| 虚拟地址上限 | 256 TiB | 128 PiB |
| 物理地址上限 | 64 TiB | 4 PiB |
| 用户空间 | ~128 TB | ~64 PB |
| 遍历内存访问 | 5 | 6 |
| 用户态可见位宽 | 47 位（默认） | 56 位（默认仍只用 47 位） |

## 四、环境

- Python ≥ 3.9 / Go 1.20+（Go 无工具链时走人工审查）
- 无第三方依赖

## 五、运行

```bash
cd python && python main.py               # 打印地址切分、规范地址判定、TLB 覆盖比
cd python && python selfcheck_paging.py   # 62 条断言
cd go     && go run .                     # Go 镜像
```

## 六、关键代码

```python
def is_canonical(va, levels=4):
    bits = 48 if levels == 4 else 57
    sign_bits = 64 - bits + 1
    top = va >> (bits - 1)
    return top == 0 or top == (1 << sign_bits) - 1
```

```python
# COW：VMA 可写但 PTE 写位被清 -> minor fault，复制后恢复写位
if write and self.cow[idx] and not self.pte_writable[idx]:
    self.cow[idx] = False
    self.pte_writable[idx] = True
    return MINOR
```

## 七、性能与边界

- **多一级页表 = 每次遍历多一次内存访问**，TLB miss 的代价从 5 次访存涨到 6 次（+20%）；
  这也是巨页受欢迎的直接原因（一条 TLB 项顶 512 条）。
- **非规范地址不产生缺页**，MMU 直接判非法 ⇒ 5 级页表下指针高位不再"免费"可用。
- **TLB shootdown 是多核开销大头**：`mprotect` 降权后需要 IPI 通知所有可能缓存了该条目的 CPU。
- **major fault 与 minor fault 的分界是"要不要 I/O"**，不是"要不要分配页"——
  COW 与首次访问都分配页，但都是 minor。
- **`mincore(2)` 只回答"在不在核心里"**，不回答"脏不脏"；且结果是采样式的，读到即可能已过期。

## 八、坑

1. **「只读页上写」有两层**：VMA 只读 → SIGSEGV；VMA 可写但 PTE 写位被清 → **COW minor fault**。
   把两者合并成一个 `prot` 字段必然判错。
2. **规范地址的判据是「符号扩展」不是「小于 2^47」** —— 内核地址（如 `0xffff800000000000`）也是规范的。
3. **5 级页表下用户空间是 64 PB，但默认仍只用 47 位**，别拿 64 PB 当可用地址空间。
4. **`MADV_DONTNEED` 的零填充只对匿名私有映射成立**，文件映射与共享映射语义不同。
5. **TLB 覆盖量比较要固定项数**：`512 × 2 MiB` 与 `512 × 4 KiB` 才是 ×512；
   拿"同样内存量需要的项数"来比会得到同一个数但含义相反。
6. **`UFFDIO_REGISTER_MODE_WP` 与 `_RWP` 互斥**，但 `MISSING | WP` 可以共存 —— 别按"位掩码都能或"想当然。

## 九、参考资料（实际读过）

- Linux `Documentation/arch/x86/x86_64/mm.rst`（4 级 / 5 级完整虚拟内存映射表）
  — <https://www.kernel.org/doc/html/latest/arch/x86/x86_64/mm.html>
- Linux `Documentation/arch/x86/x86_64/5level-paging.rst`（256 TiB→128 PiB、64 TiB→4 PiB、
  56 位用户态地址与"默认不分配 47 位以上"的约定）
  — <https://www.kernel.org/doc/html/latest/arch/x86/x86_64/5level-paging.html>
- Linux man-pages `userfaultfd(2)`（四种注册模式、内核版本、互斥规则、ioctl 集合）
  — <https://man7.org/linux/man-pages/man2/userfaultfd.2.html>
- Linux man-pages `mprotect(2)` / `madvise(2)` / `mincore(2)` / `mbind(2)`
  — <https://man7.org/linux/man-pages/man2/mprotect.2.html>
  — <https://man7.org/linux/man-pages/man2/madvise.2.html>
  — <https://man7.org/linux/man-pages/man2/mincore.2.html>
