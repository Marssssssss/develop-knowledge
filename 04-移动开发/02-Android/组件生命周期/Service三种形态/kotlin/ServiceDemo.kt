package demo.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder

/**
 * Android Service 的三种形态:started / bound / foreground(Kotlin 用法)。
 *
 * 依赖:Android framework 即可,无需第三方库。
 *
 * 官方口径(AOSP Service.java javadoc):
 *  - Service 不是进程、也不是线程,回调跑在宿主进程**主线程**上,耗时工作要自己开线程
 *  - 多次 startService 不嵌套,但会产生多次 onStartCommand;stopService/stopSelf 一次即停
 *  - stopSelf(startId) 仅在 startId == 最近一次 start 请求时才真正停止,否则是 no-op
 *  - 只要「已启动」或「存在 BIND_AUTO_CREATE 连接」其一成立,服务就活着
 *  - startForeground() 本身不会让服务变为已启动状态,必须先 startService
 */
class PlaybackService : Service() {

    private val binder = LocalBinder()
    private var startedCount = 0
    private var isPlaying = false

    inner class LocalBinder : Binder() {
        fun service(): PlaybackService = this@PlaybackService
    }

    override fun onCreate() {
        super.onCreate()
        log("onCreate")
        createChannel()
    }

    /** 已启动形态:每次 startService 都会进来一次,startId 递增。 */
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startedCount++
        log("onStartCommand startId=$startId flags=$flags intent=${intent?.action}")

        when (intent?.action) {
            ACTION_PLAY -> startPlayback()
            ACTION_STOP -> {
                stopPlayback()
                stopSelf(startId)                        // 传最近一次 startId → 立即停止
            }
            ACTION_STOP_IF_DONE -> stopSelf(startId)     // 若还有更新的 start 请求则不停止
        }
        return START_STICKY                               // 被杀后重建;无待投递 intent 时收到 null intent
    }

    /** 绑定形态:bindService 时调用一次,不会触发 onStartCommand。 */
    override fun onBind(intent: Intent?): IBinder {
        log("onBind action=${intent?.action}")
        return binder
    }

    override fun onUnbind(intent: Intent?): Boolean {
        log("onUnbind")
        return true                                       // true → 之后重新绑定会触发 onRebind
    }

    override fun onRebind(intent: Intent?) {
        log("onRebind")
    }

    /** 前台形态:必须先 startService,这一步只是「让系统更不容易杀」。 */
    fun goForeground(notificationId: Int): Notification {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                notificationId, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK   // manifest 需声明同名 type
            )
        } else {
            startForeground(notificationId, notification)           // API 28+ 需 FOREGROUND_SERVICE 权限
        }
        return notification
    }

    override fun onDestroy() {
        stopPlayback()
        log("onDestroy: startedCount=$startedCount")
        super.onDestroy()
    }

    private fun startPlayback() {
        isPlaying = true
        log("playing")
    }

    private fun stopPlayback() {
        isPlaying = false
        log("stopped")
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "playback", NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    private fun buildNotification(): Notification {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder.setContentTitle("正在播放").setSmallIcon(android.R.drawable.ic_media_play).build()
    }

    private fun log(what: String) = println("PlaybackService.$what (thread=${Thread.currentThread().name})")

    companion object {
        const val ACTION_PLAY = "demo.service.PLAY"
        const val ACTION_STOP = "demo.service.STOP"
        const val ACTION_STOP_IF_DONE = "demo.service.STOP_IF_DONE"
        private const val CHANNEL_ID = "playback"

        fun start(context: Context, action: String) {
            context.startService(Intent(context, PlaybackService::class.java).setAction(action))
        }

        fun bind(context: Context): Boolean =
            context.bindService(
                Intent(context, PlaybackService::class.java),
                ConnectionHolder.connection, Context.BIND_AUTO_CREATE
            )
    }
}

private object ConnectionHolder {
    val connection = object : android.content.ServiceConnection {
        override fun onServiceConnected(name: android.content.ComponentName?, service: IBinder?) {
            println("client connected")
        }

        override fun onServiceDisconnected(name: android.content.ComponentName?) {
            println("client disconnected (进程被杀才会触发,unbind 不会)")
        }
    }
}

/**
 * 使用示意(需在 Android 环境运行):
 *   PlaybackService.start(context, PlaybackService.ACTION_PLAY)   // started
 *   PlaybackService.bind(context)                                 // bound
 *   context.unbindService(ConnectionHolder.connection)            // 断开最后一个连接 → onUnbind/onDestroy
 */
fun main() {
    println("本文件为 Android framework demo,需在 Android 环境(如 instrumentation test)中运行")
}
