# Handler / Looper / MessageQueue 消息机制

## 简介

Android 异步消息机制的核心四件套:`Handler`(消息处理器)、`Message`(消息载体)、`MessageQueue`(消息队列,单链表)、`Looper`(消息循环器)。每个线程通过 `Looper.prepare()` 创建一个 `Looper`(存入 ThreadLocal),`Looper.loop()` 进入无限循环从 `MessageQueue` 中取出 `Message`,分发到 `Message.target` 指向的 `Handler` 执行。

**关键概念清单**:
- **Looper**:每个线程一个,ThreadLocal 存储;`prepareMainLooper()` 由 ActivityThread 在应用启动时调用
- **MessageQueue**:单链表按 `when`(投递时间)升序;`nativePollOnce` epoll 阻塞唤醒
- **Handler**:绑定到创建时所在线程的 Looper;`send*` / `post*` 入队,`handleMessage` 执行
- **Message**:对象池复用(MAX_POOL_SIZE=50,静态 sPool 链表);`what / arg1 / arg2 / obj / target / callback` 五元组
- **同步屏障**(Sync Barrier):`Message.target = null` 的消息,后续同步消息被屏蔽,只跑异步消息;用于 UI 绘制优先

**历史背景**:Android 1.0(2008)起即存在的核心机制,ActivityThread.main() 中 `Looper.prepareMainLooper()` + `Looper.loop()` 构成整个应用主线程的事件循环;HandlerThread、IntentService、HandlerThreadPool、WorkManager 等现代并发组件均建立在此机制之上。

## 原理详解

### 工作机制分步说明

主线程消息循环的启动与运转(对应 Looper.java / ActivityThread.java):

1. **应用启动**:`ActivityThread.main()` 调用 `Looper.prepareMainLooper()` → `Looper.loop()`
2. **创建 Looper**:`prepare()` 把 `new Looper(true)` 存入 `sThreadLocal`,每个线程最多一个
3. **构造 MessageQueue**:`new MessageQueue(quitAllowed)`,内部通过 JNI 创建 `NativeMessageQueue`(对应 epoll fd)
4. **进入循环**:`for (;;) { msg = queue.next(); if (msg == null) return; msg.target.dispatchMessage(msg); }`
5. **阻塞等待**:`queue.next()` 内部调用 `nativePollOnce(ptr, timeoutMillis)` → epoll_wait 阻塞;新消息入队时 `nativeWake(ptr)` 唤醒
6. **消息分发**:`dispatchMessage()` 优先执行 `msg.callback`(Runnable),否则 `mCallback.handleMessage()`,最后才到子类 `handleMessage(Message)`

### 消息流转 ASCII 图

```
外部线程(T1)                            主线程(T0 = MainLooper)
─────────────                          ─────────────────────────
                                       
Handler(T0).post(Runnable)              
  │                                     
  ├─→ Message.obtain(h, callback)       
  │   └─→ sPool 复用或 new              
  │                                     
  ├─→ sendMessageDelayed(msg, 0)        
  │   └─→ msg.when = uptimeMillis()     
  │                                     
  └─→ MessageQueue.enqueueMessage(msg) ─→ 单链表按 when 升序插入
                                              │
                                              ▼
                                        Looper.loop() 死循环
                                              │
                                          queue.next()
                                              │
                                        nativePollOnce() ← epoll_wait 阻塞
                                              │
                                        nativeWake() ← 新消息唤醒
                                              │
                                          msg != null
                                              │
                                        msg.target.dispatchMessage(msg)
                                              │
                                          callback.run() 或 handleMessage()
                                              │
                                          msg.recycleUnchecked() → 归还 sPool
```

### 核心 API 及关键参数

| 类 / 方法 | 签名 | 说明 |
| --- | --- | --- |
| `Looper.prepare()` | `static void` | 当前线程创建 Looper,ThreadLocal 存;线程重复调用抛 RuntimeException |
| `Looper.prepareMainLooper()` | `static void`(API 30+ `@Deprecated`) | 由系统调用,应用层不要主动调用 |
| `Looper.loop()` | `static void` | 死循环 next+dispatch,直到 `queue.next()` 返回 null(即 `quit()` / `quitSafely()` 后) |
| `Looper.quit()` | `void` | 立即退出,丢弃队中所有消息,延迟消息也丢失 |
| `Looper.quitSafely()` | `void` | 处理完已 due 的消息后再退出,延迟消息被丢弃 |
| `Handler(Looper)` | `Handler(Looper looper)` | 显式绑定 Looper(API 30+ 推荐;无参构造已 @Deprecated) |
| `Handler.post(Runnable)` | `boolean` | 投递 Runnable;内部走 sendMessageDelayed(0) |
| `Handler.sendMessage(Message)` | `boolean` | 投递完整 Message;返回 false 表示 Looper 已 quit |
| `Handler.sendEmptyMessage(int)` | `boolean` | 只指定 what 的快捷方法 |
| `Handler.sendMessageDelayed(Message, long)` | `boolean` | 延迟投递,delay 毫秒 |
| `Message.obtain()` | `static Message` | 从 sPool 链表头取一个,池空则 new |
| `Message.obtain(Handler, int, int, int, Object)` | `static Message` | 工厂方法,设置 target / what / arg1 / arg2 / obj |

### 底层发生了什么

- **nativePollOnce** 走 Linux `epoll_wait`,超时参数是 `nextPollTimeoutMillis = (int) Math.min(msg.when - now, Integer.MAX_VALUE)`;无消息时传 `-1` 永久阻塞
- **同步屏障**:postSyncBarrier() 入队一个 target=null 的消息,next() 跳过所有 target!=null 的同步消息直到屏障被 remove;Choreographer(UI 绘制)用此机制保证 VSYNC 信号优先
- **内存复用**:Message.recycleUnchecked() 把字段清零并插回 sPool 头部,池满 50 条时直接丢弃(由 GC 回收)
- **线程模型**:Handler 始终在创建它的 Looper 线程上 dispatch;WorkerThread 通过 Handler(handlerThread.looper) 把任务塞进工作线程

## 对比 / 选型

| 维度 | Handler / Looper | Thread | Executor / ThreadPool | Kotlin Coroutines |
| --- | --- | --- | --- | --- |
| 并发模型 | 单线程串行消息循环 | 一线程一任务 | 多线程并行池 | 结构化并发(协程) |
| 串行保证 | ✅ 同 Looper 线程天然串行 | ❌ | ❌ 需单线程 Executor | ✅ Dispatchers.Main |
| 跨线程通信 | ✅ sendMessage / post | ❌ 需手写共享状态 | ❌ Future.get() / Callback | ✅ suspend / Flow |
| 延迟 / 定时消息 | ✅ postDelayed / sendMessageAtTime | ❌ 需 sleep | ❌ ScheduledExecutorService | ✅ delay() |
| 现代推荐度 | ⭐⭐(底层仍核心) | ⭐ | ⭐⭐⭐(CPU-bound) | ⭐⭐⭐⭐(I/O + UI) |

**选型建议**:UI 主线程相关 → Handler(系统用);后台串行任务 → HandlerThread;CPU-bound 池 → ExecutorService;I/O + 结构化并发 → Coroutines。

## 环境准备

- Android Studio Hedgehog(2023.1+)或更新
- minSdk 21,compileSdk 33,targetSdk 33
- Kotlin 1.9.0+(Kotlin 版需要) / Java 8+(Java 版需要)

## 运行方式

将 `kotlin/HandlerDemo.kt` 或 `java/HandlerDemoJava.java` 放入现有 Android 项目的 `src/main/java/com/example/...`,在 `MainActivity.onCreate()` 中按需调用 `HandlerDemo().demo1_postRunnable()` 等。在 logcat(过滤 `HandlerDemo`)查看输出。

```kotlin
// MainActivity.kt 示例
override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    val demo = HandlerDemo()
    demo.demo1_postRunnable()
    demo.demo2_sendMessage()
    demo.demo3_handlerThread()
    demo.demo4_postDelayed()
    demo.demo5_memoryLeak(this)
}
```

## 关键代码片段

### Handler 主线程投递(对应 demo1)

```kotlin
val mainHandler = Handler(Looper.getMainLooper())
Thread {
    // 子线程做耗时操作
    val result = doNetworkCall()
    // 投递回主线程更新 UI
    mainHandler.post {
        textView.text = result
    }
}.start()
```

### HandlerThread 工作线程(对应 demo3)

```kotlin
val handlerThread = HandlerThread("WorkerThread").apply { start() }
val workerHandler = Handler(handlerThread.looper)
workerHandler.post {
    // 在 WorkerThread 线程上串行执行;HandlerThread 退出前不会并发
    val bitmap = decodeImage(url)
    runOnUiThread { imageView.setImageBitmap(bitmap) }
}
handlerThread.quitSafely()  // 处理完队中已有消息后退出
```

### 内存泄漏修复(对应 demo5)

```kotlin
class SafeHandler(activity: Activity) : Handler() {
    private val ref = WeakReference(activity)
    override fun handleMessage(msg: Message) {
        val a = ref.get() ?: return  // Activity 销毁则丢弃
        // 更新 a 的 UI
    }
}
// Activity.onDestroy 中:
override fun onDestroy() {
    safeHandler.removeCallbacksAndMessages(null)  // 清空队列,立即打破引用
    super.onDestroy()
}
```

## 性能与边界

- **队列容量**:MessageQueue 单链表无界,但延迟消息堆积会占内存;postDelayed 时间不可超过 Integer.MAX_VALUE
- **主线程压力**:UI 操作必须在主线程,但 postDelayed 时间间隔毫秒级;消息处理累计时间决定 UI 帧率,16ms 内必须返回,否则丢帧
- **跨进程**:Handler 不直接支持跨进程;跨进程需用 `Messenger`(基于 Binder)或 AIDL
- **API 30+ 行为变更**:`Handler()` 无参构造被标 `@Deprecated`,必须显式 `Handler(Looper.getMainLooper())` 或 `Handler(Looper.myLooper())`;理由是隐式 Looper 选择导致"handler 创建线程 ≠ 实际关联线程"的隐性 bug
- **内存复用上限**:Message 对象池 MAX_POOL_SIZE = 50;超出 50 个未归还的 Message 会 new 而不是 reuse

## 注意事项与常见坑

1. **内存泄漏(经典)**:Activity 内 `private val handler = object : Handler() { ... }` 是匿名内部类,隐式持有 Activity;Activity 销毁时若队中有未处理消息 → Handler → Activity 链导致泄漏。修复:静态内部类 + WeakReference,或 `onDestroy` 中 `removeCallbacksAndMessages(null)`。
2. **主线程 ANR**:主线程 Handler 处理消息超过 5 秒(系统默认 `ActivityManager.ANR` 阈值)触发 ANR;耗时任务必须丢到工作线程。
3. **postDelayed 不准时**:实际延迟时间受主线程队列中已有消息堆积影响;嵌套 postDelayed 用 cancel/post 才能重新调度。
4. **同步屏障误用**:`postSyncBarrier()` 必须配套 `removeSyncBarrier(token)`;忘记移除会导致主线程所有同步消息卡死(常见于自定义 Looper 框架的 bug)。
5. **Handler(Looper.myLooper()) 在非 Looper 线程抛 RuntimeException**:"Can't create handler inside thread that has not called Looper.prepare()";子线程必须先 `Looper.prepare()` 才能创建 Handler。
6. **quit() vs quitSafely()**:quit 立刻退出队中所有消息,quitSafely 处理已 due 的再退出;UI 线程不要调用 quit,否则应用主循环终止。
7. **API 30+ 隐式 Looper 已弃用**:旧代码 `new Handler()` 编译警告;迁移时注意 Looper 实际绑定的线程。
8. **WorkerThread 与 Executor 选型**:HandlerThread 是"单线程串行 Executor";并发请用 ThreadPoolExecutor,不要用多个 HandlerThread 模拟并发。

## 参考资料(实际阅读过的权威来源)

- [Handler | Android Developers](https://developer.android.google.cn/reference/android/os/Handler) — Handler API 官方参考,包含 send/post 系列方法签名与 deprecated 警告
- [Looper | Android Developers](https://developer.android.google.cn/reference/android/os/Looper) — Looper API 官方参考,prepare/loop/quit/quitSafely 完整方法签名
- [MessageQueue | Android Developers](https://developer.android.google.cn/reference/android/os/MessageQueue) — MessageQueue API 与 nativePollOnce 文档
- [Processes and threads overview | Android Developers](https://developer.android.google.cn/guide/components/processes-and-threads) — 主线程规则与 ANR 阈值说明
- [HandlerThread | Android Developers](https://developer.android.google.cn/reference/android/os/HandlerThread) — HandlerThread API 参考
- [Android 源码:Looper.java / MessageQueue.java](https://cs.android.com/android/platform/superproject/main/+/main:frameworks/base/core/java/android/os/) — AOSP 源码,ThreadLocal 存储 + 死循环 + recycleUnchecked 实现细节