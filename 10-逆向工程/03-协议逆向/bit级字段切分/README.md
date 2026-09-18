# bit 级字段切分（Bit-level Field Segmentation）

## 一、简介

协议逆向里「字段边界推断」这道工序，从 2007 年 Discoverer 到 2024 年 CCS 的 BinPRE，
**主流工具几乎全部停在字节粒度**。这不是偶然——Tupni (CCS 2008) §3.3 自己写得很直白：

> "Currently we track input chunks at **byte granularity**; we believe it takes only
> engineering efforts to refine this to bit granularity."

十六年后的 BinPRE（CCS 2024, arXiv:2409.01994）§3.2 依然是：

> "It taints protocol message data at the **byte level** and captures their propagation
> traces..."

§3.3 的字段候选也仍是**字节序列**（Algorithm 1 行 4-5："extracts the sequence of bytes
from execution information and treats them as a candidate field"）。那句「只需工程投入」
并没有兑现。

代价是实打实的：一个 DNS 报文头的 16 位标志字里有 **8 个语义字段**（RFC 1035 §4.1.1），
字节粒度只能看到 **2 个**。TCP 的 data offset 半字节、IPv4 的 Flags/Frag Offset、
HTTP/2 与 QUIC 的各类 bitflag，全都压在字节里不可见。

本 demo 用**纯统计判据**把字节拆到位，并诚实地标出**它做不到什么**（见 §2.5）。

## 二、原理详解

### 2.1 位序约定

RFC 1035 §4.1.1 的头部位布局（原图照抄）：

```
                                    1  1  1  1  1  1
      0  1  2  3  4  5  6  7  8  9  0  1  2  3  4  5
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
    |QR|   Opcode  |AA|TC|RD|RA|   Z    |   RCODE   |
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
```

8 个字段：`QR(1) Opcode(4) AA(1) TC(1) RD(1) RA(1) Z(3) RCODE(4)`；
RFC 对 Z 的规定是 "Reserved for future use. **Must be zero**"。
代码里 `bit(msg, i)` 的 `i=0` 是**最高位**（网络序），与 RFC 图从左到右一致。

### 2.2 判据：基数（cardinality）相关性

核心不等式，对相邻 bit 窗口 `A`、`B`：

```
|A ∪ B 的联合取值数| < |A 的取值数| × |B 的取值数|   →  A、B 统计相关 → 同一字段
否则                                              →  独立      → 此处是边界
```

直觉：若两位真正独立，语料够大时它们的组合会填满笛卡尔积；只要**有任一组合从未出现**，
就说明存在约束把它们绑在一起（比如 Opcode 的 3-15 保留，导致高位组合不可达）。

`segment()` 从左到右扫变量位，贪心地把「相关」的并入当前块，遇到「独立」就切一刀。

### 2.3 语料构造（真值可核对）

4000 条 DNS 标志字，seed `20260919`，刻意制造**非笛卡尔积**：

| 字段 | 取值 | 为什么这么设 |
|---|---|---|
| QR | 0/1 | 均匀 |
| Opcode | `{0,0,0,1,2}` | 按 RFC 只用 QUERY/IQUERY/STATUS，**不含 3-15** → 高位联合基数塌缩 |
| AA / RA | 0/1 | 均匀 |
| TC | 10% 为 1 | 截断少见 |
| RD | 15% 为 0 | 通常置 1 |
| Z | 恒 0 | RFC 强制 |
| RCODE | `{0,1,2,3,5}` | 跳过 4/6/7 → 低 3 位非笛卡尔积 |

### 2.4 结果

```
变量位 (10 个) : [0, 3, 4, 5, 6, 7, 8, 13, 14, 15]
常量位 (6 个)  : [1, 2, 9, 10, 11, 12]   → 常量段 [1,3) [9,13)
变量块         : (0,1) (3,5) (5,6) (6,7) (7,8) (8,9) (13,16)
                 QR  Opcode低2位 AA  TC  RD  RA  RCODE低3位
+ 常量段 [1,3) [9,13)  →  9 段正好铺满 16 位
```

`bit0` 单独成块：QR 与其它位独立，联合基数 4 = 2×2，判为边界。
`bit3-4` 合成一块：Opcode 只取 3 个值，联合基数 3 < 2×2 → 相关。
`bit13-15` 合成一块：RCODE 取 5 个值，三级联合基数 5 < 3×2 → 相关。

### 2.5 能力边界：常量位不可判定

这是本 demo 最重要的结论，也是**必须写进工具设计**的一点：

- 常量段 `[9,13)` 横跨了 **Z（3 位）+ RCODE 最高位（1 位）** 两个语义字段，
  但 Z 恒为 0、RCODE 恒 < 8，统计上**完全无法切分**。
- 给常量位单独开窗，`cardinality` 恒为 1 → 没有任何信息量。
- **换信息论判据同样失效**：Z 段的经验熵精确为 0（`abs(H) < 1e-12`）。

→ **纯统计 / 纯信息论的位级切分，对常量位无能为力**。要突破必须引入非统计先验
（RFC 规范、结构体布局、指令级移位掩码），这正是其它工具走「执行轨迹」路线的理由。

### 2.6 字节粒度对照

| 视图 | 段数 | 明细 |
|---|---|---|
| **位级** | **9** | 7 个变量块 + 2 个待定常量段，铺满 16 位 |
| 字节级 | 2 | byte0 基数 **48**（=2×3×2×2×2，5 个子字段的笛卡尔积）、byte1 基数 **10**（=2×5） |

字节 0 的基数 48 本身就是证据：它**不是** 256，说明内部有约束；但字节粒度看不出
约束落在哪一位上。

## 三、方案对比

| 工具 / 方法 | 粒度 | 边界判据 | 需不需要执行轨迹 |
|---|---|---|---|
| Discoverer (USENIX Sec '07) | token（字节序列） | 递归聚类 + FD 三判据 | 否（纯报文） |
| AutoFormat (NDSS '08) | 字节 | 调用栈上下文 + 偏移连续 | **是** |
| Tupni (CCS '08) | **字节**（§3.3 自述） | 加权最大 k-Set Packing | **是** |
| BinPRE (CCS '24) | **字节**（§3.2 自述） | 指令算子序列 NW 相似度 | **是** |
| **本 demo** | **bit** | 联合基数 < 基数之积 | 否（纯报文） |

关键取舍：**本 demo 放弃「常量位归属」，换来不依赖插桩的纯报文位级切分**。前四者靠执行
轨迹能定位常量位属于哪个结构体成员，但需要能跑起来的二进制；本方法只需要一批报文，
代价是常量段永远标成「待定」。

## 四、环境

- Python ≥ 3.8（仅标准库 `random` / `collections` / `math`）
- Go ≥ 1.20（`go run .`，仅 `fmt` / `math/rand`）
- **无第三方依赖**

## 五、运行方式

```bash
# Python
python bitfield_check.py          # 19 条断言，全通过则打印 ALL ASSERTIONS PASSED

# Go
go run .                          # 等价断言，失败即 panic
```

## 六、关键代码

| 文件 | 行数 | 职责 |
|---|---|---|
| `bitfield.py` | 93 | `bit` / `window_value` / `cardinality` / `entropy_bits` / `varying_bits` / `constant_runs` / `segment` / `byte_view` |
| `bitfield_check.py` | 116 | 19 条断言 + DNS 语料构造 |
| `bitfield.go` | 124 | Go 版算法实现 |
| `main.go` | 102 | Go 版语料与断言入口 |

切分主循环（`bitfield.py`）：

```python
for b in vb[1:]:
    ca, cb = cardinality(msgs, cur), cardinality(msgs, [b])
    if cardinality(msgs, cur + [b]) < ca * cb:
        cur.append(b)                      # 相关 → 同一字段
    else:
        blocks.append(tuple(cur))          # 独立 → 边界
        cur = [b]
```

常量位单独返回，**不与变量块混在一起**——这是把「不知道」显式建模，而不是硬塞一个答案。

## 七、性能边界

- **复杂度**：`segment` 对 `n` 位语料做 O(n) 次 `cardinality`、每次 O(N)，
  即 **O(n × N)**（N = 报文条数）；本 demo n=16、N=4000 → 瞬时。扫整条报文
  （n 数千 × N 数十万）必须先按字节粗筛再进位的细化，否则退化成逐位全量扫描。
- **样本量**：判据依赖「语料是否覆盖到笛卡尔积」。N 太小时联合基数系统性偏小 →
  过度合并（under-segmentation）；N 太大、覆盖过全时 → 见下条。
- **过度切分（over-segmentation）**：若语料里 Opcode 取满 0..15，则
  `card(bit1..4) = 16 = 2^4` 恰好等于各位基数之积 → 判据判定 4 位两两独立，
  **Opcode 被切成 4 个 1 位字段**。自检脚本用一条 64 条报文的笛卡尔积语料
  固化了这个反例。**位级切分没有「正确答案」，只有相对于语料的答案。**

## 八、注意事项与常见坑

1. **别把常量段当成一个字段**。`[9,13)` 在语义上是 Z(3) + RCODE(1) 两个字段，
   代码只保证「铺满 16 位」，不保证「段 = 字段」。下游如果要按段做变异/模糊测试，
   必须把常量段展开成全 0（或按先验拆分），否则会生成 Z≠0 的非法报文。
2. **位序别搞反**。`bit(msg, i)` 的 `i=0` 是最高位，与 RFC 图的左端一致；
   若按「bit 0 = 最低位」写，切出来的块会整个镜像。
3. **语料分布决定一切**。本 demo 刻意让 Opcode 不取 3-15、RCODE 跳过 4/6/7。
   换成真实抓包，分布变了结果就变——**不要指望跨语料复现同一份切分**。
4. **熵判据不是救命稻草**。Z 段熵为 0，既可以是「常量」也可以是「极低概率事件」，
   信息论在这里与基数判据等价地失效。
5. **Go 版本曾有真实编译错**：`segment()` 里 `if len(vb) == 0 { ... }` 的提前返回块
   **漏了右花括号**，代码读起来完全正常，只有 `bracket_check.py` 报
   `UNCLOSED [('{', ...)]` 才暴露。本机无 Go 工具链，这类语法错只能靠机械检查兜底。
6. **不要指望纯统计突破常量位**。见 §2.5，这是信息论下限，不是实现缺陷。

## 九、参考资料

（均为本 README 写作时**实际查阅**的原文）

- RFC 1035 §4.1.1 "Header section format"（位布局图与 Z 的 "Must be zero" 原文）
  — https://www.rfc-editor.org/rfc/rfc1035.txt
- Cui, Kannan, Wang. **Discoverer**, USENIX Security 2007.
- Lin, Jiang, Xu, Zhang. **AutoFormat**, NDSS 2008.
- Cui, Peinado, Chen, Wang, Irun-Briz. **Tupni**, CCS 2008, §3.3 Field
  Identification（"byte granularity" 原文）。
- Jiang, Zhang, Wan, Chen, Sun, Su. **BinPRE**, CCS 2024, arXiv:2409.01994,
  §3.2 Execution Monitor（"taints ... at the byte level" 原文）与 §3.3 Algorithm 1.
