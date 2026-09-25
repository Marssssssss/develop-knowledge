# 策略模式与条件分支的取舍

> 策略解决的问题很具体:**同一段业务里,算法变体可替换**。
> 但"要不要上模式"是个经济学问题——本 demo 给出可算的账本,
> 而不是"多用模式"的口号。

## 1. 结构(refactoring.guru Strategy 页)

- **Context** 只持有 `Strategy` 接口引用,构造注入 + `set_strategy` 运行时切换;
- 每个 Concrete Strategy 一个类,只实现接口声明的那个方法;
- 客户端创建具体策略塞给 Context——Context 不知道具体类型与算法细节。

## 2. 与分支版对拍

两版结果一致;差别在**改动面**:

| 变更 | 条件分支版 | 策略版 |
| --- | --- | --- |
| 加一种算法 | 改每一处调用点的 if/elif 树 | 注册一个新类,Context 零改动(OCP) |
| 换算法 | 改调用点代码 | set_strategy 运行时换 |
| 看懂一种算法 | 在分支树里找 | 一个类一个文件 |

## 3. 账本与阈值(模型)

```text
分支版成本 ≈ 分支数 × 调用点数      (每处调用点都长着同一棵树)
策略版成本 ≈ 策略数 + 1 个接口      (一次性结构成本)
```

单一调用点、分支两三条:直接写分支更划算(为了一个调用点引入接口+多类是过度设计);
分支数与调用点数相乘增长、或需要运行时切换/组合策略:模式的结构成本开始回本。
存量代码的迁移入口:目录中的 **Replace Conditional with Polymorphism** 重构,
每砍一棵分支树做一次,不做一次性大改。

## 自检

`python python/selfcheck_strat.py` —— 4 项断言:接口版与分支版结果对拍 /
新策略注册零改动 / 成本账本与两个平衡点 / 迁移重构入口。
Go 侧 `go/main.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [Refactoring.Guru — Strategy](https://refactoring.guru/design-patterns/strategy)
- [Refactoring.Guru — 重构目录 Replace Conditional with Polymorphism](https://refactoring.guru/replace-conditional-with-polymorphism)
