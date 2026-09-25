# MongoDB 背包文档建模与更新操作符

> 背包是"整包读写 + 局部扣加"的矛盾体。MongoDB 的三个更新操作符
> ($set/$inc/位置 $)各自的边界,直接决定背包该怎么建模才不踩坑。

## 1. $set(官方手册语义)

- 字段不存在则**新增**(不违反类型约束时);
- **点路径顺手创建嵌套文档**:`$set {"bag.slots": 30}` 会把不存在的 bag 建出来;
- 一次 $set 多个字段同文档原子生效。

## 2. $inc(官方手册语义)

| 场景 | 行为 |
| --- | --- |
| 正数/负数 | 皆可(扣款就是负增量) |
| 字段不存在 | **创建并直接置为增量值**(不是先建 0 再加) |
| 字段为 **null** | **报错**——存过 null 的字段不能再 $inc |
| 原子性 | "atomic operation within a single document"(跨文档要事务) |

## 3. 位置操作符 $(官方手册语义)

> "acts as a placeholder for the **first element** that matches the
> query document"(且数组字段必须出现在查询条件里)

背包经典坑:同名道具两组时 `{"items.id": "potion"}` + `{"items.$.count": 99}`
**只改第一组**——不是业务逻辑错,是操作符语义就如此。
要精确到某组:查询条件里带上能唯一定位那组的字段,或改用 arrayFilters。

## 4. 建模取舍

| 建模 | 读 | 写 | 适配 |
| --- | --- | --- | --- |
| 数组内嵌(整包一个文档) | 一次读整包,快 | 位置 $ 只改第一组;大数组并发写竞争 | 道具不堆叠/组数少 |
| 一行一格(格子集合) | 整包要聚合查询 | 每格精确更新 | 高并发扣加、格子会拆分 |

位置 $ 的"只改第一组"踩坑,往往正是该从内嵌切到格子表的信号。

## 自检

`python python/bagops.py` —— 5 项断言:$set 点路径建嵌套 / $inc 正负与创建语义 /
null 报错与单文档原子性 / 位置 $ 只改第一组 / 建模取舍。
Go 侧 `go/bagops.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [MongoDB — $set(update operator)](https://www.mongodb.com/docs/manual/reference/operator/update/set/)
- [MongoDB — $inc(update operator)](https://www.mongodb.com/docs/manual/reference/operator/update/inc/)
- [MongoDB — $(positional update operator)](https://www.mongodb.com/docs/manual/reference/operator/update/positional/)
