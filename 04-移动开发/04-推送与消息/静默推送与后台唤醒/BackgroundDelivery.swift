// BackgroundDelivery.swift
//
// 静默推送（background notification）的端侧语义，与 background_delivery.py 同一套规则。
// 权威来源（本轮实读全文）：
//   https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app
//
// 端侧要点：系统可能持有并延迟投递；新到的一条会顶掉旧的一条；App 被强制退出后
// 持有的通知被丢弃；用户启动 App 时立即投递；唤醒后只有 30 秒预算。

import Foundation

// MARK: - 预算与节流

/// 文档原文：唤醒后 App 有 30 秒完成任务并调用 completion handler。
public let backgroundBudgetSeconds: TimeInterval = 30

/// 文档原文："don't try to send more than two or three per hour"，取上界 3。
public let backgroundThrottleLimit = 3
public let backgroundThrottleWindow: TimeInterval = 3600

// MARK: - 载荷

public enum BackgroundPayloadError: Error {
    case missingAps
    case contentAvailableMustBeOne
    case userFacingKeyPresent(String)
}

/// 背景通知的 aps 只允许 `content-available`，不得含 alert / sound / badge。
public func validateBackgroundPayload(_ payload: [String: Any]) throws {
    guard let aps = payload["aps"] as? [String: Any] else {
        throw BackgroundPayloadError.missingAps
    }
    guard let flag = aps["content-available"] as? Int, flag == 1 else {
        throw BackgroundPayloadError.contentAvailableMustBeOne
    }
    for k in ["alert", "sound", "badge"] where aps[k] != nil {
        throw BackgroundPayloadError.userFacingKeyPresent(k)
    }
}

/// 背景通知要求的请求头组合。
public func backgroundRequestHeaders() -> [String: String] {
    return ["apns-push-type": "background", "apns-priority": "5"]
}

// MARK: - 状态

public enum BackgroundSendResult: String {
    case held
    case throttled
}

public enum BackgroundTaskResult: String {
    case completed
    case expired
}

/// 系统侧的"持有 - 延迟"行为模型。
public final class BackgroundDeliveryBox {
    private(set) public var heldID: String?
    private(set) public var delivered: [String] = []
    private(set) public var throttledCount = 0
    private(set) public var discardedByKill = 0
    private var sentTimes: [TimeInterval] = []

    public init() {}

    public func send(id: String, now: TimeInterval) -> BackgroundSendResult {
        sentTimes = sentTimes.filter { now - $0 < backgroundThrottleWindow }
        guard sentTimes.count < backgroundThrottleLimit else {
            throttledCount += 1
            return .throttled
        }
        sentTimes.append(now)
        heldID = id                      // 新的一条顶掉旧的一条
        return .held
    }

    /// 用户强制退出 / 系统杀掉 App。
    public func forceQuit() {
        if heldID != nil {
            heldID = nil
            discardedByKill += 1
        }
    }

    /// 用户主动启动 App：立即投递。
    @discardableResult
    public func userLaunch() -> String? {
        guard let id = heldID else { return nil }
        heldID = nil
        delivered.append(id)
        return id
    }

    /// 后台任务预算；超过 30 秒被系统判定为超时。
    public func runTask(duration: TimeInterval) -> BackgroundTaskResult {
        return duration > backgroundBudgetSeconds ? .expired : .completed
    }
}

// MARK: - AppDelegate 侧骨架（人工审查用，不可直接运行）

/*
 真实工程里对应的实现骨架（文档给出的回调）：

 func application(_ application: UIApplication,
                  didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                  fetchCompletionHandler completionHandler:
                  @escaping (UIBackgroundFetchResult) -> Void) {
     guard (userInfo["aps"] as? [String: Any])?["content-available"] as? Int == 1 else {
         completionHandler(.noData); return
     }
     // 30 秒预算内必须调用 completionHandler，否则系统会计一次超时
     fetchNewData { result in completionHandler(result) }
 }

 前置条件：Signing & Capability 里加 Background Modes 并勾选 Remote notification。
 */
