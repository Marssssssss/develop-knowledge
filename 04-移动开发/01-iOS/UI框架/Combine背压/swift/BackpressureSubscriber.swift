// Combine 背压:与 python/combine_backpressure.py 同题的 Swift 侧实现(人工审查用)。
// 核心:publisher 不会自己往外吐元素,只有 subscriber 索要之后才吐,而且要多少吐多少。

import Combine
import Foundation

// MARK: - 1. 自定义 Subscriber:自己决定每轮要多少

final class IntSubscriber: Subscriber {
    typealias Input = Int
    typealias Failure = Never

    private(set) var received: [Int] = []
    private(set) var completions: [Subscribers.Completion<Never>] = []
    private var subscription: Subscription?
    private let initial: Subscribers.Demand
    private let perElement: Subscribers.Demand

    init(initial: Subscribers.Demand, perElement: Subscribers.Demand = .none) {
        self.initial = initial
        self.perElement = perElement
    }

    func receive(subscription: Subscription) {
        self.subscription = subscription
        subscription.request(initial)          // 想一次要几个就在这儿说
    }

    func receive(_ input: Int) -> Subscribers.Demand {
        received.append(input)
        return perElement                      // 也可以在这儿续要(累加到欠量上)
    }

    func receive(completion: Subscribers.Completion<Never>) {
        completions.append(completion)
    }

    func ask(_ demand: Subscribers.Demand) {
        subscription?.request(demand)
    }
}

func demonstrateDemand() {
    let subject = PassthroughSubject<Int, Never>()

    // 要 2 个:只会收到 2 个,第 3 个之后就停在那儿,而且不会发 finished
    let two = IntSubscriber(initial: .max(2))
    subject.subscribe(two)
    subject.send(1); subject.send(2); subject.send(3); subject.send(4)
    // two.received == [1, 2]
    // two.completions == []     ← publisher 只是在等欠量,没有「结束」

    // 补要 2 个:接着发第 3、4 个
    two.ask(.max(2))
    // two.received == [1, 2, 3, 4]
}

// MARK: - 2. 欠量是累加的,而且只有发出元素才会减少

func demonstrateAccumulation() {
    // .max(2) 之后再 .max(3) → 欠量 5
    let d: Subscribers.Demand = .max(2)
    let sum = d + .max(3)          // == .max(5)
    // 发一个元素减少 1;subscriber 不能请求负值
    // .unlimited + 任何值 == .unlimited
    _ = sum
    _ = Subscribers.Demand.unlimited + .max(3)   // 仍是 .unlimited
}

// MARK: - 3. sink / assign 一上手就要 unlimited

func demonstrateSink() {
    let subject = PassthroughSubject<Int, Never>()
    var got: [Int] = []
    let cancellable = subject.sink { got.append($0) }
    subject.send(1); subject.send(2); subject.send(3)
    // got == [1, 2, 3] —— sink 请求的是 .unlimited,之后不再有任何协商
    // 文档提醒:闭包里不要阻塞 publisher、不要自己缓冲、不要被压垮
    _ = cancellable
}

// MARK: - 4. TimerPublisher 之类的自动来源也只在有欠量时产出

func demonstrateTimer() {
    let timerPub = Timer.publish(every: 1, on: .main, in: .default).autoconnect()

    // 先订阅但一个都不索要:publish 存在、subscriber 存在,但一个元素都不产
    let lazy = IntSubscriber(initial: .none)
    timerPub.subscribe(lazy)       // 5 秒内 lazy.received 恒为空

    // 5 秒后再要 3 个:才开始产出,产出 3 个后又停下
    DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
        lazy.ask(.max(3))
    }
}

// MARK: - 5. 不写自定义 Subscriber 也能做背压:用算子

func demonstrateOperators() {
    let subject = PassthroughSubject<Int, Never>()
    var sink: AnyCancellable?

    sink = subject
        // 攒够 3 个才往下发一个数组 —— 相当于把「一次处理一个」变成「一次处理一批」
        .collect(3)
        // 缓冲 5 个,满了按策略丢(或报错)
        .buffer(size: 5, prefetch: .keepFull, whenFull: .dropNewest)
        // 限速:每个区间只放最新的那个过去
        .throttle(for: .milliseconds(200), scheduler: DispatchQueue.main, latest: true)
        // 上游停 100ms 才发一次 —— 适合搜索框
        .debounce(for: .milliseconds(100), scheduler: DispatchQueue.main)
        .sink { print("batch:", $0) }
    _ = sink
}

// MARK: - 6. flatMap(maxPublishers:) 限制的是并发订阅数

func demonstrateFlatMap() {
    let subject = PassthroughSubject<Int, Never>()
    let transform: (Int) -> AnyPublisher<Int, Never> = { value in
        Just(value).append(Just(value * 10)).eraseToAnyPublisher()
    }
    // 在飞 inner 最多 2 个;腾出位置后才向上游要下一个
    _ = subject.flatMap(maxPublishers: .max(2), transform).sink { print($0) }
}
