# 631 · Redis ZSET 的跳表：层高、span 与 listpack 编码切换

> 归属：`07-数据存储/03-缓存`。Redis 的 ZSET 有两副面孔：成员少、元素短时是**一块连续内存的
> listpack**；一旦越过阈值就整体转成 **skiplist + dict**。本 demo 按 `redis/redis` unstable
> 分支源码实读，把「层高怎么摇」「span 怎么维护」「rank 为什么是 1-based」「什么时候换编码」
> 落成可执行代码（Python 可跑，Go 同构）。

## 一、事实来源（本轮实读，非凭记忆）

| 来源 | 拿到什么 |
| --- | --- |
| `redis/redis@unstable src/server.h` 678-679 | `ZSKIPLIST_MAXLEVEL 32`（注释：Should be enough for 2^64 elements）、`ZSKIPLIST_P 0.25`（Skiplist P = 1/4） |
| `redis/redis@unstable src/t_zset.c` 75-95 | `zslGet/Set/Incr/DecrNodeSpanAtLevel` 四个访问器 |
| `redis/redis@unstable src/t_zset.c` 254-259 | `zslRandomLevel` |
| `redis/redis@unstable src/t_zset.c` 265-330 | `zslInsertNode` 的 `update[]` / `rank[]` / span 三段更新 |
| `redis/redis@unstable src/t_zset.c` 345-389 | `zslUnlinkNode` / `zslDelete` |
| `redis/redis@unstable src/t_zset.c` 441-453 | `zslIsInRange` 的两处提前判空 |
| `redis/redis@unstable src/t_zset.c` 645-687 | `zslGetRank`、`zslGetRankByNode` |
| `redis/redis@unstable src/t_zset.c` 1405-1432 | `zsetTypeCreate` / `zsetTypeMaybeConvert` |
| `redis/redis@unstable src/t_zset.c` 1523-1531 | `zsetConvertToListpackIfNeeded` |
| `redis/redis@unstable src/t_zset.c` 1650-1665 | ZADD 里的 listpack 转换判定 |
| `redis/redis@unstable redis.conf` 2358-2359、`src/config.c` 3650/3654 | `zset-max-listpack-entries 128`、`zset-max-listpack-value 64` |

## 二、核心机制

### 1. 层高：`P = 1/4`，上限 32

```c
static int zslRandomLevel(void) {
    static const int threshold = ZSKIPLIST_P*RAND_MAX;   // 0.25 * 2147483647
    int level = 1;
    while (random() < threshold)
        level += 1;
    return (level<ZSKIPLIST_MAXLEVEL) ? level : ZSKIPLIST_MAXLEVEL;
}
```

三点容易写反：

- 循环里**没有层数上限判断**，夹取发生在 `return` 那一行。所以「摇到超过 32」是可能的，只是返回值被压平。
- 比较是**严格小于** `threshold`，`threshold` 本身（536870911.75）不满足 —— 因为 `random()` 返回整数，
  实际边界是 `536870912`。
- 老资料里常见的写法是 `(random()&0xFFFF) < (ZSKIPLIST_P*0xFFFF)`，**unstable 分支已经改成直接用
  `random()` 全值域**。本 demo 按新写法实现。

层高的期望：P(level ≥ 2) = 1/4，P(level ≥ k) = (1/4)^(k-1)，平均层数约 `1/(1-0.25) ≈ 1.33`。

### 2. `level[0].span` 不是 span（本 demo 最容易踩的一处）

unstable 分支把节点的 `levels` 与 `sdsoffset` 打包进 `level[0].span` 这一个字长里（`zskiplistNodeInfo`），
于是四个访问器全部对 level 0 做了特判：

```c
static inline unsigned long zslGetNodeSpanAtLevel(zskiplistNode *x, int level) {
    if (level > 0) return x->level[level].span;
    /* For level 0, if regular node, span is 1. If tail node, span is 0. */
    return x->level[0].forward ? 1 : 0;
}
static inline void zslSetNodeSpanAtLevel(zskiplistNode *x, int level, unsigned long span) {
    if (level > 0) x->level[level].span = span;      // level 0 上直接丢弃
}
```

后果是：**写入 level 0 的 span 是空操作，读取时永远返回 1（尾节点 0）**。
如果照抄教科书实现（level 0 的 span 也参与 `+1` / `-1` 运算），rank 会整体算错。

### 3. 插入：`update[]` 与 `rank[]` 是两套下标

```text
x = header
for i = zsl.level-1 .. 0:
    rank[i] = (i == zsl.level-1) ? 0 : rank[i+1]        ← 从上一层继承
    while compare(score, ele, x.level[i].forward) > 0:  ← 严格大于
        rank[i] += getSpan(x, i)
        x = x.level[i].forward
    update[i] = x
```

`rank[i]` 是「在第 i 层走到插入位置所跨过的节点数」，`rank[0]` 就是**插入位置前面有几个节点**。
随后三段更新：

```text
# 层高超过当前 zsl.level 时，补层的 update 指向 header，span 直接填 zsl.length
if level > zsl.level:
    for i = zsl.level .. level-1:  rank[i] = 0; update[i] = header; setSpan(header, i, zsl.length)
    zsl.level = level

# 1) 有该层的节点：新节点接手剩余跨度
for i = 0 .. level-1:
    setSpan(node,   i, getSpan(update[i], i) - (rank[0] - rank[i]))
    setSpan(update[i], i, (rank[0] - rank[i]) + 1)

# 2) 层高不够、够不到新节点的层：span 单纯 +1
for i = level .. zsl.level-1:
    incrSpan(update[i], i, 1)
```

注意 `rank[0] - rank[i]` 这一项：它是「从第 i 层的落点往下走到第 0 层落点，中间还差几个节点」。

`backward` 只在 level 0 上维护，且首节点的 `backward` 必须是 `NULL`（`update[0] == header` 的特判）。

### 4. rank：1-based，且有两种算法

```c
unsigned long zslGetRank(zskiplist *zsl, double score, sds ele) {   /* t_zset.c:645 */
    ...
    for (i = zsl->level-1; i >= 0; i--) {
        while (zslCompareWithNode(score, ele, x->level[i].forward) >= 0) {   /* >= 不是 > */
            rank += zslGetNodeSpanAtLevel(x, i);
            x = x->level[i].forward;
        }
        if (x != zsl->header && zslCompareWithNode(score, ele, x) == 0) return rank;
    }
    return 0;
}
```

- 用的是 **>= 0**（走到「相等」也要前进），与插入的 **> 0** 不同 —— 这是同一份代码里两处方向相反的判定。
- **找不到返回 0**，所以 rank 从 1 起。
- `zslGetRankByNode`（t_zset.c:672）走另一条路：从该节点沿**自己的顶层**一路跳到尾部累加 span，
  得到 `distance_to_end`，再 `rank = zsl->length - distance_to_end`。靠的是「尾节点的 span 为 0」。
  本 demo 对两种算法做了**逐节点对拍**，这是发现 span 维护错误最省事的探针。

### 5. 比较：NULL 是 +infinity

`zslCompareWithNode` 在参数为 NULL 时返回 `-1`（含义是「NULL 排在任何真实节点之后」），
这样下沉循环不需要额外判空。同分时按元素字典序排。

### 6. 编码切换：三条独立的触发路径

| 函数 | 条件 | 备注 |
| --- | --- | --- |
| `zsetTypeCreate(size_hint, val_len_hint)` | 两个 hint 都超过阈值才建 skiplist | 建对象时的**一次性**决定 |
| `zsetTypeMaybeConvert(zobj, size_hint)` | `listpack && size_hint > 128` | **只看 size_hint，不看 val_len_hint** |
| ZADD 里的判定 | `zzlLength+1 > 128` 或 `sdslen(ele) > 64` 或 `lpSafeToAdd` 失败 | 逐条命令的真实闸门 |
| `zsetConvertToListpackIfNeeded` | `zsl->length <= 128` 且 `maxelelen <= 64` 且 `lpSafeToAdd` | 反向，长度用 **<=** 不是 **<** |

三个容易记错的点：

- ZADD 里是 `zzlLength(zobj->ptr)+1 > 128`，也就是**插入第 129 个成员时才转**；插满 128 个仍然是 listpack。
- `zsetTypeMaybeConvert` 明明叫 "maybe convert"，却对**超长元素**无感 —— 传 `size_hint=100`
  即使 `val_len_hint=10000` 也不转换，真正拦下它的是后面 ZADD 里的 `sdslen(ele) > 64`。
- 反向转换同样有三个条件，且 `length <= 128`（不是 `< 128`），所以 129 个成员**不会**被降级。

## 三、运行

```bash
cd 07-数据存储/03-缓存/Redis跳表与有序集合
python python/selfcheck_skiplist.py   # 66 条断言
python python/main.py
cd go && go run skiplist.go zset_encoding.go main.go
```

## 四、断言设计

- **随机行为钉死**：`zslRandomLevel` 注入 `Seq([...])` 确定性源，分别验证「永不中奖 = level 1」、
  「中奖 2 次 = level 3」、「中奖 31 次 = 32」、「中奖 40 次仍被夹到 32」。
- **level-0 span 是空操作**（成对）：`getSpan(非尾节点, 0) == 1`、`getSpan(尾节点, 0) == 0`，
  再调用 `setSpan(节点, 0, 99)` 断言字段值没变。
- **两种 rank 算法对拍**：全 level-1 与多层混排两种结构下，逐节点断言 `rank_via_span == get_rank`。
- **顶层回收分两步**：删掉唯一的 4 层节点后 `zsl.level` 落到 2（因为还有一个 2 层节点），
  再删掉它才落到 1 —— 首版断言写成「直接落到 1」，是期望值算错，不是代码错。
- **编码转换成对构造**：`size_hint=200` 转 / `size_hint=100` 不转；元素长 64 不转 / 65 转；
  `safe_to_add=False` 转。
- **边界精确**：第 128 个成员后仍是 listpack、第 129 个才切；反向转换 129 个不降、128 个才降。

## 五、注意事项与口径

- 本 demo **不建模 listpack 的字节布局**，`lpSafeToAdd` 抽象成布尔参数（真实实现是
  `listpack.c` 里防 `size_t` 溢出的保护，本轮未读该文件），README 与代码均标注为口径。
- `zslGetNodeElement` 依赖节点内嵌 sds 的偏移（`sdsoffset`），本 demo 直接用 Go/Python 字符串。
- 真实 ZSET 是 **skiplist + dict 双索引**：dict 提供 O(1) 的 `ZSCORE`，skiplist 提供有序遍历；
  dict 里存的是 `zskiplistNode*`，两者共享同一个 sds。本 demo 只建模 skiplist 那一半。
- 源码里 `zslFree()` 有一句 `debugServerAssert(zsl->alloc_size == zmalloc_usable_size(zsl))`，
  说明 span/pointer 维护错误在 debug 构建下会被内存账本抓出来 —— 这也是本 demo 坚持对拍 rank 的理由。

## 六、参考资料（实际读过）

- <https://raw.githubusercontent.com/redis/redis/unstable/src/server.h> （`ZSKIPLIST_MAXLEVEL` / `ZSKIPLIST_P`）
- <https://raw.githubusercontent.com/redis/redis/unstable/src/t_zset.c>
- <https://raw.githubusercontent.com/redis/redis/unstable/src/config.c> （`zset-max-listpack-*` 默认值）
- <https://raw.githubusercontent.com/redis/redis/unstable/redis.conf>
