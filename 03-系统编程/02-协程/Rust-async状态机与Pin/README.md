# Rust async/await 状态机与 Pin/!Unpin

> Rust 的 async 函数编译成一个**状态机**(future);await 点即挂起点,
> 挂起时把"跨点存活的局部"搬进状态变体——**future 里可能藏着指向自己的指针**。
> Pin 就是为这个泥潭生的:把值钉在原地,让"移动即悬垂"变成类型系统拦得住的事。

## 1. 状态机降级(RFC 2394 口径)

```text
async fn work() {
    let path = get();          // 不跨 await:留在栈/寄存器
    let data = read(&path).await;   // ← 挂起点 1:path 存活 → 入变体
    let out = parse(&data).await;   // ← 挂起点 2:data 存活 → 入变体
}
```

- **每个 await 一个挂起态**:2 个 await → 3 个挂起态 + 完成态;
- 编译器只把**跨 await 存活的局部**存进变体——这是"async 让二进制变大"的精确来源;
- poll 就绪才推进:Pending/Ready 两个出口(Rust async book:线程昂贵、任务廉价,
  状态机是"任务"的实现载体)。

## 2. Pin 的三句话(std::pin 原文口径)

1. **"Types that pin data to a location in memory"**——保证值的地址不再变,
   尤其当"有一个或多个指针指着它"时才有用;
2. **Unpin 是默认**:绝大多数类型自动 Unpin,Pin 对它们**零约束**;
3. **Pin/Unpin 的交互看指向物(pointee)**:`Pin<Box<T>>` 的行为由 `T` 是否
   Unpin 决定,与 `Box` 自身无关——指针移动不影响堆上指向物的固定。

## 3. !Unpin 与自引用结构

- **PhantomPinned**:零大小标记类型,唯一效果是让包含它的类型变 !Unpin
  (官方 Unmovable 示例:`_pin: PhantomPinned` + "Suppress Unpin so that this
  cannot be moved out of a Pin once constructed");
- **Pin::new 只对 Unpin 安全**;!Unpin 走 unsafe 的 `new_unchecked` 或
   **Box::pin**(pinning Box:数据上堆,自然不移动);
- 自引用结构一旦移动,内部指针即悬垂——模型里把"移动后指针指向旧地址"
  直接演出来。

## 4. Drop 保证(官方"Subtle details"节)

Pin 的承诺延伸到析构:被固定的值**析构完成前不得被搬走/释放**。
经典反例:用 `ManuallyDrop` 包住类型再 drop 外层 Box——析构被抑制,
**违反 drop guarantee**(文档明言这是 unsound 的用法)。

## 自检

`python python/rust_async_pin.py` —— 6 项断言:状态机降级与跨点存活变量 /
Pin::new 的 Unpin 边界 / pointee 规则(Pin<Box<T>>) / 自引用移动悬垂被拦 /
Unpin 随便移 / PhantomPinned 零大小标记。Go 侧 `go/rust_async_pin.go`
为同语义复刻(静态审查)。

## 参考资料(实读)

- [Rust std::pin — 模块文档(含 Unmovable/自引用/Drop 保证示例)](https://doc.rust-lang.org/std/pin/)
- [Rust std::marker::PhantomPinned(!Unpin 标记)](https://doc.rust-lang.org/std/marker/struct.PhantomPinned.html)
- [RFC 2394 — Async/await 语法(降级为生成器/挂起点)](https://rust-lang.github.io/rfcs/2394-async_await.html)
- [Asynchronous Programming in Rust(全书版:任务/状态机/线程对照)](https://rust-lang.github.io/async-book/print.html)
