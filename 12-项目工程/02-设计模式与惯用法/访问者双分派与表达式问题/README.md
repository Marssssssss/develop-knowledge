# 访问者、双分派与表达式问题

> 访问者要解决的核心尴尬:**遍历一组类型不同的对象时,重载无法按动态类型选方法**。
> 解法是双分派——把选择权交还给元素自己。它的收益与代价正好押在
> 软件变化的两个正交轴上(表达式问题)。

## 1. 重载为什么救不了(官方反例)

```text
foreach (node in graph) exporter.visit(node)
```

`node` 的静态类型是基类——重载在**编译期按静态类型**解析,一律落到 `visit(Node)`,
具体类型信息丢失。这也是 Java/C++ 里"用一组重载替代访问者"行不通的原因。

## 2. 双分派:accept 把选择权还给元素

```text
node.accept(visitor)  →  visitor.visit_具体节点(node)
```

元素知道自己是谁,回调访问者上**对应自己类型**的方法——不需要任何条件判断。

## 3. 两个变化轴(表达式问题)

| 变化 | 访问者刻度 | 封闭枚举+模式匹配刻度 |
| --- | --- | --- |
| **加一个操作**(求值/打印/CodeGen…) | 新增 1 个访问者类,既有代码零改动(OCP 高光) | 每处 match 都要补分支 |
| **加一个节点类型** | **每个**访问者都要改(官方 Cons 第一条) | 新增 1 个变体,零改动 |

两轴不可兼得是结构性约束。选型问题只有一个:**你的系统里哪种变化更频繁?**
编译器/AST 这类"操作天天加、节点很少加"的场景是访问者的主场;
节点频繁演化的领域模型则相反。Rust 的 `Option<T>` 正是后一种刻度的日常例证——
类型系统里**没有 null 引用**,取值必须先 `match`,变体集合封闭。

官方另一条代价:访问者可能**拿不到元素的私有字段与方法**。

## 自检

`python python/selfcheck_visitor.py` —— 5 项断言:重载落基类反例 /
accept 双分派对拍(eval=3、print="(1 + 2)") / 加操作零改动 /
加节点迫使所有访问者更新(TypeError 显式失败) / 两轴取舍。
Go 侧 `go/main.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [Refactoring.Guru — Visitor(含 Double Dispatch 反例与 Pros/Cons)](https://refactoring.guru/design-patterns/visitor)
- [Rust std::option — 无 null 引用,取值先模式匹配(封闭枚举刻度的佐证)](https://doc.rust-lang.org/std/option/)
