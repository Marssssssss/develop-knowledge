# Java HashMap：树化的两道门、resize 的三分支与 split 的三种结局

> 「HashMap 在链表长度到 8 时变成红黑树」这句话有两个隐藏前提：**表长必须已经 ≥ 64**，
> 而且**触发点在链表第 9 个节点**而不是第 8 个。扩容时树桶也不是简单地"跟着走"——
> 只有真的被拆成两半才需要重建红黑结构。
> 本 demo 把 OpenJDK `java.util.HashMap` 的常量与分支**逐条复刻**，让这些数字变成可断言的事实。

代码：`python/java_hashmap.py`（模型）、`python/selfcheck_java_hashmap.py`（99 条断言，实跑全绿）、
`python/main.py`、`go/`（同口径 Go 实现，四项静态检查通过）。

## 一、六个常量

| 常量 | 值 | 源码位置 | 说明 |
| --- | --- | --- | --- |
| `DEFAULT_INITIAL_CAPACITY` | `1 << 4` = 16 | 238 | |
| `MAXIMUM_CAPACITY` | `1 << 30` | 245 | |
| `DEFAULT_LOAD_FACTOR` | `0.75f` | 250 | |
| `TREEIFY_THRESHOLD` | 8 | 260 | 链表 → 树 |
| `UNTREEIFY_THRESHOLD` | 6 | 267 | 树 → 链表，**必须 < 8**（注释明写） |
| `MIN_TREEIFY_CAPACITY` | 64 | 275 | 注释要求至少 `4 * TREEIFY_THRESHOLD` |

6 与 8 之间留 1 的间隙不是随意的：如果两者相等，一个在 8 附近反复增删的桶会在
树化/退化之间来回抖动，每次都伴随一次内存分配。

## 二、`tableSizeFor`：被 Java 的移位规则坑过一次

```java
static final int tableSizeFor(int cap) {
    int n = -1 >>> Integer.numberOfLeadingZeros(cap - 1);
    return (n < 0) ? 1 : (n >= MAXIMUM_CAPACITY) ? MAXIMUM_CAPACITY : n + 1;
}
```

Java 的移位量会**对 32 取模**（long 则 64）。`cap = 1` 时 `cap - 1 = 0`，
`numberOfLeadingZeros(0) = 32`，于是 `-1 >>> 32` 实际执行的是 `-1 >>> 0`，结果是 `-1`，
落到 `n < 0` 分支返回 **1**。任何在 Python 里直接写 `(-1 & 0xFFFFFFFF) >> 32` 的移植都会得到 0
从而返回 1 —— 数值碰巧对了，路径却完全不同；`cap = 0` 时更是会算出别的值。

实测（`table_size_for`）：

```text
0 -> 1    1 -> 1    2 -> 2    3 -> 4    5 -> 8    7 -> 8    8 -> 8
9 -> 16   16 -> 16  17 -> 32  63 -> 64  64 -> 64  65 -> 128 1000 -> 1024
1<<30 -> 1<<30      1<<30+1 -> 1<<30    Integer.MAX_VALUE -> 1<<30
```

## 三、hash 扰动与槽位

```java
static final int hash(Object key) { int h; return (key == null) ? 0 : (h = key.hashCode()) ^ (h >>> 16); }
```

`h >>> 16` 是**无符号**右移，把高 16 位折进低 16 位；槽位是 `(n - 1) & hash`。
扰动只改低 16 位，高 16 位原样保留（demo 断言了这一点）。

一个反直觉的点：**扰动不是幂等的**——`spread(spread(h)) == h`。所以"先把 key 的 hash
预处理一次再交给 HashMap"这种写法会把扰动效果抵消掉。

## 四、`resize()` 的三个分支，`newThr` 只有一条路会翻倍

```java
if (oldCap > 0) {
    if (oldCap >= MAXIMUM_CAPACITY) { threshold = Integer.MAX_VALUE; return oldTab; }
    else if ((newCap = oldCap << 1) < MAXIMUM_CAPACITY &&
             oldCap >= DEFAULT_INITIAL_CAPACITY)
        newThr = oldThr << 1;               // 只有这一条路是"翻倍"
}
else if (oldThr > 0) newCap = oldThr;        // 构造时的 initialCapacity 被塞在 threshold 里
else { newCap = 16; newThr = 12; }           // 全默认
if (newThr == 0) newThr = (int)(newCap * loadFactor);   // 注意是截断
```

第二个分支里的 `oldCap >= DEFAULT_INITIAL_CAPACITY` 常被忽略：**表长小于 16 时
`newThr` 不翻倍，而是按 `loadFactor` 重算**。默认 0.75 下两者数值一致，所以看不出来；
换一个 loadFactor 才暴露。

`(int)(newCap * loadFactor)` 是**截断**：`newCap = 1` 时 `(int)0.75 = 0`，
于是 `new HashMap<>(0)` 会连着抬好几次容量（demo 实测）：

```text
初始          cap=0  thr=1        ← tableSizeFor(0) == 1
第 1 次 put   cap=2  thr=1        ← cap=1 时 thr=(int)0.75=0，插一个就超阈值
第 2 次 put   cap=4  thr=3
第 4 次 put   cap=8  thr=6
第 7 次 put   cap=16 thr=12
```

顶到 `MAXIMUM_CAPACITY` 之后 `threshold` 变成 `Integer.MAX_VALUE`、表**不再扩容**，
冲突只能靠链表/树硬扛。

## 五、树化的两道门

```java
// putVal：binCount 从 0 起算，新节点插在 binCount+1 的位置
if (binCount >= TREEIFY_THRESHOLD - 1)   // -1 for 1st
    treeifyBin(tab, hash);
...
final void treeifyBin(Node<K,V>[] tab, int hash) {
    int n, index; Node<K,V> e;
    if (tab == null || (n = tab.length) < MIN_TREEIFY_CAPACITY)
        resize();                        // ← 表太小时是扩容，不是树化
    else if (...) { /* 真的转成 TreeNode */ }
}
```

两个门槛叠起来的净效果是：

- **第 9 个**节点才调用 `treeifyBin`（`binCount` 从 0 开始，插入第 9 个时 `binCount == 7`）；
- 此时若表长 **< 64**，走的是 `resize()`——也就是说**小表上的长链表靠扩容来稀释，而不是靠树**；
- 只有表长 **≥ 64** 才真的树化。

demo 里用 9 个落到同一槽的 key 实测：16 槽表得到的是 `cap 16 → 32`、树桶 0 个；
64 槽表得到的是树桶 1 个、`cap` 不变。

另外 `treeifyBin` 先于 `putVal` 末尾的 `++size > threshold` 检查执行，
所以一次 put 最多可能触发两次扩容（树化门触发的那次 + 阈值门那次）。

## 六、`TreeNode.split`：只有拆成两半才重建树

扩容时每个桶按 `(e.hash & oldCap) == 0` 分成 lo / hi 两堆，lo 留在 `j`、hi 去 `j + oldCap`，
**保持原来的相对顺序**（尾插到 `loTail`/`hiTail`）。树桶走 `TreeNode.split`，结局有三种：

| 拆分结果 | lo 侧动作 | hi 侧动作 |
| --- | --- | --- |
| 两侧都 > 6 | `tab[j] = loHead` + `loHead.treeify()` | `tab[j+bit] = hiHead` + `hiHead.treeify()` |
| 只有一侧 | 原样搬过去，**不 treeify** | — |
| 某侧 ≤ 6 | `loHead.untreeify(map)` 退化成链 | 同左 |

关键的那一句在源码里是：

```java
if (lc <= UNTREEIFY_THRESHOLD) tab[index] = loHead.untreeify(map);
else {
    tab[index] = loHead;
    if (hiHead != null)          // (else is already treeified)
        loHead.treeify(tab);
}
```

**整棵树没被拆开时红黑结构原样搬过去仍然合法**，不重建。demo 用三种构造实测：
`[7|7]` 触发 2 次 treeify、`[9|0]` 触发 **0** 次、`[8|1]` 触发 1 次 untreeify。

## 七、选型结论

| 场景 | 该知道的 |
| --- | --- |
| 预知元素量 | 用 `new HashMap<>(expected / 0.75 + 1)` 一次到位，避开 log₂ 次 rehash；`tableSizeFor` 保证向上取到 2 的幂 |
| 会被攻击的 key 集合（大量同槽 hash） | 表长不到 64 之前**不会**树化，只靠扩容稀释；要抗碰撞请先把容量开过 64 |
| 大量增删改的桶 | 6/8 的双阈值是防抖设计，别把自己的代码写成"看到 8 个就去清理" |
| 容量接近 2³⁰ | `threshold` 顶到 `Integer.MAX_VALUE` 后不再扩容，此时 HashMap 退化成"数组 + 长链" |

## 参考资料（实际读过）

- `https://raw.githubusercontent.com/openjdk/jdk/master/src/java.base/share/classes/java/util/HashMap.java`
  （100 KB；读了六个常量的定义与 `MIN_TREEIFY_CAPACITY` 的注释、
  `hash()`、`tableSizeFor()`、`putVal()` 的 `binCount` 循环与两处 `binCount >= TREEIFY_THRESHOLD - 1`、
  `resize()` 的三分支与 lo/hi 拆分、`treeifyBin()` 的 `MIN_TREEIFY_CAPACITY` 判据、
  `TreeNode.split()` 的 `untreeify` / `treeify` 分支）
