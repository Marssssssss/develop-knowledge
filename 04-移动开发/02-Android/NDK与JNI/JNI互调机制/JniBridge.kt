// Kotlin 侧声明：与 main.py 的模型对偶。真机上 external 函数由 libjni_bridge.so 提供。

class JniBridge {

    // 非 static：native 侧第二个参数是 jobject（this）
    external fun nativeAdd(a: Int, b: Int): Int

    // static：native 侧第二个参数是 jclass
    external fun nativeVersion(): String

    // 重载：native 侧必须用长名 Java_..._nativeScale__D 与 Java_..._nativeScale__I
    external fun nativeScale(v: Double): Double
    external fun nativeScale(v: Int): Int

    companion object {
        // JNI_OnLoad 返回的版本由 so 决定；这里只声明加载顺序。
        // 经验规则：库必须先加载，external 才能解析；重复 loadLibrary 是 no-op。
        var loaded = false
            private set

        @Synchronized
        fun ensureLoaded() {
            if (!loaded) {
                System.loadLibrary("jni_bridge")
                loaded = true
            }
        }

        // 与 register_natives 对偶：把 (name, signature) → 实现的映射显式化
        fun nativeTable(): Map<Pair<String, String>, String> = mapOf(
            ("nativeAdd", "(II)I") to "Java_pkg_JniBridge_nativeAdd",
            ("nativeVersion", "()Ljava/lang/String;") to "Java_pkg_JniBridge_nativeVersion",
            ("nativeScale", "(D)D") to "Java_pkg_JniBridge_nativeScale__D",
            ("nativeScale", "(I)I") to "Java_pkg_JniBridge_nativeScale__I",
        )

        // 与 long_name 对偶：长名只含参数签名，不含返回类型
        fun longName(cls: String, method: String, params: String): String =
            "Java_" + mangle(cls.replace(".", "/")) + "_" + mangle(method) + "__" + mangle(params)

        fun shortName(cls: String, method: String): String =
            "Java_" + mangle(cls.replace(".", "/")) + "_" + mangle(method)

        // 规范 Table: Unicode Character Translation
        fun mangle(text: String): String {
            val sb = StringBuilder()
            for (ch in text) {
                when {
                    ch == '/' -> sb.append('_')
                    ch == '_' -> sb.append("_1")
                    ch == ';' -> sb.append("_2")
                    ch == '[' -> sb.append("_3")
                    ch.isLetterOrDigit() && ch.code < 128 -> sb.append(ch)
                    else -> sb.append("_0" + ch.code.toString(16).padStart(4, '0'))
                }
            }
            return sb.toString()
        }
    }
}
