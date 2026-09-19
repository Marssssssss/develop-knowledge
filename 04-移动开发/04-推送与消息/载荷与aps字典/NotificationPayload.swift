// NotificationPayload.swift
//
// APNs 载荷拆分的 Swift 端实现，与 payload.py 同一套语义。
// 权威来源（本轮实读全文）：
//   https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification

import Foundation

// MARK: - 键表

/// Table 1：aps 字典里 Apple 定义的键。放进 aps 的其它键会被系统忽略。
public enum ApsKey: String, CaseIterable {
    case alert, badge, sound
    case threadID = "thread-id"
    case category
    case contentAvailable = "content-available"
    case mutableContent = "mutable-content"
    case targetContentID = "target-content-id"
    case interruptionLevel = "interruption-level"
    case relevanceScore = "relevance-score"
    case filterCriteria = "filter-criteria"
    case staleDate = "stale-date"
    case contentState = "content-state"
    case timestamp, event
    case dismissalDate = "dismissal-date"
    case attributesType = "attributes-type"
    case attributes
}

/// Table 2：alert 字典的键。
public enum AlertKey: String, CaseIterable {
    case title, subtitle, body
    case launchImage = "launch-image"
    case locKey = "loc-key"
    case locArgs = "loc-args"
    case titleLocKey = "title-loc-key"
    case titleLocArgs = "title-loc-args"
    case subtitleLocKey = "subtitle-loc-key"
    case subtitleLocArgs = "subtitle-loc-args"
}

/// Table 3：critical alert 的 sound 字典键。
public enum SoundKey: String, CaseIterable {
    case critical, name, volume
}

/// interruption-level 的四个取值。
public enum InterruptionLevel: String, CaseIterable {
    case passive, active
    case timeSensitive = "time-sensitive"
    case critical
}

// MARK: - 拆分

public struct RemoteNotificationPayload {
    /// 只保留 Apple 定义的键
    public let aps: [String: Any]
    /// aps 的同级自定义键
    public let custom: [String: Any]
    /// 误放进 aps 内、会被系统忽略的键（开发期自检用）
    public let ignoredKeysInAps: [String]

    public init?(raw: [String: Any]) {
        guard let apsIn = raw["aps"] as? [String: Any] else { return nil }
        let known = Set(ApsKey.allCases.map { $0.rawValue })
        self.aps = apsIn.filter { known.contains($0.key) }
        self.ignoredKeysInAps = apsIn.keys.filter { !known.contains($0) }.sorted()
        self.custom = raw.filter { $0.key != "aps" }
    }

    public var badge: Int? { aps[ApsKey.badge.rawValue] as? Int }

    /// badge 为 0 表示清除角标。
    public var clearsBadge: Bool { badge == 0 }

    public var isBackground: Bool {
        (aps[ApsKey.contentAvailable.rawValue] as? Int) == 1
    }

    public var usesServiceExtension: Bool {
        (aps[ApsKey.mutableContent.rawValue] as? Int) == 1
    }

    public var interruptionLevel: InterruptionLevel? {
        guard let s = aps[ApsKey.interruptionLevel.rawValue] as? String else { return nil }
        return InterruptionLevel(rawValue: s)
    }
}

// MARK: - alert 归一化

public struct AlertContent {
    public let title: String?
    public let subtitle: String?
    public let body: String?
}

public enum AlertParseError: Error {
    case notStringOrDictionary
    case unknownKeys([String])
    case missingLocalizedString(String)
}

/// alert 可以是 String（等价于 body）或 Dictionary。
public func normalizeAlert(_ alert: Any) throws -> [String: Any] {
    if let s = alert as? String { return ["body": s] }
    guard let d = alert as? [String: Any] else { throw AlertParseError.notStringOrDictionary }
    let known = Set(AlertKey.allCases.map { $0.rawValue })
    let unknown = d.keys.filter { !known.contains($0) }.sorted()
    if !unknown.isEmpty { throw AlertParseError.unknownKeys(unknown) }
    return d
}

// MARK: - 本地化

/// 按 %@ 的**出现顺序**替换，而不是按下标寻址。
public func localize(template: String, args: [String]) -> String {
    var out = ""
    var idx = 0
    var i = template.startIndex
    while i < template.endIndex {
        if template[i] == "%",
           let nxt = template.index(i, offsetBy: 1, limitedBy: template.endIndex),
           nxt < template.endIndex,
           template[nxt] == "@" {
            if idx < args.count {
                out += args[idx]
                idx += 1
            } else {
                out += "%@"
            }
            i = template.index(after: nxt)
        } else {
            out.append(template[i])
            i = template.index(after: i)
        }
    }
    return out
}

/// 用 Localizable.strings 解析 loc-* 系列键。显式给的 title/subtitle/body 优先。
public func resolveAlert(_ alert: Any, strings: [String: String]) throws -> AlertContent {
    let a = try normalizeAlert(alert)
    func pick(_ plain: String, _ locKey: String, _ locArgs: String) throws -> String? {
        if let v = a[plain] as? String { return v }
        if let name = a[locKey] as? String {
            guard let tpl = strings[name] else {
                throw AlertParseError.missingLocalizedString(name)
            }
            let args = (a[locArgs] as? [String]) ?? []
            return localize(template: tpl, args: args)
        }
        return nil
    }
    return AlertContent(
        title: try pick("title", "title-loc-key", "title-loc-args"),
        subtitle: try pick("subtitle", "subtitle-loc-key", "subtitle-loc-args"),
        body: try pick("body", "loc-key", "loc-args")
    )
}
