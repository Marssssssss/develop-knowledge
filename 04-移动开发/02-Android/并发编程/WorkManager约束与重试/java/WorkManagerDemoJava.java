package demo.work;

import android.content.Context;

import androidx.work.BackoffPolicy;
import androidx.work.Constraints;
import androidx.work.Data;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.WorkRequest;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import java.util.Arrays;
import java.util.concurrent.TimeUnit;

/**
 * WorkManager:约束 / 退避 / 依赖链 / 唯一工作(Java 用法,与 Kotlin 版对应)。
 *
 * 依赖:androidx.work:work-runtime。
 * 要点:约束是「与」关系;默认退避为 EXPONENTIAL + 30s,延迟 clamp 到 [10s, 5h];
 *      Worker.doWork() 返回 Result.failure() 时,依赖它的后续工作不会执行。
 */
public final class WorkManagerDemo {

    private WorkManagerDemo() {
    }

    /** 仅当「非计费网络 + 充电中 + 电量不低」同时成立才可运行。 */
    public static Constraints uploadConstraints() {
        return new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.UNMETERED)
                .setRequiresCharging(true)
                .setRequiresBatteryNotLow(true)
                .build();
    }

    public static WorkRequest oneTimeRequest(String payload) {
        return new OneTimeWorkRequest.Builder(UploadWorker.class)
                .setConstraints(uploadConstraints())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL,
                        WorkRequest.DEFAULT_BACKOFF_DELAY_MILLIS, TimeUnit.MILLISECONDS)
                .setInputData(new Data.Builder().putString(UploadWorker.KEY_PAYLOAD, payload).build())
                .addTag("upload")
                .build();
    }

    public static WorkRequest periodicRequest() {
        return new PeriodicWorkRequest.Builder(SyncWorker.class,
                PeriodicWorkRequest.MIN_PERIODIC_INTERVAL_MILLIS, TimeUnit.MILLISECONDS)
                .setConstraints(new Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build())
                .build();
    }

    /** 依赖链:A → B → C;B、C 在 A 成功前处于 BLOCKED。 */
    public static void enqueueChain(Context context) {
        OneTimeWorkRequest a = new OneTimeWorkRequest.Builder(UploadWorker.class).build();
        OneTimeWorkRequest b = new OneTimeWorkRequest.Builder(UploadWorker.class).build();
        OneTimeWorkRequest c = new OneTimeWorkRequest.Builder(UploadWorker.class).build();
        WorkManager.getInstance(context)
                .beginWith(a)
                .then(b)
                .then(c)
                .enqueue();
    }

    /** 唯一工作:KEEP 忽略新请求,REPLACE 取消旧任务。 */
    public static void enqueueUnique(Context context, String payload, ExistingWorkPolicy policy) {
        WorkManager.getInstance(context).enqueueUniqueWork(
                "daily-upload", policy, oneTimeRequest(payload));
    }

    public static void cancelAll(Context context) {
        WorkManager.getInstance(context).cancelAllWorkByTag("upload");
    }

    /** 同步型 Worker:doWork() 在后台线程执行,最多 10 分钟。 */
    public static final class UploadWorker extends Worker {
        public static final String KEY_PAYLOAD = "payload";
        private static final int MAX_ATTEMPTS = 5;

        public UploadWorker(Context context, WorkerParameters params) {
            super(context, params);
        }

        @Override
        public Result doWork() {
            String payload = getInputData().getString(KEY_PAYLOAD);
            if (payload == null || payload.isEmpty()) {
                // 永久性失败:依赖它的后续工作不会执行
                return Result.failure(new Data.Builder().putString("error", "empty payload").build());
            }
            if (getRunAttemptCount() < 2) {
                // 瞬时失败:交给 WorkManager 按退避策略重排
                return getRunAttemptCount() >= MAX_ATTEMPTS ? Result.failure() : Result.retry();
            }
            return Result.success(new Data.Builder().putInt("bytes", payload.length()).build());
        }
    }

    public static final class SyncWorker extends Worker {
        public SyncWorker(Context context, WorkerParameters params) {
            super(context, params);
        }

        @Override
        public Result doWork() {
            System.out.println("sync tick, attempt=" + getRunAttemptCount());
            return Result.success();
        }
    }

    public static void main(String[] args) {
        System.out.println("constraints = " + uploadConstraints());
        System.out.println("tags = " + Arrays.toString(
                oneTimeRequest("hello").getTags().toArray()));
    }
}
