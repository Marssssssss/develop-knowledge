package com.example.handlermechanism

import android.app.Activity
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.Message
import android.os.SystemClock
import java.lang.ref.WeakReference

/**
 * Handler / Looper / MessageQueue 消息机制 —— 5 个最小 demo
 *
 * 演示 Android 异步消息机制的核心 API 与典型使用模式。
 * 本文件可直接放入 Android 项目 src/main/java/ 编译运行(需要 compileSdk 33+)。
 *
 * 运行入口:在 Activity.onCreate() 中按需调用各 demo() 方法,观察 logcat 输出。
 */
class HandlerDemo {

    // 共享常量,模拟"消息类型"枚举
    companion object {
        private const val WHAT_PROGRESS = 1
        private const val WHAT_DOWNLOAD = 2
        private const val WHAT_FINISHED = 3
        private const val TAG = "HandlerDemo"
    }

    /**
     * Demo 1 — post Runnable 跨线程投递(子线程 → 主线程)
     *
     * 核心机制:
     *   1. 主线程默认有 Looper(由 ActivityThread 在启动时 prepareMainLooper())。
     *   2. Handler(Looper.getMainLooper()) 创建的 Handler 绑定到主线程。
     *   3. post(Runnable) 把 Runnable 包装成 Message(内部走 sendMessageDelayed(0)),
     *      入队主线程 MessageQueue。
     *   4. 主线程 Looper.loop() 在下一个循环取出消息,dispatch 到 handler 关联的 callback。
     *   5. Android 规则:UI 只能在主线程修改,故此模式是子线程更新 UI 的官方推荐路径。
     */
    fun demo1_postRunnable() {
        val mainHandler = Handler(Looper.getMainLooper())
        Thread {
            val workerName = Thread.currentThread().name
            println("[$TAG:demo1] 子线程 $workerName 投递 Runnable 到主线程")
            mainHandler.post {
                println("[$TAG:demo1] Runnable 在 ${Thread.currentThread().name} 上执行")
            }
        }.start()
    }

    /**
     * Demo 2 — sendMessage(what, arg1, arg2, obj) 四元组消息
     *
     * 核心机制:
     *   1. obtainMessage() 从静态 sPool 链表复用 Message(MAX_POOL_SIZE=50)。
     *   2. handleMessage() 在 Handler 所在线程的 Looper.loop() 调度时被调用。
     *   3. what 区分消息类型(int),arg1/arg2 是轻量整数,obj 是任意对象(跨进程需 Parcelable)。
     *   4. dispatchMessage() 优先执行 msg.callback(Runnable),然后 Handler.callback,
     *      最后才到 handleMessage(Message) 子类实现。
     */
    private val mMessageHandler = object : Handler(Looper.getMainLooper()) {
        override fun handleMessage(msg: Message) {
            val thread = Thread.currentThread().name
            when (msg.what) {
                WHAT_PROGRESS -> println("[$TAG:demo2] $thread progress=${msg.arg1}/${msg.arg2} obj=${msg.obj}")
                WHAT_DOWNLOAD -> println("[$TAG:demo2] $thread download=${msg.arg1}% file=${msg.obj}")
                WHAT_FINISHED -> println("[$TAG:demo2] $thread finished obj=${msg.obj}")
                else -> println("[$TAG:demo2] $thread unknown what=${msg.what}")
            }
        }
    }

    fun demo2_sendMessage() {
        mMessageHandler.sendMessage(mMessageHandler.obtainMessage(WHAT_PROGRESS, 50, 100, "step1"))
        mMessageHandler.sendMessage(mMessageHandler.obtainMessage(WHAT_DOWNLOAD, 75, 0, "data.bin"))
        mMessageHandler.sendMessage(mMessageHandler.obtainMessage(WHAT_FINISHED, 0, 0, "ok"))
    }

    /**
     * Demo 3 — HandlerThread 自带 Looper 的工作线程
     *
     * 核心机制:
     *   1. HandlerThread 继承自 Thread,run() 中先 Looper.prepare() 再 Looper.loop(),
     *      故该线程自带 Looper 和 MessageQueue。
     *   2. Handler(handlerThread.looper) 创建的 Handler 把消息发到该工作线程。
     *   3. quitSafely() 等到队中已 due 的消息全部处理完才退出;quit() 立刻退出。
     *   4. 适用于"串行执行后台任务"的场景(IntentService 的核心模式)。
     */
    fun demo3_handlerThread() {
        val ht = HandlerThread("WorkerLooper").apply { start() }
        val workerHandler = Handler(ht.looper)
        workerHandler.post {
            println("[$TAG:demo3] HandlerThread 上的任务 #1 执行于 ${Thread.currentThread().name}")
        }
        workerHandler.post {
            println("[$TAG:demo3] HandlerThread 上的任务 #2 执行于 ${Thread.currentThread().name}")
        }
        // quitSafely: 处理完已有消息(when<=now)后退出;延迟消息会被丢弃
        ht.quitSafely()
    }

    /**
     * Demo 4 — postDelayed / sendMessageDelayed 延迟消息
     *
     * 核心机制:
     *   1. delay 转 when(SystemClock.uptimeMillis() + delay) 写入 Message。
     *   2. MessageQueue.enqueueMessage 按 when 升序插入单链表,链表头是 mMessages。
     *   3. next() 用 when - now 计算 nextPollTimeoutMillis,nativePollOnce 用 epoll 阻塞。
     *   4. 到期才被取出;next() 返回 null 表示 quit。
     */
    fun demo4_postDelayed() {
        val h = Handler(Looper.getMainLooper())
        val start = SystemClock.uptimeMillis()
        h.postDelayed({
            val elapsed = SystemClock.uptimeMillis() - start
            println("[$TAG:demo4] 500ms 延迟消息到达,实测 elapsed=${elapsed}ms")
        }, 500)
    }

    /**
     * Demo 5 — 内存泄漏:非静态内部 Handler 隐式持有外部 Activity
     *
     * 核心机制:
     *   1. Kotlin/Java 非静态内部类(含匿名内部类)隐式持有外部类引用(Outer.this)。
     *   2. Handler 通过 MessageQueue 持有待发消息,Message.target = handler。
     *   3. Activity 被销毁时若消息队列仍有未处理的消息 → Handler → Activity 引用链 → 泄漏。
     *   4. 修复:静态内部类 + WeakReference<Activity>;或 onDestroy 时 removeCallbacksAndMessages(null)。
     *
     * Android Studio 在 API 30+ 已把无参 Handler() 构造函数标记 @Deprecated,
     * 强制用 Handler(Looper) 显式指定 Looper,避免 Looper 隐式选择歧义。
     */
    fun demo5_memoryLeak(activity: Activity) {
        // 错误示例(注释掉,避免编译器警告):
        // class LeakyActivity : Activity() {
        //     private val handler = object : Handler() {  // 编译警告:This Handler class should be static
        //         override fun handleMessage(msg: Message) { /* update UI */ }
        //     }
        // }

        // 正确示例:静态内部类 + WeakReference
        class SafeHandler private constructor(private val ref: WeakReference<Activity>) : Handler() {
            companion object {
                fun forActivity(a: Activity) = SafeHandler(WeakReference(a))
            }
            override fun handleMessage(msg: Message) {
                val a = ref.get() ?: return  // Activity 已销毁则丢弃
                println("[$TAG:demo5] SafeHandler 收到消息,Activity=${a.javaClass.simpleName}")
            }
        }
        val safe = SafeHandler.forActivity(activity)
        safe.sendEmptyMessage(0)
        // activity.onDestroy 中应调用 safe.removeCallbacksAndMessages(null)
    }
}