package com.example.launchmode

import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.appcompat.app.AppCompatActivity

/**
 * 演示 5 种 launchMode 行为 —— 4 个 Activity 子类 + 1 个 Intent flags 入口
 *
 * AndroidManifest.xml 需声明:
 *
 *   <activity android:name=".StandardActivity" android:launchMode="standard" />
 *   <activity android:name=".SingleTopActivity" android:launchMode="singleTop" />
 *   <activity android:name=".SingleTaskActivity"
 *             android:launchMode="singleTask"
 *             android:taskAffinity=".singleTaskAffinity" />
 *   <activity android:name=".SingleInstanceActivity"
 *             android:launchMode="singleInstance"
 *             android:taskAffinity=".singleInstanceAffinity" />
 *
 * 运行入口:LaunchModeDemoActivity(默认启动) → 点击按钮触发各 launchMode 演示
 */

/**
 * Demo 1 — standard:每次 startActivity() 都创建新实例
 *
 * 行为:
 *   - 栈 A → B → C → 重新 start C → 栈 A → B → C → C (C 顶部压新 C)
 *   - 每个实例处理一个 intent;onCreate() / onNewIntent() 都不会触发复用
 *   - 默认 launchMode,适用于大多数页面
 */
class StandardActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i(TAG, "[StandardActivity] onCreate instance=${System.identityHashCode(this)}")
    }
}

/**
 * Demo 2 — singleTop:若目标 Activity 已在栈顶 → 复用现有实例并触发 onNewIntent
 *
 * 行为:
 *   - 栈 A → B → C → startActivity(C) → 栈 A → B → C(同一个 C,onNewIntent 触发)
 *   - 栈 A → B → C → startActivity(B) → 栈 A → B → B(新建 B,不复用栈中已有的)
 *   - 适用场景:Activity 已经在栈顶时希望复用(如搜索结果连续点击同一搜索词)
 */
class SingleTopActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i(TAG, "[SingleTopActivity] onCreate instance=${System.identityHashCode(this)}")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)  // 必须调用,否则后续 getIntent() 返回旧 intent
        Log.i(TAG, "[SingleTopActivity] onNewIntent instance=${System.identityHashCode(this)} newIntent=$intent")
    }
}

/**
 * Demo 3 — singleTask:在指定 taskAffinity 的栈中只能存在一个实例,已存在则清空上方 Activity 并 onNewIntent
 *
 * 行为:
 *   - 同 affinity 栈中已有该 Activity → 清掉其上方所有 Activity,触发 onNewIntent
 *   - 同 affinity 栈中无该 Activity → 新建任务栈并作为根 Activity
 *   - taskAffinity 默认是包名,可通过 android:taskAffinity 自定义(同一应用内可以是空字符串跨进程隔离)
 *   - 适用场景:浏览器主页(避免堆叠)、IM 聊天窗口(进入特定会话)
 */
class SingleTaskActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i(TAG, "[SingleTaskActivity] onCreate task=${task.taskId} instance=${System.identityHashCode(this)}")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        Log.i(TAG, "[SingleTaskActivity] onNewIntent task=${task.taskId} instance=${System.identityHashCode(this)}")
    }
}

/**
 * Demo 4 — singleInstance:整个任务栈只能容纳该 Activity 一个
 *
 * 行为:
 *   - 该 Activity 始终独占一个 task(taskId 与其他 task 不同)
 *   - 其他 Activity(standard/singleTop)启动后会被压到普通 task,而非该 task
 *   - 适用场景:电话来电 Activity(必须独占)、Launcher 启动器
 *   - 注意:singleInstance 的 Activity 启动其他 Activity 等同于自动加 FLAG_ACTIVITY_NEW_TASK
 */
class SingleInstanceActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i(TAG, "[SingleInstanceActivity] onCreate task=${task.taskId} instance=${System.identityHashCode(this)}")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        Log.i(TAG, "[SingleInstanceActivity] onNewIntent task=${task.taskId} instance=${System.identityHashCode(this)}")
    }
}

/**
 * Demo 5 — Intent flags 控制启动行为(优先级高于 manifest)
 *
 * 三个关键 flag:
 *   FLAG_ACTIVITY_NEW_TASK        → 在新任务中启动(等价于 singleTask 的新任务行为)
 *   FLAG_ACTIVITY_SINGLE_TOP      → 等价于 singleTop
 *   FLAG_ACTIVITY_CLEAR_TOP       → 清空栈中目标 Activity 上方的所有 Activity
 *
 * 优先级规则:Intent flag 总是覆盖 manifest launchMode(用户路径覆盖作者意图)
 */
class LaunchModeDemoActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 5.1 FLAG_ACTIVITY_NEW_TASK + FLAG_ACTIVITY_CLEAR_TOP:
        //   - 已有同 affinity 栈 → 复用并清空上方
        //   - 等价于 singleTask 的清栈效果
        val intent1 = Intent(this, SingleTaskActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        }
        startActivity(intent1)

        // 5.2 FLAG_ACTIVITY_SINGLE_TOP:
        //   - 若 SingleTopActivity 在栈顶 → 触发 onNewIntent,不新建
        //   - 若不在栈顶 → 正常新建(等价 singleTop)
        val intent2 = Intent(this, SingleTopActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        startActivity(intent2)

        // 5.3 FLAG_ACTIVITY_CLEAR_TOP 单独使用:
        //   - 找到栈中已存在的目标 → 清掉它上方的 → 触发 onNewIntent
        //   - 不会新建任务;在当前任务栈内操作
        //   - 常用于"返回主页"导航
        val intent3 = Intent(this, LaunchModeDemoActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
        }
        startActivity(intent3)
    }

    companion object {
        private const val TAG = "LaunchModeDemo"
    }
}