package demo.work

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkRequest
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * WorkManager:约束 / 退避重试 / 依赖链 / 唯一工作(Kotlin 用法)。
 *
 * 依赖:androidx.work:work-runtime-ktx。
 *
 * 官方口径(androidx 源码 javadoc):
 *  - Constraints 默认 requiredNetworkType = NOT_REQUIRED、四个开关全 false,多个条件是「与」
 *  - WorkRequest 默认退避 EXPONENTIAL + 30_000 ms,backoffDelay 会被 clamp 到
 *    [MIN_BACKOFF_MILLIS=10s, MAX_BACKOFF_MILLIS=5h]
 *  - Worker.doWork() 每个实例只调用一次,执行窗口上限 10 分钟;
 *    返回 Result.failure() 时依赖它的后续工作不会执行
 */
object WorkManagerDemo {

    /** 只在上面的约束全部满足时运行:非计费网络 + 充电中 + 电量不低。 */
    val uploadConstraints: Constraints = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.UNMETERED)
        .setRequiresCharging(true)
        .setRequiresBatteryNotLow(true)
        .build()

    /** 一次性任务:退避策略与延迟可显式指定。 */
    fun oneTimeRequest(): WorkRequest =
        OneTimeWorkRequestBuilder<UploadWorker>()
            .setConstraints(uploadConstraints)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, WorkRequest.DEFAULT_BACKOFF_DELAY_MILLIS,
                TimeUnit.MILLISECONDS)
            .addTag("upload")
            .build()

    /** 周期任务:最小周期由 WorkRequest.MIN_PERIODIC_INTERVAL_MILLIS 限制(15 分钟)。 */
    fun periodicRequest(): WorkRequest =
        PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()

    /** 依赖链:A → B → C,B、C 在 A 成功前处于 BLOCKED。 */
    fun chained(context: Context) {
        val a = OneTimeWorkRequestBuilder<UploadWorker>().addTag("A").build()
        val b = OneTimeWorkRequestBuilder<UploadWorker>().addTag("B").build()
        val c = OneTimeWorkRequestBuilder<UploadWorker>().addTag("C").build()
        WorkManager.getInstance(context)
            .beginWith(a)
            .then(b)
            .then(c)
            .enqueue()
        // A 返回 Result.failure() 时,B、C 不会被调度;A 返回 Result.retry() 时按退避重排。
    }

    /** 唯一工作:同名任务用 KEEP 忽略新请求,用 REPLACE 取消旧任务并换新。 */
    fun uniqueUpload(context: Context, payload: Data) {
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(payload)
            .setConstraints(uploadConstraints)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "daily-upload", ExistingWorkPolicy.KEEP, request
        )
    }

    /** 周期唯一工作(例:每小时同步一次),KEEP 表示已有同名周期任务时不重复创建。 */
    fun uniquePeriodicSync(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(1, TimeUnit.HOURS).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            "hourly-sync", ExistingPeriodicWorkPolicy.KEEP, request
        )
    }
}

/** 同步型 Worker:doWork() 在 WorkManager 提供的后台线程上同步执行。 */
class UploadWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val attempt = runAttemptCount
        val payload = inputData.getString(KEY_PAYLOAD) ?: return Result.failure()
        return try {
            upload(payload)
            Result.success(Data.Builder().putInt("bytes", payload.length).build())
        } catch (e: TransientException) {
            // 约束丢失或网络抖动 → 交给 WorkManager 按退避策略重排(指数退避、clamp 到 5 小时)
            if (attempt >= MAX_ATTEMPTS) Result.failure() else Result.retry()
        } catch (e: IllegalStateException) {
            // 永久性错误:不要再重试,依赖它的后续工作也不会执行
            Result.failure(Data.Builder().putString("error", e.message ?: "unknown").build())
        }
    }

    private fun upload(payload: String) {
        if (payload.isEmpty()) throw IllegalStateException("empty payload")
        if (runAttemptCount < 2) throw TransientException("flaky network")
        println("uploaded ${payload.length} chars")
    }

    companion object {
        const val KEY_PAYLOAD = "payload"
        const val MAX_ATTEMPTS = 5
    }
}

class SyncWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        println("sync tick, attempt=$runAttemptCount")
        return Result.success()
    }
}

class TransientException(message: String) : Exception(message)
