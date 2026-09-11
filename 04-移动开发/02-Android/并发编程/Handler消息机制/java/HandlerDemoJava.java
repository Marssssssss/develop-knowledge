package com.example.handlermechanism;

import android.app.Activity;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.Message;
import android.os.SystemClock;

import java.lang.ref.WeakReference;

/**
 * Handler / Looper / MessageQueue 消息机制 —— 5 个最小 demo (Java 版)
 *
 * 与 Kotlin 版逻辑一一对应;无 lambda / 顶层函数,Java 8+ 才支持 lambda,这里用匿名内部类保证可读性。
 * 运行入口:在 Activity.onCreate() 中调用各 demo(N) 方法,观察 logcat 输出。
 */
public class HandlerDemoJava {

    private static final String TAG = "HandlerDemo";

    public static final int WHAT_PROGRESS = 1;
    public static final int WHAT_DOWNLOAD = 2;
    public static final int WHAT_FINISHED = 3;

    /**
     * Demo 1 — post Runnable 跨线程投递(子线程 → 主线程)
     */
    public static void demo1_postRunnable() {
        final Handler mainHandler = new Handler(Looper.getMainLooper());
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String worker = Thread.currentThread().getName();
                System.out.println("[" + TAG + ":demo1] 子线程 " + worker + " 投递 Runnable 到主线程");
                mainHandler.post(new Runnable() {
                    @Override
                    public void run() {
                        System.out.println("[" + TAG + ":demo1] Runnable 在 " + Thread.currentThread().getName() + " 上执行");
                    }
                });
            }
        }).start();
    }

    /**
     * Demo 2 — sendMessage 四元组(what / arg1 / arg2 / obj)
     */
    private static Handler mMessageHandler = new Handler(Looper.getMainLooper()) {
        @Override
        public void handleMessage(Message msg) {
            String thread = Thread.currentThread().getName();
            switch (msg.what) {
                case WHAT_PROGRESS:
                    System.out.println("[" + TAG + ":demo2] " + thread + " progress=" + msg.arg1 + "/" + msg.arg2 + " obj=" + msg.obj);
                    break;
                case WHAT_DOWNLOAD:
                    System.out.println("[" + TAG + ":demo2] " + thread + " download=" + msg.arg1 + "% file=" + msg.obj);
                    break;
                case WHAT_FINISHED:
                    System.out.println("[" + TAG + ":demo2] " + thread + " finished obj=" + msg.obj);
                    break;
                default:
                    System.out.println("[" + TAG + ":demo2] " + thread + " unknown what=" + msg.what);
            }
        }
    };

    public static void demo2_sendMessage() {
        mMessageHandler.sendMessage(mMessageHandler.obtainMessage(WHAT_PROGRESS, 50, 100, "step1"));
        mMessageHandler.sendMessage(mMessageHandler.obtainMessage(WHAT_DOWNLOAD, 75, 0, "data.bin"));
        mMessageHandler.sendMessage(mMessageHandler.obtainMessage(WHAT_FINISHED, 0, 0, "ok"));
    }

    /**
     * Demo 3 — HandlerThread 自带 Looper 的工作线程
     */
    public static void demo3_handlerThread() {
        HandlerThread ht = new HandlerThread("WorkerLooper");
        ht.start();
        Handler workerHandler = new Handler(ht.getLooper());
        workerHandler.post(new Runnable() {
            @Override
            public void run() {
                System.out.println("[" + TAG + ":demo3] HandlerThread 上的任务 #1 执行于 " + Thread.currentThread().getName());
            }
        });
        workerHandler.post(new Runnable() {
            @Override
            public void run() {
                System.out.println("[" + TAG + ":demo3] HandlerThread 上的任务 #2 执行于 " + Thread.currentThread().getName());
            }
        });
        // quitSafely: 处理完已 due 的消息后退出;延迟消息会被丢弃
        ht.quitSafely();
    }

    /**
     * Demo 4 — postDelayed 延迟消息
     */
    public static void demo4_postDelayed() {
        final Handler h = new Handler(Looper.getMainLooper());
        final long start = SystemClock.uptimeMillis();
        h.postDelayed(new Runnable() {
            @Override
            public void run() {
                long elapsed = SystemClock.uptimeMillis() - start;
                System.out.println("[" + TAG + ":demo4] 500ms 延迟消息到达,实测 elapsed=" + elapsed + "ms");
            }
        }, 500L);
    }

    /**
     * Demo 5 — 内存泄漏:非静态内部 Handler 持有外部 Activity
     */
    public static void demo5_memoryLeak(final Activity activity) {
        // 错误示例:匿名内部类隐式持有外部 Activity 引用,易导致内存泄漏
        // new Handler() {
        //     @Override public void handleMessage(Message msg) { /* 更新 activity.UI */ }
        // };

        // 正确示例:静态内部类 + WeakReference<Activity>
        class SafeHandler extends Handler {
            private final WeakReference<Activity> ref;
            SafeHandler(Activity a) { this.ref = new WeakReference<>(a); }
            @Override
            public void handleMessage(Message msg) {
                Activity a = ref.get();
                if (a == null) return;  // Activity 已销毁则丢弃
                System.out.println("[" + TAG + ":demo5] SafeHandler 收到消息,Activity=" + a.getClass().getSimpleName());
            }
        }
        SafeHandler safe = new SafeHandler(activity);
        safe.sendEmptyMessage(0);
        // activity.onDestroy 中应调用 safe.removeCallbacksAndMessages(null)
    }
}