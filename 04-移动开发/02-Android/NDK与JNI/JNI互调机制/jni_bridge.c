/*
 * C 侧参考实现（本机无 NDK/编译器，未实际编译，仅作人工审查）。
 * 对应 Kotlin 侧的 external 声明与 jni.h 的引用操作。
 */
#include <jni.h>
#include <stdlib.h>
#include <string.h>

#define JNI_VERSION_RETURN JNI_VERSION_1_6

static jclass g_self_class = NULL;   /* 必须是全局引用：方法返回后局部引用即失效 */

JNIEXPORT jint JNICALL
Java_pkg_JniBridge_nativeAdd(JNIEnv *env, jobject thiz, jint a, jint b) {
    (void)thiz;
    return a + b;
}

JNIEXPORT jstring JNICALL
Java_pkg_JniBridge_nativeVersion(JNIEnv *env, jclass cls) {
    return (*env)->NewStringUTF(env, "1.6");
}

/* 重载：只有参数部分进符号名，返回类型不参与 */
JNIEXPORT jdouble JNICALL
Java_pkg_JniBridge_nativeScale__D(JNIEnv *env, jobject thiz, jdouble v) {
    (void)thiz;
    return v * 2.0;
}

JNIEXPORT jint JNICALL
Java_pkg_JniBridge_nativeScale__I(JNIEnv *env, jobject thiz, jint v) {
    (void)thiz;
    return v * 2;
}

/*
 * 局部引用帧：循环里创建的对象全部随 PopLocalFrame 释放，
 * 只有 result 被提升到外层帧。
 */
JNIEXPORT jobjectArray JNICALL
Java_pkg_JniBridge_nativeBatch(JNIEnv *env, jobject thiz, jint count) {
    (void)thiz;
    jobjectArray out;
    if ((*env)->PushLocalFrame(env, 16) != JNI_OK) {
        return NULL;
    }
    jclass stringClass = (*env)->FindClass(env, "java/lang/String");
    if (stringClass == NULL) {
        (*env)->PopLocalFrame(env, NULL);
        return NULL;
    }
    out = (*env)->NewObjectArray(env, count, stringClass, NULL);
    for (jint i = 0; i < count; i++) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d", i);
        jstring s = (*env)->NewStringUTF(env, buf);
        if (s == NULL) {
            out = NULL;
            break;
        }
        (*env)->SetObjectArrayElement(env, out, i, s);
        /* s 是局部引用，本帧结束统一释放；不手动 DeleteLocalRef */
    }
    return (jobjectArray)(*env)->PopLocalFrame(env, out);
}

/*
 * 版本协商：返回 VM 不支持的版本会导致 System.loadLibrary 抛 UnsatisfiedLinkError。
 * 全局引用必须在 JNI_OnLoad 里建（此处 jclass 需 NewGlobalRef 保活）。
 */
JNIEXPORT jint JNICALL
JNI_OnLoad(JavaVM *vm, void *reserved) {
    (void)reserved;
    JNIEnv *env = NULL;
    if ((*vm)->GetEnv(vm, (void **)&env, JNI_VERSION_RETURN) != JNI_OK) {
        return JNI_ERR;
    }
    jclass local = (*env)->FindClass(env, "pkg/JniBridge");
    if (local == NULL) {
        return JNI_ERR;
    }
    g_self_class = (jclass)(*env)->NewGlobalRef(env, local);   /* 缓存必须提升为全局引用 */
    (*env)->DeleteLocalRef(env, local);
    if (g_self_class == NULL) {
        return JNI_ERR;
    }
    return JNI_VERSION_RETURN;
}

JNIEXPORT void JNICALL
JNI_OnUnload(JavaVM *vm, void *reserved) {
    (void)reserved;
    JNIEnv *env = NULL;
    if ((*vm)->GetEnv(vm, (void **)&env, JNI_VERSION_RETURN) != JNI_OK) {
        return;
    }
    if (g_self_class != NULL) {
        (*env)->DeleteGlobalRef(env, g_self_class);
        g_self_class = NULL;
    }
}
