# Android

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [并发编程/](./并发编程/) | Handler / Looper / HandlerThread / Coroutines / WorkManager |
| [组件生命周期/](./组件生命周期/) | Activity 启动模式 / Fragment / Service / ViewModel |
| [UI框架/](./UI框架/) | Compose 重组 / Compose 状态 / View 体系 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 027 | `并发编程/Handler消息机制/` | Handler / Looper / MessageQueue 异步消息机制(post / sendMessage / HandlerThread / postDelayed / 内存泄漏修复) | Kotlin / Java |
| 028 | `组件生命周期/Activity启动模式/` | Activity 启动模式(standard / singleTop / singleTask / singleInstance / singleInstancePerTask + Intent flags 优先级) | Kotlin / Java |
| 029 | `UI框架/Compose重组/` | Jetpack Compose 重组机制(mutableStateOf / remember / derivedStateOf / lambda modifier / Backwards write 反模式) | Kotlin |

## 待研究

- [ ] OkHttp / Retrofit 原理(连接池 + 拦截器链 + 协程适配)
- [ ] Kotlin Coroutines + Flow(协程上下文 + 调度器 + Flow 冷流)
- [ ] WorkManager(后台任务调度 + 约束条件 + 持久化)
- [ ] Fragment 生命周期 + ViewModel 作用域
- [ ] Service 启动 / 绑定 / 前台服务
- [ ] Compose Navigation(类型安全路由 + 嵌套图)
- [ ] Android JNI / NDK(Java ↔ C++ 互调)