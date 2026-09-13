# Neo4j 存储层：定长记录 + 双向链表

## 简介

Neo4j 作为**原生图数据库**，把节点与关系都存为**定长记录**（fixed-size records），实现"无索引邻接"（index-free adjacency）：给定一个节点 id，O(1) 计算出记录在文件中的字节偏移；给定节点，沿"关系链表头 + 关系的 next_out/next_in 指针"遍历整层邻接，**与全图大小无关**。

这是 Neo4j 宣称 "constant time traversals in big graphs for both depth and breadth" 的物理基础。

本 demo 用 **C + Python** 双版本复现：
- **NodeRecord**：15 字节定长（KB 文章 "neostore.nodestore.db 15 B"）
- **RelRecord**：34 字节定长（"neostore.relationshipstore.db 34 B"），包含 4 个链表指针（prev_out / next_out / prev_in / next_in），节点 N 出度上的邻接遍历只需 O(d)。

## 原理详解

### 1. 文件级布局

Neo4j 用 4 个核心文件存图：

| 文件 | 记录大小 | 内容 |
|---|---|---|
| neostore.nodestore.db | 15 B | 节点记录 |
| neostore.relationshipstore.db | 34 B | 关系记录 |
| neostore.propertystore.db | 41 B | 节点/关系的属性 |
| neostore.propertystore.db.strings | 128 B | 字符串属性值 |

> 引用 Neo4j KB："We use fixed record lengths to persist data and follow offsets in these files to know how to fetch data to answer queries."

### 2. NodeRecord 布局

```
[ in_use: 1B ] [ labels_bits: 1B ] [ first_rel_id: 4B ] [ first_prop_id: 4B ] [ extra: 5B ]
```

- **in_use**：是否被占用（删除只是把 in_use 置 0）
- **labels_bits**：demo 用 8 bit 表达 0~8 个标签；真实 Neo4j 用 B+ 树索引
- **first_rel_id**：节点上**第一个关系**的 rid（出/入链表的头）
- **first_prop_id**：节点上**第一个属性**的 pid

### 3. RelRecord 布局

```
[ in_use: 1B ] [ type_id: 1B ] [ src: 4B ] [ dst: 4B ] [ first_prop: 4B ]
[ prev_out: 4B ] [ next_out: 4B ] [ prev_in: 4B ] [ next_in: 4B ] [ extra: 2B ]
```

- **prev_out / next_out**：起始节点 src 上的双向链表指针
- **prev_in / next_in**：终止节点 dst 上的双向链表指针

> 引用 neo4j-contrib Glossary："The relationship chain is a doubly linked list that contains next and previous pointers to relationship records for both the start and end nodes of a given relationship record."

### 4. 双向链表 = 双重身份

一条关系在两个节点上各维护一个双向链表：src 的 OUT 链表 + dst 的 IN 链表。因此：
- **删除关系只需重链指针**（4 个 prev/next + 2 个 head），O(1)
- **遍历邻接**：node.first_rel → rel.next_out → ... → O(d)，与全图大小无关

### 5. 与关系数据库对比

| 操作 | Neo4j(定长记录 + 链表) | RDB(JOIN) |
|---|---|---|
| 查询 N 度邻居 | O(d₁·d₂·...·dₙ) 跟随指针 | 多次 JOIN，O(N·log N) 或更高 |
| 创建关系 | 4 指针 + 2 head = 6 次写入 | 1 行（2 个数字） |
| 删除关系 | O(1) 重链 | 复杂（FK / 索引更新） |
| 适合规模 | 万亿级关系遍历 | 适合聚合、不适合图遍历 |

> Neo4j 文档原话："What happens every time we create a relationship? ... it costs us three, rather than one, to add a relationship to Neo4j. In contrast, in a relational database, you go to the join table ... But an upside is that we get the joins for free."

## 环境准备

- Python ≥ 3.8（仅用 struct 标准库）
- gcc (任意支持 C99 的版本)

## 运行方式

```bash
# Python
cd python && python neo4j_storage_demo.py

# C
cd c && gcc -O2 -Wall -Wextra neo4j_storage_demo.c -o demo && ./demo
```

## 关键代码片段

C 创建关系时挂双向链表（src.out + dst.in 头插）：
```c
static int new_rel(unsigned char type_id, int src, int dst) {
    RelRecord *r = &rels[rcount];
    memset(r, 0, sizeof(*r));
    r->in_use = 1; r->type_id = type_id; r->src = src; r->dst = dst;

    unsigned int old_out = nodes[src].first_rel;
    unsigned int old_in  = nodes[dst].first_rel;
    r->next_out = old_out;        /* 头插到 src.out 链表 */
    r->next_in  = old_in;         /* 头插到 dst.in  链表 */
    nodes[src].first_rel = rcount;
    nodes[dst].first_rel = rcount;
    if (old_out) rels[old_out].prev_out = rcount;  /* 旧头的前驱改为新 */
    if (old_in)  rels[old_in].prev_in   = rcount;
    return rcount++;
}
```

C 邻接遍历（node.first_rel → rel.next_out → ...）：
```c
static void print_neighbors(int nid) {
    unsigned int cur = nodes[nid].first_rel;
    while (cur != 0) {
        printf(" (dst=%u, rid=%u)", rels[cur].dst, cur);
        cur = rels[cur].next_out;
    }
}
```

## 性能与边界

- **定长记录 → O(1) 定位**：第 N 号节点的字节偏移 = N × 15；第 M 号关系的字节偏移 = M × 34。
- **遍历复杂度**：O(d)，d 为该节点某类型上的度（与全图 V、E 无关）。
- **mmap 友好**：定长记录 + 顺序文件 → 整段 mmap 到 OS page cache，热数据命中率高。
- **本 demo 用结构体数组**模拟文件，demo 用 4 节点/3 关系示例；真实 Neo4j 可存 10⁹ 量级节点。

## 注意事项与常见坑

- **字节细节版本差异**：Neo4j 3.x / 4.x / 5.x 各版本 node/rel record 大小略有不同；本 demo 走核心思路（定长 + 双向链表），不保证与某版本 1:1 字节对齐。
- **双向链表 4 指针同步**：创建/删除关系时必须同时维护 prev_out/next_out/prev_in/next_in + 2 个 head；遗漏任一指针会破坏遍历。
- **属性链表**：本 demo 未实现 property 链表（neostore.propertystore.db），真实 Neo4j 还多一条属性单向链表。
- **Page Cache**：mmap 后 OS page cache 提供热数据；但冷启动后第一次访问会触发 page fault，延迟较高。
- **删除性能**：定长记录 + 链表让删除是 O(1)，但释放的空间会成为空洞（fragmentation）；Neo4j 用空闲列表（free-list）管理。

## 参考资料

- [Neo4j KB - Understanding Neo4j's data on disk](https://neo4j.com/developer/kb/understanding-data-on-disk/) — 文件布局 / 记录大小 / 4 MB+3 MB 等示例数据
- [neo4j-contrib Glossary](https://github.com/neo4j-contrib/neo4j-org/wiki/Glossary) — Relationship Chain / Node Record 的 14-15 B 字节细节
- [Angles & Gutierrez - Demystifying Graph Databases (arXiv 1910.09017v6)](https://arxiv.org/pdf/1910.09017v6) — Neo4j 与 Sparksee/DEX/GBase 的存储对比
- [Neo4j Blog - The secret sauce of Neo4j: Modeling and querying graphs](https://neo4j.com/?p=123541/) — 写入放大 3 倍 / 读取 O(1) 的设计权衡