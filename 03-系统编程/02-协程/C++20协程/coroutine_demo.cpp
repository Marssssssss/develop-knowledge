// coroutine_demo.cpp —— C++20 无栈协程的最小骨架
//
// 依据 cppreference《Coroutines》：
//   * *"Coroutines are stackless: they suspend execution by returning to the caller, and the
//     data that is required to resume execution is stored separately from the stack."*
//   * 协程状态是动态分配的（除非分配被优化掉），含 promise、参数副本、挂起点、局部变量；
//     按引用传入的参数保持为引用 —— *"may become dangling, if the coroutine is resumed after
//     the lifetime of referred object ends"*
//   * `co_await`：await_ready() 为 false 才调 await_suspend(handle)：
//       返回 void / true → 控制回到调用者；false → 立即恢复；
//       返回 coroutine_handle → 恢复那个协程（对称转移，可链式）
//     await_resume() 无论如何都会被调用，其值就是整个表达式的值。
//   * `co_return`：销毁局部变量（逆序）→ co_await promise.final_suspend()；
//     *"It's undefined behavior to resume a coroutine from this point."*
//
// 编译：g++ -std=c++20 -fcoroutines coroutine_demo.cpp -o coroutine_demo
#include <coroutine>
#include <exception>
#include <iostream>
#include <utility>

// ---------- 1. 生成器：final_suspend 必须挂起，否则一 co_return 状态就没了 ----------
template <typename T>
struct Generator {
    struct promise_type {
        T value_{};
        std::suspend_always initial_suspend() noexcept { return {}; }   // 惰性启动
        std::suspend_always final_suspend() noexcept { return {}; }     // 挂起，等外部 destroy
        std::suspend_always yield_value(T v) noexcept {                  // co_yield
            value_ = std::move(v);
            return {};
        }
        void return_void() noexcept {}
        void unhandled_exception() { std::terminate(); }
        Generator get_return_object() {
            return Generator{std::coroutine_handle<promise_type>::from_promise(*this)};
        }
    };

    std::coroutine_handle<promise_type> h_;
    explicit Generator(std::coroutine_handle<promise_type> h) : h_(h) {}
    Generator(const Generator&) = delete;
    Generator(Generator&& o) noexcept : h_(std::exchange(o.h_, {})) {}
    ~Generator() { if (h_) h_.destroy(); }        // 不 destroy 就是泄漏

    bool next() { return h_ && !h_.done() && (h_.resume(), !h_.done()); }
    T value() const { return h_.promise().value_; }
};

Generator<int> counter(int n) {
    for (int i = 0; i < n; ++i)
        co_yield i;                                // 每 yield 一次把控制权还给调用者
}

// ---------- 2. task：await_suspend 返回句柄 = 对称转移（栈深不增长） ----------
struct Task {
    struct promise_type {
        std::coroutine_handle<> continuation_{};
        std::suspend_always initial_suspend() noexcept { return {}; }
        std::suspend_always final_suspend() noexcept { return {}; }
        void return_void() noexcept {}
        void unhandled_exception() { std::terminate(); }
        Task get_return_object() {
            return Task{std::coroutine_handle<promise_type>::from_promise(*this)};
        }
    };
    std::coroutine_handle<promise_type> h_;
    explicit Task(std::coroutine_handle<promise_type> h) : h_(h) {}
    Task(const Task&) = delete;
    Task(Task&& o) noexcept : h_(std::exchange(o.h_, {})) {}
    ~Task() { if (h_) h_.destroy(); }

    // 作为 awaiter 三件套
    bool await_ready() const noexcept { return false; }
    // 返回 void：把控制权交回调用者，等别人来 resume 本协程
    void await_suspend(std::coroutine_handle<> caller) noexcept {
        h_.promise().continuation_ = caller;      // 记下「谁在等我」
    }
    void await_resume() const noexcept {}
    void run() { h_.resume(); }
};

// ---------- 3. await_ready 的短路：结果已就绪就一次都不挂起 ----------
struct Ready {
    int v;
    bool await_ready() const noexcept { return true; }        // 短路
    void await_suspend(std::coroutine_handle<>) noexcept {}   // 不会被调用
    int await_resume() const noexcept { return v; }           // 返回值就是 co_await 的值
};

Task use_ready() {
    int x = co_await Ready{42};                   // 同步完成，零挂起
    std::cout << "co_await Ready -> " << x << "\n";
    co_return;
}

// ---------- 4. 按引用传参：协程里只留引用，对端一走就悬垂 ----------
Generator<int> by_ref_demo(const int& n) {        // 危险：n 是引用，不是副本
    for (int i = 0; i < n; ++i)
        co_yield i;
    // 若调用方那个 int 已销毁，这里就是用后释放
}

int main() {
    auto g = counter(3);
    while (g.next())
        std::cout << "yield " << g.value() << "\n";

    auto t = use_ready();
    t.run();

    auto bad = by_ref_demo(*std::make_unique<int>(2).release());  // 仅作演示：实参临时量已销毁
    std::cout << "by_ref 演示结束（真实代码里这里已是 UB）\n";
    return 0;
}
