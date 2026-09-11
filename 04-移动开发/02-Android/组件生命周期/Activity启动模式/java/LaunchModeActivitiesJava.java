package com.example.launchmode;

import android.content.Intent;
import android.os.Bundle;
import android.util.Log;

import androidx.appcompat.app.AppCompatActivity;

/**
 * 演示 5 种 launchMode 行为 —— 4 个 Activity 子类 + 1 个 Intent flags 入口 (Java 版)
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
 */
public class LaunchModeActivitiesJava {

    private static final String TAG = "LaunchModeDemo";

    /**
     * Demo 1 — standard
     */
    public static class StandardActivity extends AppCompatActivity {
        @Override
        protected void onCreate(Bundle savedInstanceState) {
            super.onCreate(savedInstanceState);
            Log.i(TAG, "[StandardActivity] onCreate instance=" + System.identityHashCode(this));
        }
    }

    /**
     * Demo 2 — singleTop(栈顶复用 + onNewIntent)
     */
    public static class SingleTopActivity extends AppCompatActivity {
        @Override
        protected void onCreate(Bundle savedInstanceState) {
            super.onCreate(savedInstanceState);
            Log.i(TAG, "[SingleTopActivity] onCreate instance=" + System.identityHashCode(this));
        }

        @Override
        protected void onNewIntent(Intent intent) {
            super.onNewIntent(intent);
            setIntent(intent);  // 必须调用,否则后续 getIntent() 返回旧 intent
            Log.i(TAG, "[SingleTopActivity] onNewIntent instance=" + System.identityHashCode(this) + " newIntent=" + intent);
        }
    }

    /**
     * Demo 3 — singleTask(同 affinity 唯一,清空上方)
     */
    public static class SingleTaskActivity extends AppCompatActivity {
        @Override
        protected void onCreate(Bundle savedInstanceState) {
            super.onCreate(savedInstanceState);
            Log.i(TAG, "[SingleTaskActivity] onCreate task=" + getTaskId() + " instance=" + System.identityHashCode(this));
        }

        @Override
        protected void onNewIntent(Intent intent) {
            super.onNewIntent(intent);
            setIntent(intent);
            Log.i(TAG, "[SingleTaskActivity] onNewIntent task=" + getTaskId() + " instance=" + System.identityHashCode(this));
        }
    }

    /**
     * Demo 4 — singleInstance(独占任务栈)
     */
    public static class SingleInstanceActivity extends AppCompatActivity {
        @Override
        protected void onCreate(Bundle savedInstanceState) {
            super.onCreate(savedInstanceState);
            Log.i(TAG, "[SingleInstanceActivity] onCreate task=" + getTaskId() + " instance=" + System.identityHashCode(this));
        }

        @Override
        protected void onNewIntent(Intent intent) {
            super.onNewIntent(intent);
            setIntent(intent);
            Log.i(TAG, "[SingleInstanceActivity] onNewIntent task=" + getTaskId() + " instance=" + System.identityHashCode(this));
        }
    }

    /**
     * Demo 5 — Intent flags(运行时覆盖 manifest launchMode)
     */
    public static class LaunchModeDemoActivity extends AppCompatActivity {
        @Override
        protected void onCreate(Bundle savedInstanceState) {
            super.onCreate(savedInstanceState);

            // 5.1 FLAG_ACTIVITY_NEW_TASK + FLAG_ACTIVITY_CLEAR_TOP
            Intent intent1 = new Intent(this, SingleTaskActivity.class);
            intent1.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            startActivity(intent1);

            // 5.2 FLAG_ACTIVITY_SINGLE_TOP(等价 singleTop)
            Intent intent2 = new Intent(this, SingleTopActivity.class);
            intent2.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startActivity(intent2);

            // 5.3 FLAG_ACTIVITY_CLEAR_TOP 单独使用(当前任务栈内清顶)
            Intent intent3 = new Intent(this, LaunchModeDemoActivity.class);
            intent3.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP);
            startActivity(intent3);
        }
    }
}