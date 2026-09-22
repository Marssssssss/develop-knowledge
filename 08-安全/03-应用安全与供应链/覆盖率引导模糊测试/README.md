# 覆盖率引导模糊测试（AFL 模型）

American Fuzzy Lop 把「程序走了哪些边」当成遗传算法的适应度函数。整个机制只有三块：
一张 64 KB 的共享位图、一套把命中数压成 8 个桶的分级表、以及一个靠位图做贪心集合覆盖的队列裁剪器。
本 demo 按 `google/AFL` 的官方源码与 `docs/technical_details.txt` 复刻这套模型。

## 一、边覆盖：为什么用 `cur ^ prev` 而不是块号

被插桩的程序在每个分支点执行等价于下面的逻辑（technical_details.txt §1）：

```c
cur_location = <COMPILE_TIME_RANDOM>;
shared_mem[cur_location ^ prev_location]++;
prev_location = cur_location >> 1;
```

`cur_location` 是编译期随机常量，作用是让 XOR 的输出尽量均匀分布。
槽位由**跳转的源和目的共同决定**，所以它能区分这两条完全不同的执行轨迹：

```
A -> B -> C -> D -> E   (tuples: AB, BC, CD, DE)
A -> B -> D -> C -> E   (tuples: AB, BD, DC, CE)
```

纯块覆盖看不出差别，边覆盖一眼就能看出来——而安全漏洞更多来自「非预期的状态转移」，
不只是「碰到了某个新块」。

`prev_location = cur_location >> 1` 这一句是刻意的：它让 `A -> B` 和 `B -> A`
落到不同的槽（注意不是靠 XOR 的对称性，XOR 本身是对称的，**是右移打破了对称**）。
实测发现块号 1 是个反例：`1 ^ 0 == 1 ^ (1 >> 1)`，因为 `1 >> 1 == 0`；
换块号 6 就正常了（`6 ^ 0 = 6`，`6 ^ (6 >> 1) = 5`）。这不是 bug，是哈希碰撞——
文档自己也给了碰撞率表（1k 分支 0.75%，10k 分支 7%，50k 分支 30%）。

官方选 64 KB 是权衡：目标通常只有 2k–10k 个可发现分支点，碰撞很零星；
同时整张表小到能在微秒级扫完、能塞进 L2 cache。

## 二、命中数分桶：粗粒度的「走了几次」

`afl-fuzz.c` 的 `count_class_lookup8[256]` 把 0–255 的命中数压成 8 个桶：

| 命中数 | 0 | 1 | 2 | 3 | 4–7 | 8–15 | 16–31 | 32–127 | 128–255 |
|---|---|---|---|---|---|---|---|---|---|
| 桶值 | 0 | 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 |

要点是**桶值本身是 2 的幂**（除了 0），所以它们可以当位掩码用：`virgin[i] & cur`
非零就说明这一格出现了「没见过的桶」。

这一层设计让「同一条边被走了 5 次还是 6 次」不再算新行为，但「从 3 次跳到 4 次」算。
文档 §3 说得很直白：队列增长里只有 **10–30%** 来自发现新 tuple，**剩下全是命中数的变化**。

## 三、`has_new_bits`：三态返回值

```c
// 简化版
if (virgin[i] & cur) {
    if (ret < 2) ret = (virgin[i] == 0xff) ? 2 : 1;
    virgin[i] &= ~cur;
}
```

- `0`：什么都没有
- `1`：已知边，但出现了新的命中数桶
- `2`：**全新的边**

判据是 `virgin[i] == 0xff`：初始状态下每一格都是 `0xff`，一旦被碰过就必然小于它
（因为 `cur` 至少贡献一个非零位）。这是一个很省事的「从没被碰过」判据。

## 四、队列裁剪（culling）：贪心集合覆盖

后期生成的用例，其覆盖往往是祖先用例的**严格超集**。文档 §4 的算法是：

1. 给每个队列条目算分：`score ∝ 执行延迟 × 文件大小`（越小越划算）
2. 对每条 tuple，先记下分数最低的那个候选条目
3. 顺序扫：找下一条还没进工作集的 tuple → 取它的获胜条目 →
   把**该条目的全部 tuple** 都注册进工作集 → 重复

第 3 步的「注册全部」是关键，它让每次选择都顺带覆盖一大片，通常能把语料库压到
**原来的 1/5 到 1/10**。

非 favored 的条目不删除，只是按概率跳过：

| 情形 | 跳过概率 |
|---|---|
| 队列里还有没 fuzz 过的新 favorite | 99% |
| 没有新 favorite，该条目前面 fuzz 过 | 95% |
| 没有新 favorite，该条目还没 fuzz 过 | 75% |

demo 里 4 条压到 3 条：`slow-big`（score 1000000，全覆盖但太贵）出局，
`fast-cover` / `redundant` / `unique` 留下。

> 实现坑：顺序扫的时候，已经被选进 favored 的条目后面会被判成「已全覆盖」。
> 这时**不能**清它的 `favored` 标记，否则最后一个 winner 会被自己抹掉。

## 五、Trimming：靠校验和删数据

文件越大，目标跑得越慢，而且变异更容易打在冗余数据块上而不是格式控制结构。
内置 trimmer 的做法很朴素：按**变长块**顺序删，只要删完位图校验和没变就落盘。

```python
for size in (16, 8, 4, 2, 1):
    # 删掉 [i, i+size)，checksum 不变就提交
```

官方明说这个 trimmer「不追求彻底」，只在精度和执行次数之间取平衡，
平均每文件能省 **5–20%**。想更彻底得用 `afl-tmin`（它会额外做 alphabet 归一化，
而且是用 ASCII 的 `'0'` 而不是 `0x00` 去填——这样更不容易干扰文本解析）。

## 六、确定性变异的参数

`config.h` 里的常量（本 demo 全部按原值落地）：

| 常量 | 值 | 含义 |
|---|---|---|
| `MAP_SIZE_POW2` | 16 | 位图 2^16 = 65536 字节 |
| `CAL_CYCLES` | 8 | 校准时每个用例跑 8 遍 |
| `CAL_CYCLES_LONG` | 40 | 变量行为时加跑 |
| `TMOUT_LIMIT` | 250 | 超时用例累计上限 |
| `EXEC_TIMEOUT` | 1000 | 单次执行硬超时（ms） |
| `HAVOC_CYCLES` | 256 | havoc 阶段轮数 |
| `HAVOC_CYCLES_INIT` | 1024 | 首轮 havoc |
| `SPLICE_CYCLES` | 15 | 拼接阶段轮数 |
| `ARITH_MAX` | 35 | 整数加减的最大值 |
| `MAX_FILE` | 1 MiB | 输入文件上限 |
| `INTERESTING_8` | -128/-1/0/1/16/32/64/100/127 | 8 位兴趣值 |

`INTERESTING_8` 的注释解释了每个值的来历：`-128` 是「减一时溢出有符号 8 位」，
`16` / `32` 是「常见缓冲区大小 ±1」。

超时值的取法是**初始校准速度的 5 倍，向上取整到 20 ms 的倍数**——
粒度对齐是为了避免大量不同但相近的 timeout 值。

## 七、运行

```bash
cd python && python selfcheck_afl.py   # 64 assertions passed
cd python && python main.py            # 迷你 campaign + 裁剪 + trimming 演示
```

Go 版在 `go/`（模块名 `afl`），结构一一对应：`afl.go`（位图/分桶/新边判定）、
`fuzz.go`（队列/裁剪/trim/变异参数）、`main.go`（演示）。
本机无 Go 工具链，已用 `bracket_check.py` / `go_sanity.py` / `go_crossref.py` 三个静态检查器验证。

演示输出（4000 轮随机变异，种子 `b"A"`）：

```
入队用例数: 108
覆盖的边数: 32
全部 4 条 -> favored 3 条: ['fast-cover', 'redundant', 'unique']
16 字节 -> 4 字节: b'\x08\x18(8'
```

## 八、断言覆盖（64 条）

- 分桶表：8 个桶的边界成对验证（31/32 跨桶、47/48 同桶、0 得 0）
- 边覆盖：`cur ^ prev` 的槽位计算；`prev = cur >> 1` 打破 A→B / B→A 的对称（用块 6，避开块 1 的碰撞）
- `has_new_bits`：0/1/2 三态；`virgin` 的消耗是累积的；已消耗的桶不再触发
- 队列裁剪：全超集被剔除、唯一贡献者保留、score 相同时取先出现者、favored 标记不被误清
- trimming：删掉不影响路径的字节；必须删到 checksum 仍与基线一致
- 跳过概率：三种情形的 0.99 / 0.95 / 0.75
- 超时取整：5 倍 + 向上取整到 20 ms；极小值不低于 20 ms
- 兴趣值/算术偏移：`ARITH_MAX` 决定偏移个数；兴趣值含溢出边界
- 路径校验和：相同执行路径得相同值，不同路径得不同值

## 九、参考资料

实际读过的来源：

- AFL 官方技术白皮书 `docs/technical_details.txt`
  <https://raw.githubusercontent.com/google/AFL/master/docs/technical_details.txt>
  （§1 覆盖度量、§2 新行为检测、§3 队列演化、§4 语料库裁剪、§5 trimming）
- AFL `config.h`（常量与 `INTERESTING_8/16/32`）
  <https://raw.githubusercontent.com/google/AFL/master/config.h>
- AFL `afl-fuzz.c`（`count_class_lookup8`、`classify_counts`、`has_new_bits`、`cull_queue`、`trim_case`）
  <https://raw.githubusercontent.com/google/AFL/master/afl-fuzz.c>
- AFL `types.h`（`u8`/`u16` 等类型定义）
  <https://raw.githubusercontent.com/google/AFL/master/types.h>
