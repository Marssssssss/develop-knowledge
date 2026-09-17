package demo.service;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.ServiceInfo;
import android.os.Binder;
import android.os.Build;
import android.os.IBinder;

/**
 * Android Service 三种形态(Java 用法,与 Kotlin 版对应)。
 *
 * 依赖:Android framework。
 *
 * 官方口径(AOSP Service.java):
 *   - Service 不是进程也不是线程,回调都在宿主进程主线程
 *   - started:startService → onCreate + onStartCommand;多次 startService 不嵌套
 *   - bound:bindService → onCreate,**不调用 onStartCommand**;连接存在期间服务就活着
 *   - 两者兼具:任一条件成立即存活,都失效才 onDestroy
 *   - onStartCommand 返回 START_STICKY(1) / START_NOT_STICKY(2) / START_REDELIVER_INTENT(3)
 */
public class PlaybackServiceJava extends Service {

    public static final String ACTION_PLAY = "demo.service.PLAY";
    public static final String ACTION_STOP = "demo.service.STOP";
    private static final String CHANNEL_ID = "playback";

    /** START_* 常量在 Service 中定义,取值与 AOSP 源码一致。 */
    public static final int STICKY_COMPAT = Service.START_STICKY_COMPATIBILITY;   // 0
    public static final int STICKY = Service.START_STICKY;                        // 1
    public static final int NOT_STICKY = Service.START_NOT_STICKY;                // 2
    public static final int REDELIVER = Service.START_REDELIVER_INTENT;           // 3

    private final LocalBinder binder = new LocalBinder();
    private int startedCount = 0;

    public class LocalBinder extends Binder {
        public PlaybackServiceJava service() {
            return PlaybackServiceJava.this;
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        log("onCreate");
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startedCount++;
        String action = intent == null ? null : intent.getAction();
        log("onStartCommand startId=" + startId + " action=" + action);

        if (ACTION_STOP.equals(action)) {
            // startId 恰为最近一次 start 请求 → 立即停止;否则本次调用是 no-op
            stopSelf(startId);
        }
        return STICKY;                       // 被杀后重建;无待投递 intent 时会收到 null intent
    }

    @Override
    public IBinder onBind(Intent intent) {
        log("onBind");
        return binder;
    }

    @Override
    public boolean onUnbind(Intent intent) {
        log("onUnbind");
        return true;                          // 之后重新绑定会回调 onRebind
    }

    @Override
    public void onRebind(Intent intent) {
        log("onRebind");
    }

    /** 前台:先 startService,再 startForeground;API 28+ 需要 FOREGROUND_SERVICE 权限。 */
    public void goForeground(int notificationId) {
        Notification notification = buildNotification();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(notificationId, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(notificationId, notification);
        }
    }

    @Override
    public void onDestroy() {
        log("onDestroy startedCount=" + startedCount);
        super.onDestroy();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID, "playback", NotificationManager.IMPORTANCE_LOW));
        }
    }

    private Notification buildNotification() {
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder.setContentTitle("正在播放")
                .setSmallIcon(android.R.drawable.ic_media_play)
                .build();
    }

    private static void log(String what) {
        System.out.println("PlaybackServiceJava." + what
                + " (thread=" + Thread.currentThread().getName() + ")");
    }

    public static void start(Context context, String action) {
        context.startService(new Intent(context, PlaybackServiceJava.class).setAction(action));
    }

    public static final ServiceConnection CONNECTION = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            System.out.println("client connected");
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            System.out.println("client disconnected(仅进程被杀时触发,unbind 不会)");
        }
    };

    public static boolean bind(Context context) {
        return context.bindService(new Intent(context, PlaybackServiceJava.class),
                CONNECTION, Context.BIND_AUTO_CREATE);
    }

    public static void main(String[] args) {
        System.out.println("本文件为 Android framework demo,需在 Android 环境运行");
    }
}
