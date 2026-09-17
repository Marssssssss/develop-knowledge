# WorkManager:约束 / 退避重试 / 依赖链 / 唯一工作

## 简介

WorkManager 是 Android 官方**可延迟后台工作**的调度库:把「这件事必须做成」交给系统,
在满足约束(联网、充电、电量、存储、空闲)时执行,进程被杀或设备重启后仍会被恢复。

关键概念:

| 概念 | 一句话解释 |
| --- | --- |
| `Constraints` | 运行前置条件,多个条件是**与**关系;默认全部为 false,即约束为空可直接运行 |
| `WorkRequest` | 一次工作的规格:唯一 `id`、`tags`、`backoffCriteria`、约束;分一次性与周期性两种 |
| `Result` | `doWork()` 的返回值:`success` / `failure` / `retry` |
| `ExistingWorkPolicy` | 同名唯一工作的冲突策略:`KEEP` / `REPLACE` / `APPEND` / `REPLACE_AND_APPEND` |
| `WorkContinuation` | 依赖链:`beginWith(A).then(B).then(C)` |

历史背景:WorkManager(2018)统一了此前的 `JobScheduler`(API 21+)、`Firebase JobDispatcher`、
`AlarmManager` 三套方案,并把「工作规格与执行结果」持久化到本地数据库,因此具备重启后恢复能力。

## 原理详解

### 1. 约束是「与」,默认全关

`Constraints` 的构造参数默认值(源码 javadoc):

```text
requiredNetworkType = NOT_REQUIRED     # 网络要求,取值 NOT_REQUIRED / CONNECTED / UNMETERED /
                                       #   NOT_ROAMING / METERED
requiresCharging      = false
requiresDeviceIdle    = false
requiresBatteryNotLow = false
requiresStorageNotLow = false
```

另有一组 API 24+ 的 `content:` Uri 触发器(`ContentUriTrigger` + 更新延迟/最大延迟),
语义与 `JobScheduler` 的 `setTriggerContentUpdateDelay` 一致。

未满足约束时,工作停在 `ENQUEUED` **不执行**(不是失败);约束恢复后自动继续。

### 2. 退避重试

- 默认 `BackoffPolicy.EXPONENTIAL` + `DEFAULT_BACKOFF_DELAY_MILLIS = 30_000`。
- `backoffDelay` 会被 clamp 到 `[MIN_BACKOFF_MILLIS = 10 s, MAX_BACKOFF_MILLIS = 5 h]`。
- `Result.retry()` 让 WorkManager 重新排队;`runAttemptCount` 可在 `doWork()` 里读到。

> 口径声明:官方 javadoc 只给出默认值与 clamp 区间,**没有**给出「第 n 次重试的倍数公式」。
> 本 demo 采用 `base × 2^(attempt-1)`(指数)/ `base × attempt`(线性)这一常见口径,
> 并用 clamp 区间锚定官方数值;与具体版本的实现细节可能有差异。

### 3. 执行窗口与依赖

- `Worker.doWork()` 同步执行,**每个 Worker 实例只调用一次**;超过 **10 分钟**会被通知停止。
- 返回 `Result.failure()` 时,**依赖它的后续工作不会执行**(源码 `Worker.kt` 的 javadoc 原文)。
- 依赖链的前置任务未完成时,后续任务处于 `BLOCKED`。

### 4. 唯一工作

`enqueueUniqueWork(name, policy, request)`:

| 策略 | 语义 |
| --- | --- |
| `KEEP` | 同名未结束的工作存在 → **忽略**新请求 |
| `REPLACE` | 取消同名旧工作,换成新请求 |
| `APPEND` | 追加到同名工作链尾部 |
| `REPLACE_AND_APPEND` | 取消旧链并把新请求追加到新链尾 |

## 对比 / 选型

| 方案 | 适合 | 不适合 |
| --- | --- | --- |
| WorkManager | 可延迟、必须完成的持久化工作 | 需要立刻执行且用户等待的交互式任务 |
| 前台 Service | 用户可感知的持续任务(播放、导航、上传进度) | 不需要通知的短任务 |
| `AlarmManager` | 精确定时(闹钟、日历提醒) | 可延迟的批处理 |
| 协程 `launch` | 与界面同生命周期的工作 | 需要在进程被杀后仍然完成的工作 |

## 环境准备

- 依赖:`androidx.work:work-runtime`(Kotlin 版可加 `work-runtime-ktx`)
- 自检模型:**Python 3.10+**,零第三方依赖

## 运行方式

### Python(模型 + 自检,可直接跑)

```bash
cd python && python3 workmanager_check.py     # 40 项断言
```

### Kotlin / Java(Android 工程内)

```bash
# Kotlin
kotlinc WorkManagerDemo.kt -cp work-runtime.jar -d demo.jar
# Java
javac -cp work-runtime.jar WorkManagerDemoJava.java
```

## 关键代码片段

```kotlin
// 约束:非计费网络 + 充电中 + 电量不低(全部满足才跑)
val constraints = Constraints.Builder()
    .setRequiredNetworkType(NetworkType.UNMETERED)
    .setRequiresCharging(true)
    .setRequiresBatteryNotLow(true)
    .build()

// 退避:默认 EXPONENTIAL + 30s,延迟会被 clamp 到 [10s, 5h]
val request = OneTimeWorkRequestBuilder<UploadWorker>()
    .setConstraints(constraints)
    .setBackoffCriteria(BackoffPolicy.EXPONENTIAL,
        WorkRequest.DEFAULT_BACKOFF_DELAY_MILLIS, TimeUnit.MILLISECONDS)
    .build()

// 三态返回:success 才放行下游,failure 让依赖工作不执行,retry 交给退避策略
override suspend fun doWork(): Result = try {
    upload(payload); Result.success()
} catch (e: TransientException) {
    if (runAttemptCount >= MAX_ATTEMPTS) Result.failure() else Result.retry()
} catch (e: IllegalStateException) {
    Result.failure()                       // 永久性错误,不再重试
}

// 依赖链:A → B → C
WorkManager.getInstance(ctx).beginWith(a).then(b).then(c).enqueue()
```

## 性能与边界

- 周期性工作的周期下限是 `PeriodicWorkRequest.MIN_PERIODIC_INTERVAL_MILLIS`(15 分钟);
  更短的「周期」只能靠一次性任务自链实现。
- 执行窗口 10 分钟是**单次执行**上限,不是总时长;长时间任务应拆成可重试的多个工作。
- 退避延迟封顶 5 小时:若 base 设成 1 小时,第 4 次重试就会撞上上限(1h → 2h → 4h → 5h)。
- 唯一工作是按**名字**约束的,定时任务(如「每天上传」)应使用唯一工作避免重复入队。

## 注意事项与常见坑

| 现象 | 原因 | 规避方法 |
| --- | --- | --- |
| 工作一直不跑 | 约束未满足(常见:设了 `UNMETERED` 但当前是计费网络) | 先用 `WorkInfo.state == ENQUEUED` + 约束逐项排查 |
| 任务莫名重复执行 | 每次构建页面都 `enqueue()` 一次 | 用 `enqueueUniqueWork` + `KEEP` |
| 依赖任务没跑 | 前置任务返回了 `Result.failure()` | 需要继续执行就返回 `retry` 或 `success` |
| 重试越来越慢甚至像「卡死」 | 指数退避撞上 5 小时上限 | 显式 `setBackoffCriteria` 控制 base,或改判为永久失败 |
| 把 `doWork()` 当长任务入口 | 超过 10 分钟会被通知停止 | 拆分为多个工作,或改用前台 Service |
| 在 `doWork()` 里直接更新 UI | 它跑在 WorkManager 的后台线程 | 通过数据库/Flow 暴露状态,由 UI 层观察 |

## 参考资料(实际阅读过的权威来源)

- [androidx `androidx.work.Constraints` 源码](https://github.com/androidx/androidx/blob/androidx-main/work/work-runtime/src/main/java/androidx/work/Constraints.kt)
  —— 全部约束字段、默认值、`content:` Uri 触发器与 `JobScheduler` 的对应关系
- [androidx `androidx.work.Worker` 源码](https://github.com/androidx/androidx/blob/androidx-main/work/work-runtime/src/main/java/androidx/work/Worker.kt)
  —— `doWork()` 每个实例只调一次、10 分钟执行窗口、`Result.failure()` 让依赖工作不执行
- [androidx `androidx.work.WorkRequest` 源码](https://github.com/androidx/androidx/blob/androidx-main/work/work-runtime/src/main/java/androidx/work/WorkRequest.kt)
  —— `id` / `tags` / `setBackoffCriteria` 的 javadoc、`DEFAULT_BACKOFF_DELAY_MILLIS = 30_000`、
  `MIN_BACKOFF_MILLIS = 10_000`、`MAX_BACKOFF_MILLIS = 5 h`
- 说明:`androidx.work.impl.model.WorkSpec`(退避时间的具体计算公式)在本轮抓取时网络不可达,
  因此公式部分已在 README 中显式标注为「demo 口径」而非官方口径
- 本目录 `python/workmanager_model.py` + `workmanager_check.py` 的实跑输出(40 项断言全绿)
