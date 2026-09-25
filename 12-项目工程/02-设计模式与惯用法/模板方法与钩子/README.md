# 模板方法与钩子

> 模板方法把**算法骨架**放进基类、把**步骤**留给子类:结构只在一处定义,
> 变化只发生在指定的缝里。它的三个官方代价(骨架限制、LSP 风险、步骤多难维护)
> 都来自同一件事——你把结构当成了契约。

## 1. 结构与三类步骤(refactoring.guru Template Method 页)

```text
template_turn():   # 子类不得覆写
    before_build_hook()   # 钩子:空体,不复写也能跑,放在关键步骤前后
    collect_resources()   # 默认:基类实现,可覆写
    build_structures()    # 抽象:子类必须实现
    build_units()         # 抽象
    attack()              # 默认
```

| 步骤类型 | 语义 | 不实现的后果 |
| --- | --- | --- |
| 抽象 | 子类必须交代 | 骨架一跑就 NotImplementedError |
| 默认 | 共享实现,可覆写 | 直接用基类的 |
| 钩子 | **空体可选**,关键步骤前后的扩展点 | 无感,骨架照常工作 |

结构变更(调步骤顺序/加减步骤)只改基类一处——这是模板方法全部的收益来源。

## 2. 两个官方代价的活标本

- **LSP 风险**:MonstersAI 把 `collect_resources` 压制成空——它"是一台游戏 AI",
  却不再"会采集资源";压制默认实现是最隐蔽的里氏替换违约;
- **骨架限制**:子类只能在被允许的缝里变化;当多个子类开始互相配合地覆写
  同一组步骤,说明要拆的是**算法本身**,不是再开一条缝。

## 3. 与策略的分工(官方 Relations)

| | Template Method | Strategy |
| --- | --- | --- |
| 机制 | 继承 | 组合 |
| 刻度 | **类级,静态**(编译期定型) | **对象级,运行时可换** |
| 变化单位 | 算法的一段步骤 | 整个算法对象 |

Factory Method 是 Template Method 的特化——"创建对象"这一步被做成钩子族。

## 自检

`python python/selfcheck_templ.py` —— 5 项断言:骨架锁定与步骤序列 /
抽象步骤缺失即失败 / 三类步骤语义(含钩子置位与默认压制)/
模板vs策略分工 / 维护性警示。Go 侧 `go/main.go` 用**函数字段组合**复刻同构骨架
(Go 无继承——这也顺带展示了模板方法的组合化等价形态)。

## 参考资料(实读)

- [Refactoring.Guru — Template Method](https://refactoring.guru/design-patterns/template-method)
