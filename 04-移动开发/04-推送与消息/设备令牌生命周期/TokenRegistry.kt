// TokenRegistry.kt
//
// 设备令牌注册与失效清理的 Kotlin 侧实现，与 token_registry.py 同一套语义。
// 权威来源：
//   https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns
//   https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns
// FCM 侧错误码来自官方 Admin SDK 源码：firebase_admin/messaging.py

// MARK: - 错误

class TokenRegistryError(message: String) : IllegalArgumentException(message)

// MARK: - 失效判定

/**
 * APNs 的 reason 中意味着令牌已失效、必须清理的那些（文档明列不可重试）。
 * FCM 官方 SDK 里对应 UNREGISTERED -> UnregisteredError。
 */
private val APNS_DEAD_TOKEN_REASONS = setOf(
    "BadDeviceToken", "DeviceTokenNotForTopic", "ExpiredToken", "Unregistered"
)

private val FCM_DEAD_TOKEN_CODES = setOf("UNREGISTERED")

/** 官方 SDK 的错误码 -> 异常类型映射（节选）。 */
val FCM_ERROR_TYPES: Map<String, String> = mapOf(
    "APNS_AUTH_ERROR" to "ThirdPartyAuthError",
    "QUOTA_EXCEEDED" to "QuotaExceededError",
    "SENDER_ID_MISMATCH" to "SenderIdMismatchError",
    "THIRD_PARTY_AUTH_ERROR" to "ThirdPartyAuthError",
    "UNREGISTERED" to "UnregisteredError"
)

fun isDeadTokenReason(reason: String): Boolean =
    reason in APNS_DEAD_TOKEN_REASONS || reason in FCM_DEAD_TOKEN_CODES

// MARK: - 注册结果

enum class RegisterResult { NEW, UNCHANGED, ROTATED }

data class DeadToken(val token: String, val reason: String, val at: Long)

// MARK: - 令牌库

/**
 * 服务端令牌库。硬约束（来自文档）：
 * 1. 每次 App 启动都要重新注册并上报 token；APNs 会在「从备份恢复」「装到新设备」
 *    「重装系统」时发新 token，所以不要缓存到本地；
 * 2. token 对「设备 + 应用」唯一，同一设备上的不同 App 不能复用；
 * 3. 一个用户可能有多台设备，因此一个用户对应多个 token；
 * 4. 收到失效类 reason 必须清理，否则形成重试风暴。
 */
class TokenRegistry {

    private val byDeviceApp = mutableMapOf<Pair<String, String>, String>()
    private val tokens = mutableMapOf<String, TokenMeta>()
    private val _dead = mutableListOf<DeadToken>()

    /** 已清理的令牌，供可观测性使用。 */
    val dead: List<DeadToken> get() = _dead

    var registrations: Int = 0
        private set

    data class TokenMeta(
        val userId: String, val deviceId: String, val appId: String, var lastSeen: Long
    )

    /**
     * 幂等：App 每次启动都会上报，重复上报同一个 token 不产生新记录。
     */
    fun register(
        userId: String, deviceId: String, appId: String, token: String, now: Long
    ): RegisterResult {
        require(token.isNotEmpty()) { "device token must not be empty" }
        val owner = tokens[token]
        if (owner != null && (owner.deviceId to owner.appId) != (deviceId to appId)) {
            throw TokenRegistryError(
                "device token is already bound to (${owner.deviceId}, ${owner.appId})"
            )
        }
        registrations++
        val key = deviceId to appId
        val old = byDeviceApp[key]
        if (old == token) {
            tokens[token]!!.lastSeen = now
            return RegisterResult.UNCHANGED
        }
        if (old != null) {
            tokens.remove(old)
            _dead.add(DeadToken(old, "rotated", now))
        }
        byDeviceApp[key] = token
        tokens[token] = TokenMeta(userId, deviceId, appId, now)
        return if (old == null) RegisterResult.NEW else RegisterResult.ROTATED
    }

    fun tokenFor(deviceId: String, appId: String): String? = byDeviceApp[deviceId to appId]

    fun tokensForUser(userId: String): List<String> =
        tokens.filter { it.value.userId == userId }.keys.sorted()

    fun size(): Int = tokens.size

    /** 清理一个已失效的令牌。 */
    fun invalidate(token: String, reason: String, now: Long = 0L): Boolean {
        val meta = tokens.remove(token) ?: return false
        byDeviceApp.remove(meta.deviceId to meta.appId)
        _dead.add(DeadToken(token, reason, now))
        return true
    }

    /**
     * 批量清理：只有「令牌失效类」的原因才删，其余（TooManyRequests 等）保留。
     */
    fun prune(failures: List<Pair<String, String>>, now: Long = 0L): Int {
        var removed = 0
        for ((token, reason) in failures) {
            if (isDeadTokenReason(reason) && invalidate(token, reason, now)) removed++
        }
        return removed
    }

    /** 实际可发送的令牌；已清理的自动缺席，这就是重试风暴的防线。 */
    fun sendable(userId: String): List<String> = tokensForUser(userId)
}

// MARK: - 端侧上报骨架（人工审查用）

/*
 Android / FCM 侧对应的回调骨架：

 class MyFirebaseMessagingService : FirebaseMessagingService() {
     override fun onNewToken(token: String) {
         // 每次拿到新 token 都上报；不要只在首次安装时上报
         api.uploadToken(userId, token)
     }
 }

 iOS 侧对应：

 func application(_ application: UIApplication,
                  didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
     sendDeviceTokenToServer(data: deviceToken)   // 每次启动都会回调
 }
 func application(_ application: UIApplication,
                  didFailToRegisterForRemoteNotificationsWithError error: Error) {
     needsRetryRegistration = true                // 置标志位，稍后重试
 }
 */
