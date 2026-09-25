# 单例的代价与依赖注入

> 单例是最流行也最受批评的模式:它**一次解决两个问题**(唯一实例 + 全局访问点),
> 这本身就违反单一职责。本 demo 把单例的四个官方缺点做成可执行断言,
> 再用 Fowler 的原文对比"服务定位器 vs 依赖注入"两条出路。

## 1. 单例解决了什么(以及代价)

保证(refactoring.guru Singleton 页):

- 全局唯一实例;**首次请求才初始化**(lazy)——普通构造器做不到(构造必须返回新对象);

四条代价(官方 Cons):

| 代价 | 说明 |
| --- | --- |
| 违反单一职责 | 一次解决两个问题 |
| 掩盖坏设计 | 组件互相知道太多时,单例把耦合藏进全局 |
| 多线程要特殊处理 | 并发首用可能创建多个实例(锁 + 双检) |
| **难单元测试** | 私有构造 + 类方法不可覆盖,mock 只能 hack 全局 |

## 2. Fowler:定位器 vs 注入

> "The choice between them is less important than the principle of
> **separating configuration from use**."

| | Service Locator | Dependency Injection |
| --- | --- | --- |
| 获取方式 | 应用类**显式向定位器要** | 服务"自己出现"在类里(控制反转) |
| 依赖可见性 | 每个使用者都**依赖定位器本身** | 构造签名即完整依赖清单 |
| mock | 换实现要经定位器 | 直接当参数塞进构造器 |
| 代价 | 多一跳间接 | IoC 难理解、调试绕——"需要自我证明" |

注入三种形态:**构造 / Setter / 接口注入**;注意"控制反转"是大概念,
依赖注入只是它的一个子集(容器、框架都是 IoC)。

## 3. 什么场景单例仍然合理

- 真正的进程级唯一资源(日志器、配置加载后的只读快照);
- 生命周期 = 进程生命周期、无状态或不可变;
- 不需要按测试替身——否则优先 DI(即使"DI 容器"不引,手工构造注入也成立)。

## 自检

`python python/selfcheck_sidi.py` —— 6 项断言:唯一实例与 lazy /
线程安全模拟 / 测试困难的结构性原因 / 定位器把"定位器"本身带进依赖清单 /
注入的依赖可见性 / 三形态与 Fowler 原则。Go 侧 `go/main.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [Refactoring.Guru — Singleton](https://refactoring.guru/design-patterns/singleton)
- [Martin Fowler — Inversion of Control Containers and the Dependency Injection pattern(2004)](https://martinfowler.com/articles/injection.html)
