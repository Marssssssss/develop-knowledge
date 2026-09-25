# 设计模式与惯用法

> 模式的价值不在「记住 23 个名字」，而在**知道每个模式解决的是什么变化方向**，
> 以及——同样重要——**什么时候用了反而是负担**。

## 核心研究主题

- **分类与动机**：创建型（对象怎么生出来）/ 结构型（对象怎么拼起来）/ 行为型（职责与协作怎么分）；
  每个模式的「变化点」在哪里（Refactoring.Guru 目录列为 22 个经典模式，另有 criticism 章节）
- **语言特性对模式的替代**：一等函数吞掉策略/命令、生成器吞掉迭代器、模式匹配与代数数据类型
  削弱访问者、鸭子类型让适配器退化——先看语言，再谈模式
- **模式与 SOLID 的关系**：开闭原则靠的是「找到正确的抽象」，不是靠套模式
- **反模式与过度设计**：单例当全局变量（隐藏依赖、难测）、工厂爆炸（每个类配一个工厂）、
  God Object、Anemic Domain Model
- **命名的沟通价值**：模式名是团队的压缩词汇，但只应在结构真的吻合时使用

## 已完成 demo

| demo | 主题 |
| --- | --- |
| [单例代价与依赖注入/](./单例代价与依赖注入/) | 双问题违反 SRP、线程安全、测试困难的结构性原因；Fowler 定位器 vs 注入口径 |
| [策略与条件分支取舍/](./策略与条件分支取舍/) | Context 只认接口、运行时切换、注册零改动；分支数×调用点数的账本模型 |
| [观察者与事件分发/](./观察者与事件分发/) | 订阅机制两要素、快照迭代防跳项、分发器演化为 pub/sub、解耦代价 |
| [访问者双分派与表达式问题/](./访问者双分派与表达式问题/) | 重载落基类反例、accept 双分派、加操作 vs 加节点两轴取舍 |
| [模板方法与钩子/](./模板方法与钩子/) | 骨架锁定、三类步骤、默认压制的 LSP 风险、模板(继承) vs 策略(组合) |

## 待研究

- [x] 单例 vs 依赖注入：可测试性与生命周期管理的取舍，什么场景下单例仍然合理 → 723 单例代价与依赖注入
- [x] 策略模式 vs 条件分支：分支数量到多少才「值回」抽象成本（可操作的判据）→ 724 策略与条件分支取舍
- [x] 观察者/发布订阅：解耦收益 vs 隐式控制流带来的调试困难 → 725 观察者与事件分发
- [ ] 装饰器 vs 继承 vs AOP：横切关注点三种实现的组合性差异
- [x] 访问者 vs 模式匹配：表达式求值场景下增加操作与增加类型哪个更频繁 → 726 访问者双分派与表达式问题
- [x] 模板方法 vs 回调/钩子：基类膨胀问题 → 727 模板方法与钩子
- [ ] 空对象/可选类型：语言有 `Option` 时还需不需要 Null Object（refactoring.guru 站内无独立页面，待权威源后做）
- [ ] 命令模式三种 undo 的代价边界：备份快照（历史越长内存越贵）/ 逆操作（差值事件可逆、「设为 110 元」不可逆）/ 事件日志重放——命令对象落盘演化为 Event Sourcing 的临界点
- [ ] 状态模式 vs 表驱动状态机：状态少但每态行为丰富选状态类，状态×事件多但迁移简单选 `(状态, 事件)→(次态, 动作)` 迁移表；何时退化回 switch 即可（判据：状态数与迁移变更频率）
- [ ] 外观 vs 中介者的边界：Facade 是客户端对「无感知被动子系统」的单向入口简化，Mediator 是同事组件间双向协调且相互知晓——判据是「简化访问入口」还是「接管交互网络」，以及中介者退化为 God Object 的前兆
- [ ] 享元、对象池、字符串驻留三种共享的机制差异：内在态/外在态分离多客户端共享 vs 独占借出归还 vs 每个值一个规范实例（identity 语义）——字形共享、连接池、`String.intern()` 的 `==` 判等对照
- [ ] 原型模式的深浅拷贝陷阱：Java `clone()` 默认浅拷贝导致引用字段共享、`Cloneable` 是不提供 `clone()` 的坏味道标记接口——copy 构造器/序列化深拷贝替代与「哪些字段该共享」的逐字段决策
- [ ] 迭代器失效三语言对照：C++ vector 扩容使全部迭代器失效且使用即 UB（无运行期检测）、Java fail-fast 靠 modCount 尽力抛 ConcurrentModificationException、Rust 借用检查编译期拒绝——同一 bug 的三种代价等级
- [ ] RAII 与 scope guard：「持有资源是类不变式」靠 C++ 析构/Rust Drop 确定性释放，对比 try-with-resources / using / with 的显式作用域语法——异常/panic 安全的锁释放 demo 与「资源获取次数远多于资源种类」的省码论断验证
- [ ] Go functional options vs Builder vs 配置结构体：变长 `Option` 函数如何在加参数时不破坏调用方（API 演进判据）、Builder 的分步构建与校验时机、config struct 一次到位——10 个可选参数的同一构造器三实现对比
- [ ] Rust typestate：consuming self + `PhantomData` + 零大小状态标记类型把状态机编进类型（HTTP 响应只能按「状态行→头部→体」推进，乱序直接编译失败）；依赖 move 语义故 C++ 难移植
- [ ] 责任链 vs HTTP 中间件管线：经典 CoR 被某 handler 处理即终止，中间件必须 `next()` 贯穿到端点、不调 `next()` 即短路、传值跳错误处理（洋葱模型双向包裹）——两种控制流的等价改写

## 参考资料（已读）

- [Refactoring.Guru — Design Patterns（模式目录按意图分创建型/结构型/行为型三组，站点含 Criticism 与分类讨论）](https://refactoring.guru/design-patterns)
- [The Typestate Pattern in Rust — cliffle.com（consuming self + `PhantomData` + 零大小状态标记的完整机制；C++ moved-from 仍可访问导致的移植难题与 `r = r.header(...)` 循环绕行）](https://cliffle.com/blog/rust-typestate/)
- [Refactoring.Guru — Command（`saveBackup()` 备份字段 + CommandHistory 栈的 undo 结构；命令可序列化进队列/数据库；与 Memento/Strategy 的搭配与区别）](https://refactoring.guru/design-patterns/command)
- [Refactoring.Guru — State（状态模式与 FSM 的对应关系；条件分支法随状态增长的膨胀路径；「状态很少或 rarely changes 时属过度设计」的官方警告）](https://refactoring.guru/design-patterns/state)
- [Martin Fowler — Event Sourcing（差值事件可逆、设值事件不可逆；complete rebuild/时间点查询/快照三种回放；外部网关与重放模式的代价清单）](https://martinfowler.com/eaaDev/EventSourcing.html)
- [RAII — Wikipedia（「持有资源是类不变式」；C++/Rust Drop 与 Java try-with-resources/C# using/Python with 对照；SBRM 即 scope guard）](https://en.wikipedia.org/wiki/Resource_acquisition_is_initialization)
- [Express — Writing middleware（`next()` 的挂起规则、按加载顺序执行、不调 `next()` 即短路、传非 'route' 值跳错误处理、洋葱模型）](https://expressjs.com/en/guide/writing-middleware.html)
