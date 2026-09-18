//! async/await 状态机：Future 是惰性的、poll + Waker 的契约、Pin 为什么存在。
//!
//! 依据 std::future::Future 文档原文：
//!   * "Futures alone are **inert**; they must be actively `poll`ed ..."
//!   * "only the Waker from the Context passed to the **most recent** call
//!      should be scheduled to receive a wakeup."
//!   * "Once a future has finished, clients should not poll it again."
//!
//! 编译：cargo run（纯标准库，不依赖 tokio/async-std）

use std::future::{poll_fn, Future};
use std::marker::PhantomPinned;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};
use std::thread;
use std::time::{Duration, Instant};

// ---------------------------------------------------------------- 叶子 Future

struct Shared {
    deadline: Instant,
    waker: Option<Waker>,   // 只保留最近一次 poll 传进来的那个
}

struct TimerFuture {
    shared: Arc<Mutex<Shared>>,
}

impl TimerFuture {
    fn new(after: Duration) -> Self {
        let shared = Arc::new(Mutex::new(Shared {
            deadline: Instant::now() + after,
            waker: None,
        }));
        let shared2 = Arc::clone(&shared);
        thread::spawn(move || {
            let wait = {
                let s = shared2.lock().unwrap();
                s.deadline.saturating_duration_since(Instant::now())
            };
            thread::sleep(wait);
            // 到点：唤醒最近一次登记的 waker
            if let Some(w) = shared2.lock().unwrap().waker.take() {
                w.wake();
            }
        });
        Self { shared }
    }
}

impl Future for TimerFuture {
    type Output = String;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let mut shared = self.shared.lock().unwrap();
        if Instant::now() >= shared.deadline {
            Poll::Ready("timer fired".to_string())
        } else {
            // 覆盖式登记：std 明确要求只保留最近一次 Context 的 Waker
            shared.waker = Some(cx.waker().clone());
            Poll::Pending
        }
    }
}

// ---------------------------------------------------------------- 极简执行器

struct ThreadWaker;

impl Wake for ThreadWaker {
    fn wake(self: Arc<Self>) {
        thread::current().unpark();
    }
    fn wake_by_ref(self: &Arc<Self>) {
        thread::current().unpark();
    }
}

/// `block_on`：Pending 就把线程 park 住，等 Waker 把它叫醒再 poll。
fn block_on<F: Future>(fut: F) -> F::Output {
    let mut fut = Box::pin(fut);
    let waker = Waker::from(Arc::new(ThreadWaker));
    let mut cx = Context::from_waker(&waker);
    loop {
        match fut.as_mut().poll(&mut cx) {
            Poll::Ready(v) => return v,
            Poll::Pending => thread::park(), // 唤醒后重新 poll
        }
    }
}

/// async-book 里的 `Join`：两个 future 并发推进。
/// 为可读性用了 `Box::pin`；真实的组合子是把子 future **内联**持有的（见 Python 断言 6.2）。
async fn join2<F1: Future, F2: Future>(f1: F1, f2: F2) -> (F1::Output, F2::Output) {
    let mut f1 = Box::pin(f1);
    let mut f2 = Box::pin(f2);
    let mut o1 = None;
    let mut o2 = None;
    poll_fn(move |cx| {
        // 已完成的字段不再 poll —— 官方注释："This prevents us from polling a
        // future after it has completed, which would violate the contract."
        if o1.is_none() {
            if let Poll::Ready(v) = f1.as_mut().poll(cx) {
                o1 = Some(v);
            }
        }
        if o2.is_none() {
            if let Poll::Ready(v) = f2.as_mut().poll(cx) {
                o2 = Some(v);
            }
        }
        if o1.is_some() && o2.is_some() {
            Poll::Ready((o1.take().unwrap(), o2.take().unwrap()))
        } else {
            Poll::Pending
        }
    })
    .await
}

// ---------------------------------------------------------------- 自引用与 Pin

struct SelfRef {
    a: i32,
    ptr_to_a: *const i32,
    _pin: PhantomPinned, // 摘掉自动派生的 Unpin —— 官方 std::pin 文档推荐做法
}

impl SelfRef {
    fn new(a: i32) -> Pin<Box<Self>> {
        let mut boxed = Box::pin(Self { a, ptr_to_a: std::ptr::null(), _pin: PhantomPinned });
        let ptr = &boxed.a as *const i32;
        // SAFETY: 我们没有搬动 boxed，只写一个不由 Pin 保护的字段
        unsafe {
            boxed.as_mut().get_unchecked_mut().ptr_to_a = ptr;
        }
        boxed
    }

    fn pointer_is_valid(&self) -> bool {
        std::ptr::eq(self.ptr_to_a, &self.a)
    }
}

// ---------------------------------------------------------------- main

async fn scenario() {
    // 顺序 await：两个 60ms 的定时器首尾相接
    let t0 = Instant::now();
    TimerFuture::new(Duration::from_millis(60)).await;
    TimerFuture::new(Duration::from_millis(60)).await;
    println!("2) 顺序 await 两个 60ms ≈ {:?}", t0.elapsed());

    // 并发 join：同一个 poll 循环里推进两个
    let t1 = Instant::now();
    let (a, b) = join2(
        TimerFuture::new(Duration::from_millis(60)),
        TimerFuture::new(Duration::from_millis(60)),
    )
    .await;
    println!("3) 并发 join 两个 60ms ≈ {:?}  ({a} / {b})", t1.elapsed());
}

fn main() {
    // ---- 1. 惰性：构造完不 poll 就什么都不会发生
    let fut = TimerFuture::new(Duration::from_millis(50));
    println!("1) future 已构造，尚未 poll —— Futures alone are inert");
    let out = block_on(fut);
    println!("   block_on 之后拿到: {out}");

    // ---- 2/3. async 场景
    block_on(scenario());

    // ---- 4. async block 本身也是一个 Future（同样惰性）
    let lazy = async { 1 + 1 };
    println!("4) async block 构造出来时不执行，await 才跑: {}", block_on(lazy));

    // ---- 5. 自引用 future 必须被 Pin 住
    let sr = SelfRef::new(42);
    println!("5) 被 Pin 住的自引用指针有效: {}", sr.pointer_is_valid());
    // let moved = *sr;  // 编译不过：Pin<Box<SelfRef>> 拿不出所有权
    // 而 SelfRef 因为含 PhantomPinned，不再是 Unpin —— 这正是能安全持有自引用的前提
}
