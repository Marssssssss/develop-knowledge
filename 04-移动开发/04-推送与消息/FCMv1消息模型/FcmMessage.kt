// FcmMessage.kt
//
// FCM HTTP v1 消息模型的 Kotlin 侧实现，与 fcm_message.py 同一套规则。
// 权威来源：官方 firebase-admin-python 仓库的 _messaging_encoder.py / messaging.py
// （firebase.google.com 本轮不可达，故以官方 SDK 源码为语义依据）

// MARK: - 错误

class FcmMessageError(message: String) : IllegalArgumentException(message)

// MARK: - 常量

/** 官方 SDK：_MessagingService.FCM_URL */
private const val FCM_URL = "https://fcm.googleapis.com/v1/projects/%s/messages:send"

private val TARGET_FIELDS = listOf("fid", "token", "topic", "condition")

/** 分析标签：URL-safe 字符，长度 1~50 */
private val ANALYTICS_LABEL_RE = Regex("^[a-zA-Z0-9-_.~%]{1,50}$")

/** topic 名（去掉 /topics/ 前缀之后） */
private val TOPIC_NAME_RE = Regex("^[a-zA-Z0-9-_.~%]+$")

private val COLOR_RE = Regex("^#[0-9a-fA-F]{6}$")

val ANDROID_PRIORITIES = listOf("high", "normal")
val NOTIFICATION_PRIORITIES = listOf("min", "low", "default", "high", "max")
val VISIBILITIES = listOf("private", "public", "secret")
val PROXIES = listOf("allow", "deny", "if_priority_lowered")

// MARK: - 工具

/** 官方 SDK：剔除值为 null / 空集合 / 空 map 的键；false 与 0 会被保留。 */
fun removeNullValues(d: Map<String, Any?>): Map<String, Any?> =
    d.filterValues { v -> v != null && v !is List<*> && v !is Map<*, *> }.toMutableMap().also { m ->
        d.forEach { (k, v) ->
            if (v is List<*> && v.isNotEmpty()) m[k] = v
            if (v is Map<*, *> && v.isNotEmpty()) m[k] = v
        }
    }

fun checkAnalyticsLabel(label: String, value: String?): String? {
    if (value == null) return null
    require(ANALYTICS_LABEL_RE.matches(value)) { "Malformed $label." }
    return value
}

/** 去掉 /topics/ 前缀后校验字符集。 */
fun sanitizeTopicName(topic: String?): String? {
    if (topic.isNullOrEmpty()) return null
    val stripped = if (topic.startsWith("/topics/")) topic.removePrefix("/topics/") else topic
    require(TOPIC_NAME_RE.matches(stripped)) { "Malformed topic name." }
    return stripped
}

/**
 * TTL 编码：秒数或 Duration -> duration 字符串。
 * 整数秒 -> "3s"；带纳秒 -> "3.500000000s"（固定 9 位）。
 */
fun encodeTtl(seconds: Double): String {
    require(seconds >= 0) { "AndroidConfig.ttl must not be negative." }
    val whole = kotlin.math.floor(seconds).toLong()
    val nanos = ((seconds - whole) * 1e9).toLong()
    return if (nanos != 0L) "%d.%09ds".format(whole, nanos) else "%ds".format(whole)
}

fun checkColor(color: String?) {
    if (color != null) {
        require(COLOR_RE.matches(color)) { "AndroidNotification.color must be in the form #RRGGBB." }
    }
}

// MARK: - 平台覆写

/**
 * Android 通知优先级：min/low/default/high/max -> PRIORITY_MIN ... PRIORITY_MAX。
 */
fun encodeNotificationPriority(priority: String): String {
    require(priority in NOTIFICATION_PRIORITIES) {
        "AndroidNotification.priority must be \"default\", \"min\", \"low\", \"high\" or \"max\"."
    }
    return "PRIORITY_" + priority.uppercase()
}

fun encodeVisibility(visibility: String): String {
    require(visibility in VISIBILITIES) {
        "AndroidNotification.visibility must be \"private\", \"public\" or \"secret\"."
    }
    return visibility.uppercase()
}

fun encodeProxy(proxy: String): String {
    require(proxy in PROXIES) {
        "AndroidNotification.proxy must be \"allow\", \"deny\" or \"if_priority_lowered\"."
    }
    return proxy.uppercase()
}

/** Android 传输层优先级：只接受 high / normal，且原样保留。 */
fun encodeAndroidPriority(priority: String): String {
    require(priority in ANDROID_PRIORITIES) {
        "AndroidConfig.priority must be \"high\" or \"normal\"."
    }
    return priority
}

/**
 * aps 字典：content_available / mutable_content 为 true 时写成数值 1（不是 true）。
 */
fun encodeAps(
    alert: String? = null,
    badge: Int? = null,
    sound: String? = null,
    category: String? = null,
    threadId: String? = null,
    contentAvailable: Boolean = false,
    mutableContent: Boolean = false
): Map<String, Any> {
    val out = mutableMapOf<String, Any>()
    alert?.let { out["alert"] = it }
    badge?.let { out["badge"] = it }
    sound?.let { out["sound"] = it }
    category?.let { out["category"] = it }
    threadId?.let { out["thread-id"] = it }
    if (contentAvailable) out["content-available"] = 1
    if (mutableContent) out["mutable-content"] = 1
    return out
}

// MARK: - 消息

/**
 * 目标四选一：fid / token / topic / condition 必须恰好指定一个。
 */
fun encodeMessage(
    fid: String? = null,
    token: String? = null,
    topic: String? = null,
    condition: String? = null,
    android: Map<String, Any?>? = null,
    apns: Map<String, Any?>? = null,
    data: Map<String, String>? = null
): Map<String, Any?> {
    val targets = listOf(fid, token, sanitizeTopicName(topic), condition).count { it != null }
    require(targets == 1) {
        "Exactly one of fid, token, topic or condition must be specified."
    }
    val out = mutableMapOf<String, Any?>(
        "fid" to fid,
        "token" to token,
        "topic" to topic?.let { sanitizeTopicName(it) },
        "condition" to condition,
        "android" to android?.let { removeNullValues(it) },
        "apns" to apns?.let { removeNullValues(it) },
        "data" to data
    )
    return removeNullValues(out)
}

fun fcmSendUrl(projectId: String): String = FCM_URL.format(projectId)

/** 官方 SDK 的错误码 -> 异常类型映射（节选）。 */
fun classifyError(code: String): String = when (code) {
    "APNS_AUTH_ERROR" -> "ThirdPartyAuthError"
    "QUOTA_EXCEEDED" -> "QuotaExceededError"
    "SENDER_ID_MISMATCH" -> "SenderIdMismatchError"
    "THIRD_PARTY_AUTH_ERROR" -> "ThirdPartyAuthError"
    "UNREGISTERED" -> "UnregisteredError"
    else -> "UnknownError"
}
