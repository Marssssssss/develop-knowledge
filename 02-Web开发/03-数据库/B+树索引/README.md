# B+ 树索引（B+ Tree Index）

## 简介

B+ 树是关系数据库与 KV 存储的事实标准索引结构。它是一种**自平衡的 M 路搜索树**，所有数据只存在叶子节点，叶子节点用**兄弟指针**串成有序链表；内节点只做路由（separator keys）。这带来三大工程收益：

1. **稳定的 O(log<sub>M</sub> N) 查找** —— 磁盘场景下每个层级只需一次 I/O；
2. **极快的范围扫描** —— 命中起点后，沿兄弟链 O(k) 扫出整段结果（k = 匹配行数）；
3. **可预测的页利用率** —— 除根外每个节点至少半满，磁盘浪费可控。

**关键概念（5 个）**

- **Order（阶 M）** —— 内节点最多 M 个子节点、最多 M-1 个 separator key。
- **Separator key** —— 内节点路由键，约定 `children[i+1]` 的最小键 = `keys[i]`（OpenDSA 7.2.1.1）。
- **Sibling pointer** —— 叶子的 `next` 指针，串成有序链表，专为范围扫描设计。
- **Copy-up vs push-up** —— 叶子分裂把最小键**复制**给父节点；内节点分裂把中间键**上推**给父节点（中间键分裂后从两边都消失）。
- **Underflow & rebalance** —— 删除导致键数 < ⌈M/2⌉-1 时，**borrow**（从兄弟借 1 个）失败就 **merge**（合并 + 删父分隔键）。

**历史背景**：Bayer & McCreight 1972 年为 Boeing 提出 B-tree；B+ 变体（Knuth《TAOCP》Vol.3 详述）将数据下沉到叶子、加 sibling pointer，演化为 InnoDB clustered index / SQLite / PostgreSQL btree / WiredTiger / LMDB 等存储引擎的索引事实标准。

## 原理详解

### 1. 工作机制（按操作分步）

**Search（点查）** 1) 从根起对 `keys[]` 二分定位 `i` = 第一个 `> k` 的位置；2) 下降到 `children[i]`；3) 重复到叶子后二分命中或 NOT FOUND。注意：**搜索必须落到叶子**（OpenDSA 7.2.1.1："internal node keys are merely placeholders"）。

**Insert** 1) 沿搜索路径下降到目标叶子 L，沿途压栈 `(parent, child_index)`；2) 把 `(k, v)` 二分插入 L；3) 若 L over-full 触发 **split_leaf**：`mid = nk/2`，左半 L 保留 `[0..mid-1]`，右半 new_leaf 拿走 `[mid..nk-1]`；`new_leaf.next = L.next; L.next = new_leaf`；**copy-up** key = `new_leaf.keys[0]`；4) 把 `(copy_up_key, new_leaf)` 插入父节点；若父节点 over-full 递归 **split_inner**：`mid = nk/2`，**push-up** key = `keys[mid]`（中间键从两边都消失），左半保留 `[0..mid-1]` + children `[0..mid]`，右半取 `[mid+1..nk-1]` + children `[mid+1..nk]`；5) 根本身分裂则新建单键根（高度 +1）。

**Delete** 1) 沿搜索路径下降删除 `k`；2) 若 L underflow，先**borrow**（叶子借首尾键 + 更新父分隔键；内节点走 rotate-through-parent）；3) 兄弟都没多余键则 **merge**（叶子合并并接 sibling chain；内节点拉父分隔键下来再吸收）；4) 递归向上检查父节点；5) 根因此空时把唯一子节点提升为新根。

**Range Query** 下降到 lo 对应的起始叶子，沿 `next` 兄弟链扫描到 `> hi` 为止 —— **O(log<sub>M</sub> N + k)**。

### 2. 核心数据结构

```
                       [inner keys=[7, 13]]                 <-- 内节点（separator）
                      /         |         \
        [inner 3, 5]        [inner 9, 11]      [inner 15, 17, 19]
       /    |    \          /    |    \          /    |    \    \
   [1,2]  [3,4]  [5,6]   [7,8] [9,10] [11,12] [13,14] [15,16] [17,18] [19,20]
     |      |      |       |      |      |       |      |      |       |
     +------+------+-------+------+------+-------+------+------+-------+
                sibling chain (sorted, via leaf.next)
```

### 3. 核心 API

| 角色 | Python | C | Go |
| --- | --- | --- | --- |
| 点查 | `search(root, k)` | `bpt_search(root, key)` | `Search(root, k)` |
| 范围 | `range_query(root, lo, hi)` | `bpt_range_query(root, lo, hi, ...)` | `RangeQuery(root, lo, hi)` |
| 插入 | `insert(root, k, v)` | `bpt_insert(root, key, value)` | `Insert(root, k, v)` |
| 删除 | `delete(root, k)` | `bpt_delete(root, key)` | `Delete(root, k)` |
| 批量建 | `bulk_load(pairs)` | `bpt_bulk_load(keys, vals, n)` | `BulkLoad(keys, vals)` |

`bulk_load` 系列要求输入已按 key 升序（这是它 O(N) 优势前提）。

### 4. 底层（数据库视角）

RDBMS 把 B+ 树节点映射到 4-16 KB 的磁盘页：每页容纳 `b = ⌊B / (k + p)⌋` 个键（B = 页字节、k = key 字节、p = 指针字节）。典型 h ≈ 3-5（b=100, N=10⁶ 时 h ≈ 3）。点查 I/O = h；范围扫描 = h + ⌈结果数 / (b/2)⌉。B-tree 把数据存在内节点会增大非叶节点、破坏 sibling chain，所以工程上 B+ 树是更优折中。

## 对比 / 选型

| 数据结构 | 查找 | 范围扫描 | 顺序构造 | 主要缺点 |
| --- | --- | --- | --- | --- |
| **B+ 树**（本 demo） | O(log N) | O(log N + k) 兄弟链 | O(N) bulk-load | 插入/删除需重新平衡 |
| B-tree 原版 | O(log N) | O(log N + k·log N) | O(N) | 数据在内节点，无 sibling chain |
| 红黑树 | O(log N) | O(log N + k) 中序 | O(N log N) | 节点度低 → 缓存局部性差 |
| Skip List | O(log N) 期望 | O(log N + k) | O(N log N) 期望 | 缓存局部性差 |
| Hash 索引 | O(1) 期望 | **不支持** | O(N) | 无序、不可范围 |
| **B\* 树** | O(log N) | 同 B+ | O(N) | 节点 ≥ 2/3 满，延迟 merge |

何时选 B+ 树：**需要顺序/范围扫描的持久化索引**。OLTP 范围查、KV 范围查、文件元数据（XFS / ReiserFS / JFS / ReFS / BFS 全用 B+ 树，见 Wikipedia B+ tree "Applications: Filesystems"）。

## 环境准备

- **OS**：跨平台（已在 Windows Git Bash 用 Python 实测；Linux/macOS 同理）
- **Python** ≥ 3.8 / **C** ≥ C99（`-Wall -Wextra -pedantic` 干净） / **Go** ≥ 1.22
- **依赖**：零外部依赖；仅标准库

## 运行方式

```bash
# Python
cd python && python3 demo.py            # 推荐：可视化 + 5 个 demo

# C
cd c && gcc -O2 -Wall -Wextra -pedantic main.c bptree.c -o bptree && ./bptree

# Go
cd go && go build . && ./bptree
```

预期输出（demo 1 节选）：

```
└── [inner keys=[7, 13]]
    ├── [inner keys=[3, 5]]
    │   ├── [leaf 1:v1, 2:v2] -> next
    │   ├── [leaf 3:v3, 4:v4] -> next
    │   └── [leaf 5:v5, 6:v6] -> next
    ...
```

## 关键代码片段

完整代码见各语言子目录；以下展示三个最容易出错的"教学片段"。

### Insert 的分裂传播（伪代码）

```c
/* 沿搜索路径压栈 */
Node *path[64]; int idx_path[64]; int depth = 0;
for (Node *n = root; !n->is_leaf; n = n->children[child_idx(n, k)]) {
    int i = child_idx(n, k);
    path[depth] = n; idx_path[depth] = i; depth++;
}
/* 叶子过满 -> 分裂 */
int pushed_key;  Node *pushed_node = NULL;
if (n->nk > MAX_KEYS) pushed_key = split_leaf(n, &pushed_node);
/* 向上传播；若根本身分裂则新建根 */
while (pushed_node && depth > 0) {
    depth--;
    par = path[depth]; idx = idx_path[depth];
    splice(par, idx, pushed_key, pushed_node);   /* O(nk) 移动 */
    if (par->nk <= MAX_KEYS) break;
    pushed_key = split_inner(par, &pushed_node); /* 中间键上推 */
}
if (pushed_node) { /* 根分裂 -> 新建根 */ nr.children = [par, pushed_node]; return nr; }
```

### Delete 的 borrow 优先于 merge

```c
if (idx+1 < parent->nk+1) {                            /* 先试右兄 */
    Node *R = parent->children[idx+1];
    if (R->nk > min_keys) {
        if (L->is_leaf) {                               /* 叶子：搬 R 首键到 L 尾；分隔键 <- R 新首键 */
            L->keys[L->nk] = R->keys[0]; ... R->nk--; parent->keys[idx] = R->keys[0];
        } else {                                        /* 内节点：rotate-through-parent */
            L->keys[L->nk] = parent->keys[idx];         /* 父分隔键下沉 */
            L->children[L->nk+1] = R->children[0];
            parent->keys[idx] = R->keys[0];             /* R 首键升入父 */
        }
        return 1;
    }
}
/* 否则合并到左兄（或吸收右兄），并删父分隔键 */
```

### Bulk-load（两阶段 O(N)）

```c
/* Step 1: 顺序切满叶子（按 MAX_KEYS） */
while (i < n) {
    if (cur->nk == MAX_KEYS) { leaves[nleaves++] = cur; cur = leaf_new(); }
    cur->keys[cur->nk] = keys[i]; strcpy(cur->vals[cur->nk], vals[i]); cur->nk++; i++;
}
/* Step 2: 自底向上建内层，直到只剩 1 个根 */
while (lvl_n > 1) {
    for (i = 0; i < lvl_n;) {
        Node *par = inner_new();
        par->children[0] = level[i++];
        for (j = i; j < lvl_n && par->nk < MAX_KEYS; j++)
            par->keys[par->nk] = level[j]->keys[0], par->children[++par->nk] = level[j];
        next[next_n++] = par; i = j;
    }
    level = next; lvl_n = next_n;
}
```

## 性能与边界

| 指标 | 量级 | 来源 |
| --- | --- | --- |
| 点查 | O(log<sub>M</sub> N) | CMU 15-445 L08 |
| 单次插入均摊 | O(log<sub>M</sub> N) | Cormen《Intro to Algorithms》18 |
| 范围扫描 | O(log<sub>M</sub> N + k/b) | OpenDSA 7.2.1.1 |
| Bulk-load | O(N) 自底向上 | Wikipedia B+ tree |
| 空间利用率 | ≥ 50%（除根外每个节点 ≥ ⌈M/2⌉-1 键） | OpenDSA 7.2.1.1 |
| 高度 | ⌈log<sub>⌈M/2⌉</sub>((N+1)/2)⌉ | CMU 15-445 L08 |
| 实测（M=4, N=20） | 树高 = 2（10 个叶子） | demo 1 输出 |

**平台/规模限制**

- 演示 M=4（每节点 ≤ 3 键，方便可视化）；生产 RDBMS 典型 M ≈ 100-200（一页 16 KB、key 8 B、指针 6 B 时 M ≈ 1100）。
- 单进程内存版（无持久化、无并发控制）。生产需追加：WAL、MVCC、latch/crabbing、prefix compression 等。

## 注意事项与常见坑

1. **叶子 copy-up vs 内节点 push-up**：叶子分裂上交的最小键在右半叶子中**仍存在**；内节点上交的中间键在两边都**消失**。混用会破坏 B+ 树的"数据只存在叶子"语义。
2. **根分裂必须用分裂后的左半作 `new_root.children[0]`**：调试本 demo 时栽过一次 —— 把最初下到的叶子 `L` 直接挂到新根，导致 `L` 既在 `new_root.children[0]` 又在 `pushed_node->children` 里，整棵树变成有向图。`new_root.children = [par, pushed_node]` 才是正确的（`par` 此时已被 split_inner 原地改成左半）。
3. **Bulk-load 的 O(N) 前提是输入已排序**。未排序请先排序或退回 N 次 `bpt_insert`（O(N log N)）。
4. **Delete 必须 borrow 优先于 merge**：CMU 15-445 / OpenDSA 都强调；merge 会让兄弟也 underflow，触发级联。生产 B\* 树（节点 ≥ 2/3 满）进一步推迟 merge。
5. **不要把 sibling chain 当子指针递归**：范围扫描只沿叶子 `next` 走，不递归子树 —— 否则最坏 O(N)。
6. **并发/持久化不在本 demo 范围**：真实数据库还需 crabbing latch / MVCC / WAL 等。
7. **Go 切片副本陷阱**：`copy(R.keys[:], L.keys[mid:L.nk])` 是值拷贝不是引用；漏 copy 会让左右两半共享底层数组，违反独立节点语义。
8. **演示 M=4 vs 生产 M≈100**：演示用 M=4 仅为打印可读；代码逻辑本身与生产一致，只调常量。

## 参考资料（实际阅读过的权威来源）

- [CMU 15-645 Database Systems, Lecture 08: Tree Indexes (Fall 2023, Andy Pavlo + Jignesh Patel)](https://15445.courses.cs.cmu.edu/fall2023/notes/08-trees.pdf) — B+ 树定义、M-way 性质（`M/2-1 ≤ #keys ≤ M-1`）、insert/delete 算法、leaf vs inner node 值方案（record IDs vs tuple data）。Demo 1 树形与 invariant 的精确依据。
- [OpenDSA 7.2 "B-Trees", CS3 Data Structures & Algorithms, Clifford Shaffer](https://opendsa.cs.vt.edu/ODSA/Books/anderson/cpsc2500/fall-2024/Final/html/BTree.html) — B+ 与 B-tree 关系、inner vs leaf 节点结构、`leaf.next` 链表 vs B-tree 对比、`B*` 树变体。提供 insert/delete/search 伪代码与图示，是"对比 / 选型"与"关键代码片段"的主要参照。
- [Wikipedia: B+ tree (en.wikipedia.org/wiki/B+_tree)](https://en.wikipedia.org/wiki/B+_tree) — `⌈(K+1)/2⌉ - 1` split 公式、bulk-loading 算法（sort + 满页 root + 右路传播）、删除三大步（redistribute/merge/pop parent key）、leaf 链表 vs B-tree 对比段落。
- [WiredTiger storage engine (MongoDB)](https://www.wiredtiger.com/) — 商业 B+ 树存储引擎，证实 B+ 树是当代 NoSQL（KV / 文档）的索引事实标准。
- [Andy Pavlo 个人主页 (CMU)](https://www.cs.cmu.edu/~pavlo/) — 15-445 课程主讲人；公开讲义把 CMU Database Group bustub 项目的工程细节下沉到课堂材料。

> 未引用任何内容农场或未经验证的转载；所有引用均为教科书/讲义/百科/工业产品官方页面。