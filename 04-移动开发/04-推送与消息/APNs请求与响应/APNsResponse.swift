// APNsResponse.swift
//
// APNs 响应分派的 Swift 侧实现，与 apns_request.py 同一套语义。
// 权威来源（本轮实读全文）：
//   https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns
//
// 注意：真实工程里服务端才是接收 APNs 响应的一方；Swift 端这份实现用于
// 「客户端 SDK / 自建 Provider 组件」中复用同一套判定规则，避免两端规则漂移。

import Foundation

// MARK: - 状态码

/// 文档列出的全部 :status 取值。
public enum ApnsStatus: Int, CaseIterable, CustomStringConvertible {
    case ok = 200
    case badRequest = 400
    case certificateOrTokenError = 403
    case badPath = 404
    case methodNotAllowed = 405
    case tokenInactive = 410
    case payloadTooLarge = 413
    case tooManyRequests = 429
    case internalServerError = 500
    case serviceUnavailable = 503

    public var description: String {
        switch self {
        case .ok: return "Success."
        case .badRequest: return "Bad request."
        case .certificateOrTokenError:
            return "There was an error with the certificate or with the provider's authentication token."
        case .badPath: return "The request contained an invalid :path value."
        case .methodNotAllowed:
            return "The request used an invalid :method value. Only POST requests are supported."
        case .tokenInactive: return "The device token is no longer active for the topic."
        case .payloadTooLarge: return "The notification payload was too large."
        case .tooManyRequests: return "The server received too many requests for the same device token."
        case .internalServerError: return "Internal server error."
        case .serviceUnavailable: return "The server is shutting down and unavailable."
        }
    }

    /// 文档原文：410 不被当作 error condition。
    public var isErrorCondition: Bool {
        return self != .ok && self != .tokenInactive
    }

    public var isServerError: Bool {
        return rawValue >= 500 && rawValue < 600
    }
}

// MARK: - reason 错误串

public enum ApnsReason: String, CaseIterable {
    case badCollapseId = "BadCollapseId"
    case badDeviceToken = "BadDeviceToken"
    case badExpirationDate = "BadExpirationDate"
    case badMessageId = "BadMessageId"
    case badPriority = "BadPriority"
    case badTopic = "BadTopic"
    case deviceTokenNotForTopic = "DeviceTokenNotForTopic"
    case duplicateHeaders = "DuplicateHeaders"
    case idleTimeout = "IdleTimeout"
    case invalidPushType = "InvalidPushType"
    case missingDeviceToken = "MissingDeviceToken"
    case missingTopic = "MissingTopic"
    case payloadEmpty = "PayloadEmpty"
    case topicDisallowed = "TopicDisallowed"
    case badCertificate = "BadCertificate"
    case badCertificateEnvironment = "BadCertificateEnvironment"
    case expiredProviderToken = "ExpiredProviderToken"
    case forbidden = "Forbidden"
    case invalidProviderToken = "InvalidProviderToken"
    case missingProviderToken = "MissingProviderToken"
    case unrelatedKeyIdInToken = "UnrelatedKeyIdInToken"
    case badEnvironmentKeyIdInToken = "BadEnvironmentKeyIdInToken"
    case badPath = "BadPath"
    case methodNotAllowed = "MethodNotAllowed"
    case expiredToken = "ExpiredToken"
    case unregistered = "Unregistered"
    case payloadTooLarge = "PayloadTooLarge"
    case tooManyProviderTokenUpdates = "TooManyProviderTokenUpdates"
    case tooManyRequests = "TooManyRequests"
    case internalServerError = "InternalServerError"
    case serviceUnavailable = "ServiceUnavailable"
    case shutdown = "Shutdown"

    /// 文档原文：这些 reason 一律不要再重试。
    public var isNeverRetry: Bool {
        switch self {
        case .badDeviceToken, .deviceTokenNotForTopic, .forbidden,
             .expiredToken, .unregistered, .payloadTooLarge:
            return true
        default:
            return false
        }
    }

    /// 令牌类失效：必须清理设备令牌，否则形成重试风暴。
    public var invalidatesDeviceToken: Bool {
        switch self {
        case .badDeviceToken, .deviceTokenNotForTopic, .expiredToken, .unregistered:
            return true
        default:
            return false
        }
    }

    /// 文档原文：只有 TooManyRequests 可以「延迟后重试」。
    public var isDelayRetry: Bool {
        return self == .tooManyRequests
    }

    /// 响应体里的 timestamp 键只在 reason 为 Unregistered 时出现。
    public var carriesTimestamp: Bool {
        return self == .unregistered
    }
}

// MARK: - 动作

public enum ApnsAction: String {
    case success
    case dropToken          // 清理设备令牌，禁止重试
    case neverRetry         // 不可重试
    case retryLater         // 延迟后退避重试
    case retryAfterBackoff  // 5XX
    case fixThenRetry       // 修好 reason 指明的问题后可重试
}

// MARK: - 响应解析

public struct ApnsErrorBody: Decodable {
    public let reason: ApnsReason
    /// 毫秒级 epoch；仅 reason == .unregistered 时存在
    public let timestamp: Int?
}

public enum ApnsResponseError: Error {
    case missingReason
    case unexpectedTimestamp(reason: ApnsReason)
}

/// 与 Python 版 classify_response 一一对应。
public func classify(status: ApnsStatus, reason: ApnsReason?) -> ApnsAction {
    if status == .ok { return .success }
    if let r = reason {
        if r.isNeverRetry {
            return r.invalidatesDeviceToken ? .dropToken : .neverRetry
        }
        if r.isDelayRetry { return .retryLater }
    }
    if status.isServerError { return .retryAfterBackoff }
    return .fixThenRetry
}

/// 解析错误响应体并校验「timestamp 只伴随 Unregistered」这条约束。
public func parseErrorBody(_ data: Data) throws -> ApnsErrorBody {
    let body = try JSONDecoder().decode(ApnsErrorBody.self, from: data)
    if let ts = body.timestamp, !body.reason.carriesTimestamp {
        throw ApnsResponseError.unexpectedTimestamp(reason: body.reason)
    }
    return body
}
