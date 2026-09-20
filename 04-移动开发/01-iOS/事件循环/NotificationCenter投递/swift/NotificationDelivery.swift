// NotificationCenter 投递语义:与 python/notification_center.py 同题(人工审查用)。
// 重点:queue 为 nil 时 block 在**投递线程**上同步跑,以及 observer token 的生命周期。

import Foundation

final class Listener {
    private var token: NSObjectProtocol?
    private var oneShot: NSObjectProtocol?
    private let queue = OperationQueue()
    private(set) var log: [String] = []

    // MARK: - 1. queue = nil:在投递线程上同步执行

    func observeSync() {
        token = NotificationCenter.default.addObserver(
            forName: .localeDidChange,
            object: nil,
            queue: nil                      // ← nil:不在队列里排,当场在投递线程跑
        ) { [weak self] note in
            // block 里用 weak self:中心强持有这个 block,不用 weak 就放不掉 self
            guard let self else { return }
            self.log.append("sync:\(note.name.rawValue)")
        }
    }

    // MARK: - 2. queue 非 nil:排进 OperationQueue,post 返回时不执行

    func observeAsync() {
        queue.maxConcurrentOperationCount = 1     // 串行队列,保证顺序
        token = NotificationCenter.default.addObserver(
            forName: .localeDidChange,
            object: nil,
            queue: queue
        ) { [weak self] note in
            self?.log.append("async:\(note.name.rawValue)")
        }
    }

    // MARK: - 3. 一次性通知:在 block 里把自己摘掉

    func observeOnce() {
        var box: NSObjectProtocol?
        box = NotificationCenter.default.addObserver(
            forName: .localeDidChange, object: nil, queue: nil
        ) { [weak self] note in
            guard let self else { return }
            self.log.append("once:\(note.name.rawValue)")
            if let box { NotificationCenter.default.removeObserver(box) }
        }
        oneShot = box
    }

    // MARK: - 4. 按 name + object 精确注销

    func removePrecisely() {
        if let token {
            NotificationCenter.default.removeObserver(token)
            self.token = nil
        }
    }

    // 必须在 self 被释放**之前**注销
    deinit {
        if let token { NotificationCenter.default.removeObserver(token) }
        if let oneShot { NotificationCenter.default.removeObserver(oneShot) }
    }
}

// MARK: - 5. 自建中心:只在单个进程内投递

enum Scoped {
    static let center = NotificationCenter()
}

func postScoped() {
    // default 中心收不到这里发的通知,反之亦然
    Scoped.center.post(name: Notification.Name("scoped.event"), object: nil)
}

// MARK: - 6. Swift 原生消息 API(Swift 6 起)

struct LocaleChanged: MainActorMessage {
    typealias Subject = Locale
    static var name: Notification.Name { .localeDidChange }
}

@MainActor
func observeTyped() async {
    // addObserver(of:for:using:) 提供强类型 + 正确的 actor 隔离
    let handle = NotificationCenter.default.addObserver(of: Locale.self, for: .localeDidChange) { _ in
        print("locale changed")
    }
    NotificationCenter.default.removeObserver(handle)
}
