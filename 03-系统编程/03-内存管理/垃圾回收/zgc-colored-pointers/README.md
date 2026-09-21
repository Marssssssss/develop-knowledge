# ZGC 着色指针（colored pointer / zpointer）与并发压缩

> 目录：`03-系统编程/03-内存管理/垃圾回收/zgc-colored-pointers/`
> 语言：Python（`python/zpointer.py` + `python/main.py` + `python/selfcheck_zgc.py`，**63 断言实跑全绿**）/ Go（`go/zpointer.go` + `go/main.go` 人工审查）

## 一、简介

ZGC 的核心设计选择是 **colored oops**：把标记/重定位状态直接编进**指针值本身**，
而不是放进对象头或旁表。这样「对象已经被搬走了」这件事**在指针里就能读出来**，
load barrier 只需一次位测试即可判断是否需要修正，从而实现**并发压缩**
（对象在搬，应用线程照跑）。

本 demo 的位布局**逐字照抄** OpenJDK `src/hotspot/share/gc/z/zAddress.hpp`（master 分支实读），
并对照 Shenandoah 的 **Brooks pointer**（转发词放在对象头里）。

## 二、原理

### 2.1 zpointer 的 16 位元数据布局

源码注释给出的布局串（自高到低）：

```
RRRRMMmmFFrr0000
****                 : Used by load barrier          (Remapped 4 位)
**********           : Used by mark barrier          (+ Marked 6 位)
************         : Used by store barrier         (+ Remembered 2 位)
             ****    : Reserved bits                 (低 4 位)
```

| 字段 | shift | bits | 取值 | 用途 |
| --- | --- | --- | --- | --- |
| Reserved | 0 | 4 | `0x0000` | 恒为 0，不参与任何屏障 |
| Remembered `rr` | 4 | 2 | `1<<4`, `1<<5` | store barrier（记忆集） |
| Finalizable `FF` | 6 | 2 | `1<<6`, `1<<7` | mark |
| MarkedYoung `mm` | 8 | 2 | `1<<8`, `1<<9` | mark（新生代） |
| MarkedOld `MM` | 10 | 2 | `1<<10`, `1<<11` | mark（老年代） |
| Remapped `RRRR` | 12 | 4 | `1<<12`…`1<<15` | **load barrier** |

三层屏障的掩码关系（`Load ⊆ Mark ⊆ Store = All`）：

```
LoadMetadataMask  = RemappedMask                     = 0xF000
MarkMetadataMask  = Load  | MarkedMask               = 0xFFC0
StoreMetadataMask = Mark  | RememberedMask           = 0xFFF0
AllMetadataMask   = Store                            = 0xFFF0
```

- **remembered 位只有 store barrier 看**：`MarkMetadataMask & RememberedMask == 0`；
- **reserved 四位不参与任何屏障**：`AllMetadataMask & ReservedMask == 0`；
- 元数据占满低 16 位 ⇒ **地址位恒从 bit 16 开始 ⇒ 堆基址必须 64 KiB 对齐**。

### 2.2 为什么 remap 有 4 个状态（而不是 2 个）

分代 ZGC 里新生代与老年代各自独立翻转，于是概念上有 `RemappedYoung[0,1]` 与 `RemappedOld[0,1]` 两对位。
但 load barrier 只接受「操作数里**只有一个零**」的位型，所以这两对被**编码成 4 位中的单比特**：

```
RemappedOldMask   交替:  0011 / 1100
RemappedYoungMask 交替:  0101 / 1010
两者取交:  0011&0101 = 0001 (Remapped00)
           0011&1010 = 0010 (Remapped01)
           1100&0101 = 0100 (Remapped10)
           1100&1010 = 1000 (Remapped11)
```

因此每次只翻一侧，remap 位走 **`00 → 01 → 11 → 10 → 00`** 的四步循环（demo 已断言）。

### 2.3 重叠零位与「一条推测移位」

x86 上 JIT 编译的 load barrier 期望**地址位紧跟在 load-good 位之后**，
这样「检查 good 位」与「去色」能合并成一条推测移位指令：

```
      vvv- 重叠的地址/元数据零位
aaa...aaa0001MMmmFFrr0000  = Remapped00
aaa...aaa00010MMmmFFrr0000 = Remapped01
aaa...aaa000100MMmmFFrr0000= Remapped10
aaa...aaa0001000MMmmFFrr0000 = Remapped11   (无重叠)
```

重叠零位数依次为 **3 / 2 / 1 / 0**（AArch64 不做重叠，用取反的 remap 位）。
对应 `ZPointerLoadShiftTable`：

| 下标 | 含义 | 移位量 |
| --- | --- | --- |
| 0 | Null | `12+12 = 24` |
| 1 | Remapped00 | 13 |
| 2 | Remapped01 | 14 |
| 4 | Remapped10 | 15 |
| 8 | Remapped11 | 16 |
| 3/5/6/7 | 非法组合 | 0 |

### 2.4 并发压缩与自愈（self-healing）

1. GC 把对象从 `a` 搬到 `b`，**但引用它的字段还指向旧地址**；
2. 翻转 remap 位 ⇒ 所有旧颜色的指针**立刻**变成 load-bad，无需遍历修正；
3. 应用线程第一次 load 这个字段 → barrier 判 bad → 慢路径查转发表 → 得到 `b`；
4. **把修正后的（带当前颜色的）指针写回字段** ⇒ 下次不再进慢路径。

JEP 333 原文强调这条性质带来的好处：可以在「指向被回收 region 的指针还没修完之前」
就回收并复用那块内存，因此**不需要单独实现 mark-compact 来处理 full GC**。

## 三、对比：ZGC colored pointer vs Shenandoah Brooks pointer

| | ZGC | Shenandoah |
| --- | --- | --- |
| 状态存放位置 | **指针值的低位** | 对象头里的转发词（Brooks pointer） |
| load 开销 | 一次位测试，good 时**零额外内存访问** | **每次 load 都要多读一次对象头**（恒 1:1） |
| 修正方式 | barrier 把修正后的指针**写回字段**（自愈） | LRB 直接返回 to-space 地址 |
| 标记屏障 | mark barrier（元数据位） | SATB mark queue（`shenandoahSATBMarkQueueSet`） |
| 关键结构 | `zpointer` / load barrier / 转发表 | `load_reference_barrier` / `arraycopy_evacuation` |
| 里程碑 | JEP 333（JDK 11 实验）→ JEP 439（分代） | JEP 189 → **JEP 379（JDK 15 产品化）** |

本 demo 用计数断言这一点：Shenandoah 侧 `extra_reads == loads`；ZGC 侧颜色一致时 `slow_paths == 0`。

## 四、环境

- Python ≥ 3.9 / Go 1.20+（Go 无工具链时走人工审查）
- 无第三方依赖

## 五、运行

```bash
cd python && python main.py            # 打印掩码、remap 循环、自愈过程、Shenandoah 对照
cd python && python selfcheck_zgc.py   # 63 条断言
cd go     && go run .                  # Go 镜像
```

## 六、关键代码

```python
def load_barrier(self, field):
    p = self.fields[field]
    if p == 0 or self.g.is_load_good(p):      # 仅一次位测试
        return uncolor(p)
    self.slow_paths += 1
    addr = self.forwarding.get(uncolor(p), uncolor(p))
    self.fields[field] = color(addr, self.g.remapped)   # 自愈：写回字段
    return addr
```

## 七、性能与边界

- **地址可用位数**：元数据吃掉低 16 位后，堆内偏移从 bit 16 起算；堆基址必须 **64 KiB 对齐**
  （非对齐地址在本 demo 的 `color()` 里会被断言直接拦下）。
- **load barrier 的快路径**只需一条测试 + 一条推测移位（靠重叠零位实现），
  慢路径才查转发表；**慢路径对每个字段只会走一次**（自愈）。
- JEP 333 的公开基准（128 G 堆，SPECjbb 2015 composite，文献数值）：
  ZGC 暂停 avg **1.091 ms** / max **1.681 ms**，G1 avg **156.806 ms** / max **543.846 ms**；
  吞吐 max-jOPS 100% vs G1 91.2%，critical-jOPS 76.1% vs 54.7%。
  设计目标是**暂停 ≤ 10 ms、吞吐损失 ≤ 15%**。
- **STW 阶段只剩根扫描**，因此暂停时间不随堆大小或存活集增长（JEP 333 原文）。

## 八、坑

1. **别把「当前 master 的 16 位布局」当成 JEP 333 时代的 4 位布局** —— 分代化（JEP 439）之后
   元数据从 4 位扩到 16 位（新增 remembered 与 young/old 分组）。本 demo 用的是实读到的 master 版本。
2. **remap 位是「两个掩码取交」的结果**，翻 young 或翻 old 都会改变它；不要以为只有一次翻转。
3. **`ZPointerLoadShiftTable` 下标不是连续的**：只有 0/1/2/4/8 有效，3/5/6/7 恒为 0，
   直接 `table[color]` 取到 0 会算出错误地址。
4. **重叠零位是 x86 特有的优化**（AArch64 用取反的 remap 位，得到 3 个 good 位 + 1 个 bad 位），
   跨平台讨论时不要混用。
5. **「颜色过期」不等于「对象已被搬走」** —— 也可能是整个 epoch 翻转了，
   所以慢路径必须查转发表而不能假设地址失效。

## 九、参考资料（实际读过）

- OpenJDK `src/hotspot/share/gc/z/zAddress.hpp`（master）：完整的 zpointer 布局注释、
  各段 shift/bits/位常量、`ZPointerLoadShiftTable`、三层屏障掩码、RemappedOld/Young 掩码交替
  — <https://raw.githubusercontent.com/openjdk/jdk/master/src/hotspot/share/gc/z/zAddress.hpp>
- JEP 333: ZGC: A Scalable Low-Latency Garbage Collector（设计目标、并发压缩、基准数字）
  — <https://openjdk.org/jeps/333>
- JEP 439: Generational ZGC（分代化后 young/old 双份标记位的动机）
  — <https://openjdk.org/jeps/439>
- OpenJDK `src/hotspot/share/gc/shenandoah/shenandoahBarrierSet.hpp`（`load_reference_barrier`、
  `satb_mark_queue_set`、`arraycopy_evacuation`）
  — <https://raw.githubusercontent.com/openjdk/jdk/master/src/hotspot/share/gc/shenandoah/shenandoahBarrierSet.hpp>
- JEP 379: Shenandoah: A Low-Pause-Time Garbage Collector (Production)
  — <https://openjdk.org/jeps/379>
