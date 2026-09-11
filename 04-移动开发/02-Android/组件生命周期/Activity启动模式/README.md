# Activity 启动模式(launchMode)

## 简介

Android 通过 `android:launchMode` 属性(清单文件)和 `Intent` flags(运行时)控制 Activity 实例与任务的关联方式,共同解决"启动 Activity 时是新建还是复用"的问题。

**关键概念清单**:
- **任务(Task)**:Activity 的栈,系统用 taskId 标识;每个任务有自己的回退栈
- **回退栈(Back Stack)**:Activity 后进先出栈;按返回键依次弹出
- **launchMode**:5 种 — standard / singleTop / singleTask / singleInstance / singleInstancePerTask
- **taskAffinity**:Activity 偏好的任务(默认包名);`singleTask` 等模式据此找任务
- **onNewIntent**:复用现有 Activity 实例时,系统调用的回调方法(注意必须调用 `setIntent(intent)`)

**历史背景**:Android 1.0(API 1)起即存在 standard / singleTop / singleTask / singleInstance 四种;`singleInstancePerTask` 在 Android 12(API 31)新增,允许在不同任务中复用。早期 Webview、地图、Launcher 等系统组件大量使用 singleTask / singleInstance 解决"跨应用 Activity 实例共享"的问题。

## 原理详解

### 工作机制分步说明

启动 Activity 的判定流程(framework 层面简化):

1. **解析 Intent**:取出 ComponentName(即目标 Activity 类名)和 flags
2. **合并优先级**:Intent flag 与 manifest launchMode 合并;Intent flag 优先级更高
3. **查找现有实例**:按当前任务栈 → 同 taskAffinity 栈 → 新建任务的顺序查找
4. **判定复用条件**:
   - `singleInstance` 任何位置已有该 Activity → 直接复用并 onNewIntent
   - `singleTask` / `singleInstancePerTask` 同 affinity 栈根位置已有 → 清空其上方所有 Activity 后 onNewIntent
   - `singleTop` 当前栈顶已有 → 复用 onNewIntent;否则新建
   - `standard` 总是新建
5. **创建 / 分发**:
   - 新建 → onCreate(savedInstanceState) → onStart → onResume
   - 复用 → onPause → onNewIntent(intent) → onResume(savedInstanceState 不变)

### launchMode 行为对照表

| launchMode | 是否允许多实例 | 是否允许同任务多实例 | 是否允许跨任务多实例 | 复用触发回调 | 典型用途 |
| --- | --- | --- | --- | --- | --- |
| `standard` | ✅ | ✅ | ✅ | 无(总是新建) | 大多数 Activity |
| `singleTop` | ✅ | ✅(但同栈仅顶部复用) | ✅ | onNewIntent | 搜索结果页(连续点击) |
| `singleTask` | ❌(同 affinity 唯一) | ❌(根位置) | ✅(不同 affinity) | onNewIntent | 浏览器主页、IM 聊天 |
| `singleInstance` | ❌(全设备唯一) | ❌(独占任务) | ❌ | onNewIntent | 来电、Launcher |
| `singleInstancePerTask` | ❌(每任务唯一) | ❌(根位置) | ✅ | onNewIntent(API 31+) | 文档型 Activity |

### Intent flag 优先级

| Flag | 行为 | 等价 launchMode |
| --- | --- | --- |
| `FLAG_ACTIVITY_NEW_TASK` | 在新任务中启动 | 与 `singleTask` 的新任务行为等价 |
| `FLAG_ACTIVITY_SINGLE_TOP` | 栈顶复用 | 与 `singleTop` 等价 |
| `FLAG_ACTIVITY_CLEAR_TOP` | 清掉目标上方的所有 Activity | 无等价 manifest 模式 |
| `FLAG_ACTIVITY_CLEAR_TOP \| FLAG_ACTIVITY_NEW_TASK` | 跨任务找 + 清顶 | 与 `singleTask` 的清栈行为等价 |

**重要规则**:Intent flag 优先级**始终高于** manifest launchMode(用户路径覆盖作者意图);但 `singleInstance` / `singleInstancePerTask` 不能被 Intent flag 模拟,只能用 manifest 声明。

### 任务栈 ASCII 图

```
初始任务栈(taskA, 包名 com.example):          standard 行为:
┌──────┐                                      startActivity(C) →
│   C  │ ← 栈顶                                  ┌──────┐
├──────┤                                       │   C' │ ← 新实例
│   B  │                                       ├──────┤
├──────┤                                       │   C  │
│   A  │ ← 根                                  ├──────┤
└──────┘                                       │   B  │
                                               ├──────┤
                                               │   A  │
                                               └──────┘

同栈 startActivity(C) 但 C 是 singleTop:        同栈 startActivity(C) 但 C 是 singleTask:
  C 已在栈顶 → 复用,触发 onNewIntent              C 不在 task 根 → 清掉 B, 触发 onNewIntent
  ┌──────┐                                       ┌──────┐
  │   C  │ ← 同一实例,onNewIntent 触发          │   C  │ ← 同一实例,onNewIntent 触发
  ├──────┤                                       └──────┘
  │   B  │                                       (B 被 finish)
  ├──────┤
  │   A  │
  └──────┘
```

### 核心 API

| 类 / 方法 | 签名 | 说明 |
| --- | --- | --- |
| `Activity.onNewIntent(Intent)` | `protected void` | 复用现有 Activity 时调用,必须调 `setIntent(intent)` 更新 |
| `Activity.getTaskId()` | `int` | 返回当前任务 taskId;相同 taskId 属同一任务栈 |
| `Activity.getTask()` | `TaskInfo` | API 29+ 返回当前任务信息(affinity 等) |
| `Intent.addFlags(int)` | `Intent` | 添加 flag |
| `Intent.setFlags(int)` | `Intent` | 替换所有 flag |
| `Intent.FLAG_ACTIVITY_*` | `static final int` | 见上节对照表 |

## 对比 / 选型

| 场景 | 推荐 launchMode | 原因 |
| --- | --- | --- |
| 普通页面(详情、表单) | `standard` | 默认行为,简单可控 |
| 顶部列表(搜索结果、新闻) | `singleTop` | 连续点击同一搜索词复用实例,避免重复加载 |
| 应用主页、IM 聊天 | `singleTask` | 确保唯一实例,跨任务复用并清掉中间页 |
| 来电 Activity、系统组件 | `singleInstance` | 独占任务,跨应用启动也唯一 |
| 文档型(每文档一任务) | `singleInstancePerTask` | API 31+,文档作为任务根 |

## 环境准备

- Android Studio Hedgehog(2023.1+)或更新
- minSdk 21(前 4 种);minSdk 31(用于 `singleInstancePerTask`)
- compileSdk 33,targetSdk 33
- AndroidX `appcompat:1.6.0+`(Java/Kotlin 版需要)

## 运行方式

将 `kotlin/LaunchModeActivities.kt` 或 `java/LaunchModeActivitiesJava.java` 放入 Android 项目,在 `AndroidManifest.xml` 声明各 Activity 的 launchMode + taskAffinity。启动 `LaunchModeDemoActivity`,通过 logcat(过滤 `LaunchModeDemo`)观察 onCreate / onNewIntent 调用序列。

```xml
<!-- AndroidManifest.xml 关键片段 -->
<application ...>
    <activity android:name=".launchmode.LaunchModeDemoActivity"
              android:exported="true">
        <intent-filter>
            <action android:name="android.intent.action.MAIN" />
            <category android:name="android.intent.category.LAUNCHER" />
        </intent-filter>
    </activity>

    <activity android:name=".launchmode.StandardActivity" />
    <activity android:name=".launchmode.SingleTopActivity"
              android:launchMode="singleTop" />
    <activity android:name=".launchmode.SingleTaskActivity"
              android:launchMode="singleTask"
              android:taskAffinity=".singleTaskAffinity" />
    <activity android:name=".launchmode.SingleInstanceActivity"
              android:launchMode="singleInstance"
              android:taskAffinity=".singleInstanceAffinity" />
</application>
```

## 关键代码片段

### singleTop onNewIntent 复用(对应 Demo 2)

```kotlin
class SingleTopActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i("Demo", "onCreate ${System.identityHashCode(this)}")
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)  // 必须!后续 getIntent() 才能拿到新 intent
        Log.i("Demo", "onNewIntent ${System.identityHashCode(this)} $intent")
    }
}
```

### Intent flag CLEAR_TOP + NEW_TASK(对应 Demo 5.1)

```kotlin
val intent = Intent(this, SingleTaskActivity::class.java).apply {
    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
}
startActivity(intent)
// 效果:跨任务栈找到 SingleTaskActivity 实例,清掉其上方所有 Activity,触发 onNewIntent
// 等价于 manifest 上声明 singleTask
```

### taskAffinity 跨任务隔离

```kotlin
// AndroidManifest.xml:
<activity android:name=".SecretActivity"
          android:taskAffinity=""  // 空字符串 → 与其他应用隔离
          android:launchMode="singleTask" />
// 效果:SecretActivity 永远不会与其他任务的 Activity 共享栈
// 适用于安全敏感页面
```

## 性能与边界

- **任务数量**:Android 不会主动清理任务;Recent 列表最多展示约 50 个任务;`excludeFromRecents=true` 可隐藏
- **跨任务启动开销**:FLAG_ACTIVITY_NEW_TASK 触发任务切换,有 ~100ms 延迟(冷启动);热启动 ~10ms
- **singleInstance 限制**:同一应用的多个 singleInstance Activity 会各自独占一个任务(taskId 不同),可能造成任务碎片化
- **API 31+ singleInstancePerTask**:允许同 Activity 在不同任务中存在多个实例(每任务一个);`singleTask` 仍只允许同 affinity 一个根
- **Back 键行为**:`singleTask` 启动的新任务中,Back 键仍返回原任务栈,而不是直接退出应用(用户可能误以为应用未退出)

## 注意事项与常见坑

1. **singleTop 不复用栈中非顶部实例**:栈 A → B → C(都是 B 的同 Activity),B 是 singleTop,从 C 再 startActivity(B) → 新建 B 实例(栈 A → B → C → B')。
2. **onNewIntent 必须 setIntent**:`setIntent(intent)` 不调,后续 `getIntent()` 返回旧的;这点在 SearchableActivity 处理 Search Intent 时最常见。
3. **singleTask 清栈不调 onDestroy**:被清掉的 Activity 走 onPause → onStop → onDestroy,但 onNewIntent 走完 onResume,新 Activity 复用原栈;B/C 被 finish 但 singleTask 实例的 savedInstanceState 保留。
4. **taskAffinity 默认值**:默认是 applicationId 包名;跨进程启动时 task 会被创建;空字符串 taskAffinity 让 Activity 始终在新任务(空任务)。
5. **systemProperty persist.taskAffinity**:OEM 修改可能改变默认行为;跨设备测试需注意。
6. **不要混用 launchMode + FLAG_ACTIVITY_NEW_DOCUMENT**:文档场景必须用 `singleInstancePerTask`(API 31+)或 `documentLaunchMode`,否则任务重复。
7. **singleInstance 启动其他 Activity 自动加 NEW_TASK**:从 singleInstance 启动 standard Activity 等同于自动 `FLAG_ACTIVITY_NEW_TASK`,可能在另一任务,导致返回栈穿越。
8. **Android 12(API 31)任务栈限制**:任务根 Activity 必须显式声明 android:documentLaunchMode 或 android:launchMode;否则 Back 键返回 Home 而非任务栈(预测式返回手势相关)。
9. **测试技巧**:用 `adb shell dumpsys activity activities` 观察 taskId 与 Activity 关联,确认 launchMode 行为是否符合预期。

## 参考资料(实际阅读过的权威来源)

- [<activity> | Android Developers](https://developer.android.google.cn/guide/topics/manifest/activity-element) — 5 种 launchMode 完整定义 + 用例表 + 与 intent flag 交互
- [Tasks and the back stack | Android Developers](https://developer.android.google.cn/guide/components/activities/tasks-and-back-stack) — 任务与回退栈机制,示例 A-B-C-D 栈演化
- [Intent | Android Developers](https://developer.android.google.cn/reference/android/content/Intent) — FLAG_ACTIVITY_* 常量定义
- [Activity lifecycle | Android Developers](https://developer.android.google.cn/guide/components/activities/activity-lifecycle) — onNewIntent / onResume 顺序
- [Android 12 launchMode 变更](https://developer.android.google.cn/guide/components/activities/launchmode) — singleInstancePerTask + 预测式返回手势