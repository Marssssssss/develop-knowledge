# iOS · 事件循环

研究 RunLoop(Foundation + CoreFoundation 双 API)、UIKit 主循环、port-based 进程间通信等。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [RunLoop/](./RunLoop/) | modes / sources / timers / observers(6 个 Activity + 5 个标准 mode + Timer 生命周期 + CFRunLoopObserver) |
| [NotificationCenter投递/](./NotificationCenter投递/) | 同步投递(`queue=nil` 走投递线程)、`name/object` 筛选、中心强持有 token 与 block 拷贝、一次性通知、weak self |

## 待研究

- [ ] CFRunLoopSource 自定义事件源(schedule/cancel/perform 三函数 context)
- [ ] Mach port / NSMachPort 跨进程通信
- [ ] main run loop 与 UIKit UI 事件分发的绑定
- [ ] CADisplayLink vs NSTimer 的 vsync 同步
- [ ] Combine 框架的 RunLoop / DispatchQueue 调度
- [ ] async/await 在 main run loop 上的 continuation 调度
- [x] NotificationCenter 的注册与投递语义 → demo 456
- [ ] `NotificationCenter.notifications(name:)` 异步序列与 main run loop 的协同