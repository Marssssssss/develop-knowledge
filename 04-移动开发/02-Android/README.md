# Android

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [并发编程/](./并发编程/) | Handler / Looper / HandlerThread / Coroutines / Flow / WorkManager |
| [组件生命周期/](./组件生命周期/) | Activity 启动模式 / Fragment / Service / ViewModel |
| [UI框架/](./UI框架/) | Compose 重组 / Compose 状态 / View 体系 |
| [网络编程/](./网络编程/) | OkHttp 拦截器链 / 连接池 / Retrofit 适配 |
| [NDK与JNI/](./NDK与JNI/) | JNI 符号 mangling / 引用生命周期 / RegisterNatives |
| [数据持久化/](./数据持久化/) | Room 连接池 / PRAGMA 配置 / 迁移事务 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 027 | `并发编程/Handler消息机制/` | Handler / Looper / MessageQueue 异步消息机制(post / sendMessage / HandlerThread / postDelayed / 内存泄漏修复) | Kotlin / Java |
| 028 | `组件生命周期/Activity启动模式/` | Activity 启动模式(standard / singleTop / singleTask / singleInstance / singleInstancePerTask + Intent flags 优先级) | Kotlin / Java |
| 029 | `UI框架/Compose重组/` | Jetpack Compose 重组机制(mutableStateOf / remember / derivedStateOf / lambda modifier / Backwards write 反模式) | Kotlin |
| 282 | `网络编程/OkHttp拦截器与连接池/` | OkHttp 拦截器链与连接池(应用/网络拦截器分界 + 7 层内置链顺序与短路 + 池 5 空闲/5 分钟 + Address/Route/Connection + Happy Eyeballs) | Python / Kotlin / Java |
| 283 | `并发编程/协程上下文与Flow/` | Kotlin 协程上下文与 Flow(CoroutineContext 右侧覆盖合并 + 调度器 Default/IO/Unconfined + 结构化并发失败传播 + 冷流 vs 热流 + `flowOn` 只影响上游) | Python / Kotlin |
| 284 | `并发编程/WorkManager约束与重试/` | WorkManager 约束与重试(约束全 AND 且默认 false + 退避 30s 起步 clamp 5h + 一次执行 10 分钟窗口 + 唯一任务 KEEP/REPLACE/APPEND) | Python / Kotlin / Java |
| 285 | `组件生命周期/Fragment生命周期与ViewModel/` | Fragment 生命周期与 ViewModel 作用域(`mState` 9 态含 `AWAITING_*` 过渡态 + 视图生命周期 vs Fragment 生命周期 + `viewLifecycleOwner` 抛错边界 + `viewModelScope` 早于 `onCleared` 取消) | Python / Kotlin |
| 286 | `组件生命周期/Service三种形态/` | Service 三种形态(started/bound/foreground + `onStartCommand` 返回值四档重建策略 + `stopSelf(startId)` 排序语义 + 前台服务权限与 type 子集) | Python / Kotlin / Java |
| 507 | `UI框架/ComposeNavigation类型安全路由/` | 类型安全路由与嵌套图(RouteBuilder 的 PATH/QUERY 单判据 + RouteEncoder 的 `Map<String, List<String>>` + RouteDecoder 跳过缺失元素 + startDestination 必须是直接子节点) | Python / Kotlin |
| 508 | `NDK与JNI/JNI互调机制/` | JNI 互调机制(短名/长名 mangling 与转义表、局部引用随方法返回失效、全局与弱全局、`PushLocalFrame`、`RegisterNatives` 与 JNI 版本协商) | Python / Kotlin / C |
| 509 | `网络编程/Retrofit动态代理与适配器/` | Retrofit 动态代理与适配器(`create()` 接口校验 + InvocationHandler 三分支 + RequestFactory 注解规则 + suspend 的 Call 改写 + `nextCallAdapter` skipPast 与 Platform 分支) | Python / Kotlin |
| 510 | `数据持久化/Room连接池与ORM映射/` | Room 连接池与打开流程(Single/Multiple 配置、选池四路分支、500ms 退避与破坏性恢复、`journal_mode`/`synchronous` 与迁移事务、AOSP 连接池主连接与 30s 阈值) | Python / Kotlin |
| 511 | `UI框架/Compose快照与LazyColumn复用/` | Compose 快照与 LazyColumn 复用(StateRecord 多版本链 + 碰撞写定义 + `innerApplyLocked` 三分支合并 + `PreexistingSnapshotId` 豁免 + 空集/钳位/beyondBounds + key 驱动的位置修正) | Python / Kotlin |

## 待研究

- [x] OkHttp / Retrofit 原理(连接池 + 拦截器链 + 协程适配)→ demo 282
- [x] Kotlin Coroutines + Flow(协程上下文 + 调度器 + Flow 冷流)→ demo 283
- [x] WorkManager(后台任务调度 + 约束条件 + 持久化)→ demo 284
- [x] Fragment 生命周期 + ViewModel 作用域 → demo 285
- [x] Service 启动 / 绑定 / 前台服务 → demo 286
- [x] Compose Navigation(类型安全路由 + 嵌套图)→ demo 507
- [x] Android JNI / NDK(Java ↔ C++ 互调)→ demo 508
- [x] Retrofit 动态代理与 CallAdapter / Converter 解析 → demo 509
- [x] Room 的 ORM 与连接池 → demo 510
- [x] Compose 快照系统与 `LazyColumn` 复用 → demo 511
- [ ] HTTP/2 多路复用与流控
- [ ] `GetPrimitiveArrayCritical` 的 critical section 与 pin 开销
- [ ] DataStore 与 SharedPreferences 的事务语义对比
- [ ] Room 编译期 DAO 校验与 `EntityUpsertAdapter`
- [ ] WorkManager 的加急任务与进程间调度
