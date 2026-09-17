# Service 三种形态(started / bound / foreground)

## 简介

Android 的 `Service` 是"没有界面、可长时间运行"的组件,官方给了它三种**并存但不互斥**的形态:

- **started(已启动)**：由 `Context.startService()` 拉起,一直跑到 `stopService()` / `stopSelf()`
- **bound(已绑定)**：由 `Context.bindService()` 建立连接,客户端全部 `unbindService()` 后自动销毁
- **foreground(前台)**：在 started 的基础上调 `startForeground()`,让系统把进程优先级提到"用户可感知",低内存时优先不被杀

三者可以叠加:**服务只要是「已启动」或「还存在带 `BIND_AUTO_CREATE` 的连接」其一成立,就会一直活着**(AOSP `Service.java` 类级 javadoc 原文:*"the system will keep them running as long as either it is started or there are one or more connections to it with the Context.BIND_AUTO_CREATE flag"*)。

**关键概念清单**：

- **不是进程、不是线程**：`Service` 只是宿主进程里的一个对象,所有回调都跑在**主线程**上
- **多次 start 不嵌套**：调 N 次 `startService()` → 1 次 `onCreate()` + N 次 `onStartCommand()`,且 `startId` 递增
- **`stopSelf(startId)`**：只在 `startId` 等于"最近一次 start"时才真正停止,否则是 no-op;但**乱序**先调最近 ID 会立即停止
- **返回值决定重建策略**：`START_STICKY`(1) / `START_NOT_STICKY`(2) / `START_REDELIVER_INTENT`(3) / `START_STICKY_COMPATIBILITY`(0);`flags` 参数含 `START_FLAG_REDELIVERY`(0x0001) / `START_FLAG_RETRY`(0x0002)

**历史背景**：`setForeground(boolean)` 在 Android 2.0 之前有效,之后被 `startForeground(int, Notification)` 取代但保留为 **no-op**(官方 `@deprecated` 注释原文:*"This is a now a no-op, use startForeground(int, Notification) instead. This method has been turned into a no-op rather than simply being deprecated"*)。前台服务随后持续收紧:API 28 起需要 `FOREGROUND_SERVICE` 权限;API 29 起要在 manifest 声明 `foregroundServiceType`;API 31 起禁止后台启动前台服务(`ForegroundServiceStartNotAllowedException`);API 34 起 manifest 未声明 type 会抛 `MissingForegroundServiceTypeException`。

## 原理详解

### 启动 / 绑定 / 前台三条路径

```
startService(intent#1)       onCreate → onStartCommand(startId=1)
startService(intent#2)       （不重复 onCreate） → onStartCommand(startId=2)
                             ↑ 不嵌套：一个 onCreate,两个 onStartCommand

bindService(c, BIND_AUTO_CREATE)   onCreate(已存在则跳过) → onBind   ← 只对**第一个**客户端调用
bindService(c2, BIND_AUTO_CREATE)  （不重复 onBind）→ 直接复用同一 IBinder
unbindService(c)                   （还留着 c2,不触发 onUnbind）
unbindService(c2)                  onUnbind → onDestroy

startForeground(id, notif)   只是"提升进程优先级 + 挂通知",**不改变 started/bound 状态**
```

### 生命周期的停止条件(状态图)

```
  startService() ──► onCreate ──► onStartCommand     started = true
  bindService(BIND_AUTO_CREATE) ──► onBind(首个客户端) connections += 1
  stopService() / stopSelf(最近 startId) ──► started = false
  unbindService(最后一个客户端)           ──► connections = 0

  停止判定(与调用顺序无关):
    started == false  AND  connections == 0  ──►  onDestroy(只触发一次)
```

**核心不变量**:`onDestroy` 只在 `started == false` **且** `connections == 0` **且**尚未销毁时触发一次。所以"先 unbind 再 stop"与"先 stop 再 unbind"最终都只销毁一次,但中间的事件序列不同(前者最后一步是 `unbind`,`onUnbind` 在 `stopService` 之前)。

### `onStartCommand` 返回值语义

| 常量 | 值 | 进程被杀后 | 是否重投递 Intent | 是否可能收到 `null` intent |
| --- | --- | --- | --- | --- |
| `START_STICKY_COMPATIBILITY` | 0 | 重建(不保证 `onStartCommand` 被调) | ❌ | ❌ |
| `START_STICKY` | 1 | 重建,且保证调 `onStartCommand` | ❌ | ✅(无 pending start 时) |
| `START_NOT_STICKY` | 2 | **不重建**,等下次显式 `startService` | ❌ | ❌ |
| `START_REDELIVER_INTENT` | 3 | 重建并**重投递最后一条 Intent** | ✅ | ❌ |

`START_REDELIVER_INTENT` 的重投递会一直排队,**直到服务用 `onStartCommand` 拿到的那个 `startId` 调用 `stopSelf(int)`** 才解除 —— 这也是"任务型服务"必须正确回传 `startId` 的原因。

### `stopSelf(int startId)` 的两种用法

```java
// AOSP Service.java javadoc 原文要点:
//   "Stop the service if the most recent time it was started was startId."
//   "Be careful about ordering of your calls to this function. If you call this
//    function with the most-recently received ID before you have called it for
//    previously received IDs, the service will be immediately stopped anyway."
```

所以有两种写法,后果完全不同:

| 写法 | 行为 |
| --- | --- |
| 每次 `onStartCommand` 用**本次收到的** `startId` 调 `stopSelf` | 按序收敛:最早的 start 处理完才能停;乱序完成时不会提前杀死服务 |
| 乱序用**最近一次** `issuedStartId` 调 `stopSelf` | ⚠️ **立即停止**,后来的 `onStartCommand` 收不到 —— 官方明确警告的反例 |

`stopSelfResult(int)` 是同语义但**返回 boolean**,告诉你服务是否真的被停掉了。

### 核心 API

| 类 / 方法 | 签名 | 说明 |
| --- | --- | --- |
| `Service.onStartCommand` | `int (Intent, int flags, int startId)` | 返回值见上表;`flags` 含 `START_FLAG_*` |
| `Service.onBind` | `IBinder? (Intent)` | 只对第一个客户端调用;返回 `null` 表示不支持绑定 |
| `Service.onUnbind` | `boolean (Intent)` | 返回 `true` → 下次绑定走 `onRebind` |
| `Service.stopSelf(int)` / `stopSelfResult(int)` | `void` / `boolean` | 仅最近 `startId` 生效;后者带返回值 |
| `Service.startForeground(int, Notification[, int])` | `void` | `id` **不得为 0**;三参版 API 29+,`type` 必须是 manifest 声明 flag 的**子集** |
| `Service.stopForeground(boolean)` | `void` | 移除通知但服务继续跑 |

## 对比 / 选型

| 需求 | 形态 | 原因 |
| --- | --- | --- |
| 后台执行一次任务后结束 | `started` + `START_NOT_STICKY` | 不重建,避免无意义的空转 |
| 长驻后台(音乐、导航) | `started` + `START_STICKY` + **foreground** | 需要"被杀后重建"且不能被杀 |
| 与 Activity 交互 / 跨进程调用 | `bound`(同进程直接 cast;Binder 接口 + AIDL / `Messenger`) | 官方 Local / Remote Messenger Sample 两条路线 |
| 定时轮询 | `started` + `START_NOT_STICKY` + `AlarmManager` | 官方 javadoc 举的例子:闹钟到点再拉起来 |
| 保证 Intent 不丢 | `START_REDELIVER_INTENT` | 重建时重投递未完成的那条 Intent |
| 现代推荐的"可延迟后台任务" | `WorkManager`(见同域 demo) | 系统统一调度,带约束与退避 |

## 环境准备

- Android Studio Hedgehog(2023.1+)、minSdk 21、compileSdk 34;Python 3.8+(跑自检)
- 前台服务需在 manifest 声明 `FOREGROUND_SERVICE` 权限(API 28+)与 `android:foregroundServiceType`(API 29+)

## 运行方式

```bash
# 1) 跑 Python 自检(35 条断言,覆盖 startId 语义 / 重启计划 / 前台权限 / 停止不变量)
python "04-移动开发/02-Android/组件生命周期/Service三种形态/python/service_state_check.py"

# 2) Kotlin / Java 用法:把源码放进 Android 工程,在 Activity 里调用
#    PlaybackService.start(context, ACTION_PLAY)  → logcat 过滤 PlaybackService
```

```xml
<!-- AndroidManifest.xml 关键片段 -->
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />

<application ...>
    <service
        android:name=".service.PlaybackService"
        android:exported="false"
        android:foregroundServiceType="mediaPlayback" />
</application>
```

## 关键代码片段

### 已启动形态 + 正确的 `stopSelf(startId)`

```kotlin
override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    when (intent?.action) {
        ACTION_STOP -> {
            stopPlayback()
            stopSelf(startId)          // 传**本次**收到的 startId → 立即停止
        }
        ACTION_STOP_IF_DONE -> stopSelf(startId)  // 若还有更新的 start 请求则不停止
    }
    return START_STICKY                // 被杀后重建;无待投递 intent 时收到 null intent
}
```

### 绑定形态(同进程直接 cast)

```kotlin
inner class LocalBinder : Binder() {
    fun service(): PlaybackService = this@PlaybackService
}
override fun onBind(intent: Intent?): IBinder = binder

override fun onUnbind(intent: Intent?): Boolean {
    return true                        // true → 之后重新绑定会触发 onRebind
}
```

### 前台形态(注意权限与 type)

```kotlin
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
    startForeground(
        notificationId, notification,
        ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK   // 必须是 manifest 声明 flag 的子集
    )
} else {
    startForeground(notificationId, notification)            // API 28+ 需 FOREGROUND_SERVICE 权限
}
```

## 性能与边界

- **回调都在主线程**:`onCreate` / `onStartCommand` / `onDestroy` 都在主线程跑,官方明确要求"耗时工作自己开线程,避免 ANR"。`onBind` 的调用在主线程,但**返回的 IBinder 上的远端调用不一定在主线程**(javadoc 原文:*"...may not happen on the main thread of the process"*)。
- **进程优先级四档**(javadoc `Process Lifecycle` 节):① 正在跑 `onCreate`/`onStartCommand`/`onDestroy` → **前台进程**优先级;② 已启动但未前台 → 低于"用户可见进程"、高于"不可见进程",长期运行的 started 服务**最终一定会被杀**(并在合适时重建);③ 存在 bound 客户端 → 进程重要性**不低于最重要的那个客户端**;④ 前台服务 → 低内存时**不是候选**(极端内存压力下仍理论上可能被杀)。
- **通知 id 不得为 0**;`startForeground(id, notif, type)` 的 `type` 必须是 manifest `foregroundServiceType` 声明 flag 的**子集**,否则 `IllegalArgumentException`(用 `FOREGROUND_SERVICE_TYPE_MANIFEST` 表示"全用 manifest 声明的")。API 34 起还有三类异常:manifest 未声明 type → `MissingForegroundServiceTypeException`;声明为 `FOREGROUND_SERVICE_TYPE_NONE` → `InvalidForegroundServiceTypeException`;无对应权限 → `SecurityException`。
- **URI 授权生命周期**:`startService` 时带上 `FLAG_GRANT_READ_URI_PERMISSION` / `FLAG_GRANT_WRITE_URI_PERMISSION`,授权**保持到服务为该 start 命令(或更晚的一条)调用 `stopSelf(int)`,或服务被完全停止**为止 —— 这是必须正确回传 startId 的第二个理由。
- **API 31+ 后台启动限制**:targeting S+ 的应用**不能从后台启动前台服务**(抛 `ForegroundServiceStartNotAllowedException`);但该限制**不影响 sticky 前台服务的重启**路径。

## 注意事项与常见坑

1. **`startForeground` 不会让服务变为"已启动"**:名字有误导性;它只提升优先级 + 挂通知,要先 `startService()` 才有 started 语义。同理 `setForeground(boolean)` 已是空函数,当前 AOSP 实现只打一行 `Log.w(TAG, "setForeground: ignoring old API call on ...")`,看到老代码调它应当作"没写"。
2. **乱序调 `stopSelf` 会提前杀服务**:如果 `onStartCommand` 分发到多个线程处理,而后台线程用"手上最新的那个 startId"去停,服务会立即停止,尚未处理的 intent 全部丢失。要么按序停,要么用 `stopSelfResult` 判断。
3. **`START_STICKY` 的 `onStartCommand` 可能收到 `null` intent**:javadoc 明确要求"必须自己判空" —— 这是 `intent?.action` 里那个问号存在的官方理由。反之 `START_REDELIVER_INTENT` 会**一直重投递**直到用对应 `startId` 调 `stopSelf(int)`,忘了停 = 每次重启都重放同一条 Intent。
4. **`onBind` 只对第一个客户端调用**:第 2 个客户端直接拿到同一 `IBinder`。想在"新客户端接入"时做点什么,得自己在 Binder 层记录。
5. **`unbindService` 不会触发 `onServiceDisconnected`**:只有**服务进程被杀**才会;混用这两个回调做资源清理会漏。另:`bindService` 不带 `BIND_AUTO_CREATE` 时,服务不存在只回调连接失败,不会创建实例。
6. **前台服务通知被用户划掉**:理论上系统会移除前台状态(不同版本行为有差异);不要让业务正确性依赖"通知一定在"。
7. **别用 started Service 做定时任务**:被 killed 的时间不确定;定时/约束型任务应交给 `WorkManager` 或 `AlarmManager`(官方 javadoc 自己举的就是 `AlarmManager` 的例子)。

## 参考资料(实际阅读过的权威来源)

- [Service.java — AOSP frameworks/base (GitHub 镜像)](https://github.com/aosp-mirror/platform_frameworks_base/blob/master/core/java/android/app/Service.java) — 类级 javadoc(三种形态、`Process Lifecycle` 四档优先级、主线程要求)、`START_*` 与 `START_FLAG_*` 常量定义与完整语义、`stopSelf(int)` 的排序警告原文、`setForeground(boolean)` 的 no-op 注释与实现、`startForeground(int, Notification[, int])` 的 type 子集规则与 API 34 三类异常、`FLAG_GRANT_*_URI_PERMISSION` 授权生命周期
- 本轮同时抓取了 [androidx Constraints.kt / Worker.kt / WorkRequest.kt](https://github.com/androidx/androidx/tree/androidx-main/work/work-runtime/src/main/java/androidx/work) 与 [Fragment.java](https://github.com/androidx/androidx/blob/androidx-main/fragment/fragment/src/main/java/androidx/fragment/app/Fragment.java),用于同批次其它 demo 的对照

> 口径说明:`android.googlesource.com` / `cs.android.com` / `developer.android.com` 在本轮抓取时均不可达(连接超时),上表源码通过 GitHub 的 AOSP / AndroidX 镜像取得;README 中所有引号内文字均来自该镜像中的 javadoc 原文。`python/service_state_check.py` 复刻的是**状态不变量与常量语义**,不是 Android framework 内部的 `ActiveServices` 调度实现。
