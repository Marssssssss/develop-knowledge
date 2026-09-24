# Index-Only Scan 与可见性地图(VM)

> 索引扫描为什么还要回表:PG 索引里**没有元组可见性信息**(MVCC 在堆上)。
> Index-Only Scan 的全部戏法就是一张**可见性地图**:每堆页一个
> "该页全部行对所有当前与未来事务可见"的位。位在→免回堆;位不在→照样回堆。

## 1. 两个前置条件(缺一不可)

1. **索引类型支持**:B-tree 恒支持;GiST/SP-GiST 部分算子类;**GIN 不支持**
   (每个索引项只存原始值的一部分);要求索引能物理存出/重建原始值;
2. **查询只引用索引内列**:`SELECT x,y WHERE x=` 可以,`SELECT x,z` /
   `WHERE x= AND z<` 不行(z 不在索引里)。

物理上可行后,**经济上是否划算取决于 VM 命中率**。

## 2. 可见性地图(Vacuum 维护)

| 事件 | VM 位 |
| --- | --- |
| VACUUM 扫过页(确认全部可见) | 置位 |
| 页上发生任何修改 | 清零 |

- 位未置的页:IOS 必须回堆查可见性,**相对普通索引扫描毫无优势**;
- VM 每页只有 2 位(all-visible + all-frozen),比堆**小四个数量级**,常态全量驻留内存;
  VACUUM 自己也靠它跳过页(第二个用途)。

推论:频繁写的表 IOS 会退化成"索引扫描+回堆"双份代价;
**写多读少的表上建覆盖索引前先想清楚 VM 会被清多快**。

## 3. 覆盖索引与 INCLUDE

```sql
CREATE INDEX tab_x_y ON tab(x) INCLUDE (y);      -- y 是 payload
CREATE UNIQUE INDEX ... ON tab(x) INCLUDE (y);   -- 唯一性只约束 x
```

- payload **不参与搜索键**:y 的类型可以不是索引能处理的类型;
- 唯一索引的唯一性只作用于键列;
- 上层 B-tree 节点做后缀截断时会移除非键列,显式 INCLUDE 让上层元组稳定变小;
- 代价:宽 payload 膨胀索引、可能撞索引元组大小上限——须保守。

## 4. 两个进阶可 IOS 的情形

- **部分索引**:`CREATE INDEX ... WHERE success` 上执行
  `SELECT target WHERE subject=? AND success`——`success` 不在索引列里,
  但索引全体条目蕴含 success=true,**无需运行期 recheck**(9.6+ 识别);
- **表达式索引** `f(x)`:规划器目前"不聪明"——它要求查询需要的列全部可从索引取得,
  `f(x)` 不算取得了 `x`,需 `CREATE INDEX ... (f(x)) INCLUDE (x)` 显式补救。

## 自检

`python ionly_check.py` —— 6 项断言:官方四例的资格判定 / VM 置位-清零-回堆计数 /
VM 体量四个数量级 / INCLUDE 键与唯一性 / 部分索引谓词蕴含免 recheck /
表达式索引的规划器缺口。Go 侧 `ionly.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [PostgreSQL 18 — 11.9. Index-Only Scans and Covering Indexes](https://www.postgresql.org/docs/current/indexes-index-only-scans.html)
- [PostgreSQL 18 — 24.1.4. Updating the Visibility Map](https://www.postgresql.org/docs/current/routine-vacuuming.html#VACUUM-BASICS)
