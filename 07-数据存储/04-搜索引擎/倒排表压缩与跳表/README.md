# 倒排表压缩与跳表（Lucene 104 postings format）

一个 term 的倒排表在 Lucene 里被切成 **256 个值一块**，每块先做差值（d-gap），
再用 **PForDelta** 压缩；为了支持 `advance(target)`，还会在块之间铺一张**多级跳表**。
本 demo 把这两件事的**真实实现**逐行转写成 Python 与 Go。

## 1. 块的整体结构

```
┌────────┬──────────────────────┬─────────────────────────┐
│ token  │ 定长区（或 vInt）      │ patch 区（每个例外 2 字节）│
│ 1 字节  │ numBytes(bpv) 字节    │ 2 × numExceptions 字节   │
└────────┴──────────────────────┴─────────────────────────┘
   bit7..5 = numExceptions
   bit4..0 = bitsPerValue
```

`token` 一个字节同时编码两件事（源码 `PForUtil.encode`）：

```java
final int token = (numExceptions << 5) | patchedBitsRequired;
out.writeByte((byte) token);
```

于是 `numExceptions ≤ 7` 不是拍脑袋的限制，而是 **token 只有 3 位能放例外个数**。
`PForUtil` 里的常量 `MAX_EXCEPTIONS = 7` 正是它的另一种写法，且源码用 `assert`
保证 `ForUtil.BLOCK_SIZE <= 256`（例外下标要塞进 1 字节）。

## 2. 位数是怎么试探出来的

```java
final int minBits = Math.max(0, maxBitsRequired - 8);   // patch 只占 1 字节
int cumulativeExceptions = 0;
for (int b = maxBitsRequired; b >= minBits; --b) {
  if (cumulativeExceptions > MAX_EXCEPTIONS) break;
  patchedBitsRequired = b;
  numExceptions = cumulativeExceptions;
  cumulativeExceptions += histogram[b];
}
```

三个容易读错的点：

1. **循环是「先记账后判断」**：进入 `b` 这一轮时 `cumulativeExceptions` 是「比
   `b` 位更宽的值的个数」，所以 `numExceptions` 取的是**上一轮**累计值。实测
   `255×1 + 1×300`（`max_bits=9`）会被一路压到 **1 位、1 个例外**。
2. **下限是 `maxBits - 8` 而不是 0**：例外的高位只有 1 字节，`ints[i] >>> bpv`
   必须放得下。`255×7 + 1×4000` 的 `max_bits=12` 只能压到 **4 位**。
3. **整块全等的捷径发生在掩码之后**：源码先把例外 `ints[i] &= maxUnpatchedValue`
   写回原数组，**再**判 `allEqual`。所以 `250×1 + 6×5` 掩码后 256 个值全是 1，
   token 的 `bitsPerValue` 被写成 **0**，正文退化成一个 `vInt`。此时 patch 区
   记录的高位必须**先左移回去**再落盘：

   ```java
   exceptions[2*i+1] = (byte) (Byte.toUnsignedInt(exceptions[2*i+1]) << patchedBitsRequired);
   ```

   因为解码端统一写成 `ints[idx] |= hi << bitsPerValue`，而这里 `bitsPerValue == 0`。

## 3. ForUtil：为什么 bpv=8 的落盘字节是 0,64,128,192,1,65,...

`ForUtil` 不是朴素的位拼接。它先把 256 个值**交叠**进更少的「值字」：

| bpv | primitiveSize | collapse | 值字个数 | 每条泳道承载 |
| --- | --- | --- | --- | --- |
| 1..8 | 8 | `collapse8`（4 值/字） | 64 | 64 个值 |
| 9..16 | 16 | `collapse16`（2 值/字） | 128 | 128 个值 |
| 17..32 | 32 | 无 | 256 | 256 个值 |

```java
static void collapse8(int[] arr) {
  for (int i = 0; i < 64; ++i)
    arr[i] = (arr[i] << 24) | (arr[64+i] << 16) | (arr[128+i] << 8) | arr[192+i];
}
```

一个 32 位字因此被切成 **4 条 8 位泳道**，位打包在每条泳道里**完全同样地**进行 ——
这就是 `MASKS8/MASKS16` 要用 `expandMask8/16` 摊成 `0x01010101` 的原因：
一次移位同时推进 4 条泳道（思路来自 `fulmicoton.com/posts/bitpacking/`，
源码注释里直接给了链接）。

落盘时按 **bpv×8 个 32 位大端字**写出，所以字节顺序是**泳道转置**的。实测：

| bpv | 前 8 字节（值为下标 0..255） |
| --- | --- |
| 8 | `0, 64, 128, 192, 1, 65, 129, 193` |
| 16 | `0, 0, 0, 128, 0, 1, 0, 129` |

块长只由位数决定：`numBytes(bpv) = bpv << (BLOCK_SIZE_LOG2 - 3)`，
即 bpv=1 → 32 B、bpv=3 → 96 B、bpv=8 → 256 B、bpv=17 → 544 B、bpv=32 → 1024 B。

> 本 demo 用一张**放置图**（`placement_map`）复刻 `ForUtil.encode` 的控制流：
> 记录「第 i 个值字的第 k 位落在第几个输出字的哪一位」，encode 与 decode 共用
> 它，天然互逆。这样既保真，又避开了源码里向量化 `PostingDecodingUtil` 的分支。

## 4. 跳过一个块不必解码

```java
static void skip(DataInput in) {
  final int token = Byte.toUnsignedInt(in.readByte());
  final int bitsPerValue = token & 0x1f;
  final int numExceptions = token >>> 5;
  if (bitsPerValue == 0) { in.readVLong(); in.skipBytes(numExceptions << 1); }
  else in.skipBytes(ForUtil.numBytes(bitsPerValue) + (numExceptions << 1));
}
```

注意 `bitsPerValue == 0` 时读的是 **vLong 而不是 vInt**（源码注释没解释，
但 `decode` 那侧用的是 `readVInt`；两者对小值同构，本 demo 按源码原样实现）。

## 5a. Lucene104 真正在用的两级跳表

**注意**：Lucene 104 的 postings format **不再走** `MultiLevelSkipListWriter`。
它在 `Lucene104PostingsFormat` 里自己定了两级：

```java
public static final int BLOCK_SIZE = ForUtil.BLOCK_SIZE;   // 256
public static final int LEVEL1_FACTOR = 32;
public static final int LEVEL1_NUM_DOCS = LEVEL1_FACTOR * BLOCK_SIZE;   // 8192
static final int LEVEL1_MASK = LEVEL1_NUM_DOCS - 1;
```

- **level0**：每写完一个 256-块，就把 skip data 前插到这一块之前（因为块没编码完
  算不出 impacts，所以 `Lucene104PostingsWriter` 先写进 `level0Output` 缓冲）；
- **level1**：每 **32 个块**（= 8192 篇文档）一条，同样先缓冲在 `level1Output`。

于是 `doc >> 13` 直接给出 level1 组号、`doc & 8191` 给出组内偏移 —— 比通用
多级跳表少一次 `MathUtil.log` 与若干次取模。

## 5b. codecs 包里保留的通用多级实现

`MultiLevelSkipListWriter(skipInterval=128, skipMultiplier=8, maxSkipLevels=10, df)`
仍留在 `org.apache.lucene.codecs` 下供其它 codec 使用。它的层数推导是：

```java
this.numberOfSkipLevels =
    Math.min(1 + MathUtil.log(df / skipInterval, skipMultiplier), maxSkipLevels);
```

- `df <= skipInterval` 时**恒为 1 层**（不建跳表）；
- `MathUtil.log(base, x)` 是「满足 `base^ret <= x` 的最大 ret」，`log(8, 7) = 0`；
- 于是 df=1000（`1000/128 = 7`）只有 **1 层**，df=10000 有 3 层，df=100000 有 **4 层**。

`bufferSkip(df)` 决定一条 skip datum 写进哪几层，**先过 `windowLength` 这道闸**：

```java
int numLevels = 1;
if (df % windowLength == 0) {          // windowLength = skipInterval * skipMultiplier
  numLevels++;  df /= windowLength;
  while ((df % skipMultiplier) == 0 && numLevels < numberOfSkipLevels) {
    numLevels++;  df /= skipMultiplier;
  }
}
```

即 df=128 只写第 0 层，df=1024 写 0/1 层，df=8192 写 0/1/2 层。
层级越高条目越少（df=100000 时是 `781 / 97 / 12 / 1`），且**只有 1 层以上才有
child pointer**，落盘顺序是**自高层向低层**。

阅读端 `MultiLevelSkipListReader.skipTo` 先「上爬」到够得着 target 的最高一层：

```java
while (level < numberOfSkipLevels - 1 && target > skipDoc[level + 1]) level++;
```

判据用的是 `skipDoc[level + 1]`（更高层的当前 datum），所以 target=500 时
（更高层 datum 在 1024）**仍停在 level 0**，只有 target > 1024 才升到 level 1。

## 6. 运行

```bash
python python/selfcheck_postings.py   # 5934 条断言
python python/main.py                 # 演示输出
go run ./go                           # Go 侧（token/例外语义 + 跳表层数）
```

## 7. 参考资料（本轮实际读过的源文件）

- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/codecs/lucene104/ForUtil.java`（18 489 B，自动生成，勿手改）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/codecs/lucene104/PForUtil.java`（4 733 B）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/codecs/lucene104/Lucene104PostingsFormat.java`（27 005 B；`BLOCK_SIZE = ForUtil.BLOCK_SIZE`、`LEVEL1_FACTOR = 32`、`LEVEL1_NUM_DOCS = 8192`）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/codecs/lucene104/Lucene104PostingsWriter.java`（26 927 B；level0/level1 两条 `ByteBuffersDataOutput` 缓冲的用途说明）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/codecs/MultiLevelSkipListWriter.java`（7 040 B）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/codecs/MultiLevelSkipListReader.java`（8 776 B）
- <https://fulmicoton.com/posts/bitpacking/>（ForUtil 头部注释里点名的位打包思路出处）

> 口径：本文常量与分支均以 `main` 分支源码为准。§5b 的 `skipInterval=128` /
> `skipMultiplier=8` 是**举例值**（`MultiLevelSkipListWriter` 由调用方传入），
> 并非 Lucene104 的硬编码常量；Lucene104 实际用的是 §5a 的 `LEVEL1_FACTOR = 32`。
