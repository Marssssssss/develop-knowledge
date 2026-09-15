//! 智能指针与内部可变性（Rust demo 4/5）
//!
//! 四个标准库类型的可观测行为，全部用**断言**锁定：
//!   Box<T>       —— 堆分配 + 间接层，让递归类型有确定大小
//!   Deref/DerefMut —— deref coercion（引用链自动转换，编译期解析、零运行期开销）
//!   Rc<T>        —— 强引用计数（单线程共享所有权）
//!   RefCell<T>   —— 把借用规则从编译期搬到运行期（违反则 panic，不 UB）
//!   Weak<T>      —— 把「父←子」反向边变弱，打破引用环
//!
//! 依据：The Rust Programming Language ch15-01 / ch15-02 / ch15-04 / ch15-05 / ch15-06，
//! std 文档 `core::ops::Deref`（deref coercion 三条规则）。

use std::cell::RefCell;
use std::mem::size_of;
use std::rc::{Rc, Weak};

// ---------------------------------------------------------------------------
// Box：递归类型必须插一层间接（指针大小固定，编译器才算得出类型大小）
// ---------------------------------------------------------------------------
#[derive(Debug)]
enum ListBox {
    Cons(i32, Box<ListBox>),
    Nil,
}

/// 自定义智能指针：实现 Deref 后就能像引用一样用
struct MyBox<T>(T);

impl<T> MyBox<T> {
    fn new(x: T) -> MyBox<T> {
        MyBox(x)
    }
}

impl<T> std::ops::Deref for MyBox<T> {
    type Target = T;
    fn deref(&self) -> &T {
        &self.0
    }
}

fn hello(name: &str) {
    println!("      hello, {name}!");
}

// ---------------------------------------------------------------------------
// RefCell 运行期借用规则（用 try_borrow / try_borrow_mut 把判定结果取出来看）
// ---------------------------------------------------------------------------
fn refcell_demo() {
    let c = RefCell::new(5);

    let b1 = c.borrow();
    let b2 = c.borrow();
    println!("  已有 {n} 个活动的 Ref（共享借用）", n = 2);
    let err = c.try_borrow_mut().unwrap_err();
    println!("    此时 try_borrow_mut() 失败：{err}（运行期检查，而非编译期）");
    drop(b1);
    drop(b2);
    println!("    释放后 try_borrow_mut() 成功 = {}", c.try_borrow_mut().is_ok());

    {
        let m = c.borrow_mut();
        println!(
            "    持有 RefMut 期间：try_borrow() 失败 = {}，try_borrow_mut() 失败 = {}",
            c.try_borrow().is_err(),
            c.try_borrow_mut().is_err()
        );
        drop(m);
    }
    println!("    释放后 try_borrow() 成功 = {}", c.try_borrow().is_ok());

    {
        let mut m = c.borrow_mut();
        *m += 10;
    }
    println!("    通过 RefMut 修改后 value = {}", c.borrow());

    // 直接违反规则 → panic（不是 UB）；catch_unwind 证明进程可控地拦下了它
    let caught = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let d = RefCell::new(1);
        let _m1 = d.borrow_mut();
        let _m2 = d.borrow_mut(); // BorrowMutError
    }));
    println!("    双重 borrow_mut() 触发 panic 并被 catch_unwind 拦下 = {}", caught.is_err());
    assert!(caught.is_err());
}

// ---------------------------------------------------------------------------
// Rc<RefCell<T>>：多所有者 + 可改
// ---------------------------------------------------------------------------
#[derive(Debug)]
enum ListRc {
    Cons(Rc<RefCell<i32>>, Rc<ListRc>),
    Nil,
}

// ---------------------------------------------------------------------------
// 引用环：strong_count 永不归零 → 泄漏（内存安全，但内存不回收）
// ---------------------------------------------------------------------------
struct Cyc {
    name: &'static str,
    next: RefCell<Option<Rc<Cyc>>>,
}

// ---------------------------------------------------------------------------
// Weak：父子双向（父持子 Rc、子持父 Weak）不构成环
// ---------------------------------------------------------------------------
struct Node {
    value: i32,
    parent: RefCell<Weak<Node>>,
    children: RefCell<Vec<Rc<Node>>>,
}

fn main() {
    println!("=== 1. Box<T>：为递归类型提供确定大小 ===");
    let list = ListBox::Cons(1, Box::new(ListBox::Cons(2, Box::new(ListBox::Cons(3, Box::new(ListBox::Nil))))));
    println!("  list = {list:?}");
    println!("  size_of::<Box<ListBox>>() = {} 字节（等于一个指针）", size_of::<Box<ListBox>>());
    println!("  size_of::<ListBox>()       = {} 字节（有限 → 编译器算得出）", size_of::<ListBox>());
    assert_eq!(size_of::<Box<ListBox>>(), size_of::<usize>());
    assert!(size_of::<ListBox>() < 64, "递归类型经 Box 打断后应有确定大小");

    // 大数据转移所有权时不复制堆数据：堆地址不变
    let big = Box::new([7_u8; 65536]);
    let addr_before = &*big as *const [u8; 65536] as usize;
    let big2 = big; // move
    let addr_after = &*big2 as *const [u8; 65536] as usize;
    println!("  64 KiB 数组 move 前后堆地址 0x{addr_before:x} → 0x{addr_after:x}（相同：只搬了指针）");
    assert_eq!(addr_before, addr_after);

    println!("\n=== 2. Deref coercion：&MyBox<String> → &String → &str ===");
    let m = MyBox::new(String::from("Rust"));
    hello(&m); // 编译器自动插入 2 次 Deref::deref（查表在编译期完成，无运行期代价）
    hello(&(*m)[..]); // 没有 deref coercion 时要手写的等价形式
    assert_eq!(&*m as &str, "Rust");

    println!("\n=== 3. Rc<T>：强引用计数 ===");
    let a = Rc::new(ListRc::Cons(Rc::new(RefCell::new(5)), Rc::new(ListRc::Nil)));
    let mut counts = vec![Rc::strong_count(&a)];
    let _b = ListRc::Cons(Rc::new(RefCell::new(3)), Rc::clone(&a));
    counts.push(Rc::strong_count(&a));
    {
        let _c = ListRc::Cons(Rc::new(RefCell::new(4)), Rc::clone(&a));
        counts.push(Rc::strong_count(&a));
    }
    counts.push(Rc::strong_count(&a));
    println!("  strong_count 轨迹 = {counts:?}（Book Listing 15-19 的输出为 [1, 2, 3, 2]）");
    assert_eq!(counts, vec![1, 2, 3, 2]);
    println!("  weak_count（此处无 Weak）= {}", Rc::weak_count(&a));

    println!("\n=== 4. RefCell<T>：借用规则搬到运行期 ===");
    refcell_demo();

    println!("\n=== 5. Rc<RefCell<i32>>：多所有者 + 内部可变 ===");
    let value = Rc::new(RefCell::new(5));
    let ra = Rc::new(ListRc::Cons(Rc::clone(&value), Rc::new(ListRc::Nil)));
    let rb = ListRc::Cons(Rc::new(RefCell::new(3)), Rc::clone(&ra));
    let rc = ListRc::Cons(Rc::new(RefCell::new(4)), Rc::clone(&ra));
    *value.borrow_mut() += 10;
    let (sa, sb, sc) = (format!("{ra:?}"), format!("{rb:?}"), format!("{rc:?}"));
    println!("  a after = {sa}");
    println!("  b after = {sb}");
    println!("  c after = {sc}");
    // Book Listing 15-24 给出的期望输出
    assert_eq!(sa, "Cons(RefCell { value: 15 }, Nil)");
    assert_eq!(sb, "Cons(RefCell { value: 3 }, Cons(RefCell { value: 15 }, Nil))");
    assert_eq!(sc, "Cons(RefCell { value: 4 }, Cons(RefCell { value: 15 }, Nil))");

    println!("\n=== 6. 引用环 → 泄漏（strong_count 不归零）===");
    let x = Rc::new(Cyc { name: "x", next: RefCell::new(None) });
    let y = Rc::new(Cyc { name: "y", next: RefCell::new(None) });
    *x.next.borrow_mut() = Some(Rc::clone(&y));
    *y.next.borrow_mut() = Some(Rc::clone(&x));
    let (wx, wy) = (Rc::downgrade(&x), Rc::downgrade(&y));
    println!("  成环后 strong: x={} y={}", Rc::strong_count(&x), Rc::strong_count(&y));
    drop(x);
    drop(y);
    println!(
        "  变量都离开作用域后 strong: x={} y={} → 永不归零",
        wx.strong_count(),
        wy.strong_count()
    );
    println!("  Weak::upgrade() 仍成功 = {}（内存泄漏，但仍是内存安全的）", wx.upgrade().is_some());
    assert_eq!(wx.strong_count(), 1);
    assert_eq!(wy.strong_count(), 1);

    println!("\n=== 7. Weak<T>：父子双向不成环 ===");
    let leaf = Rc::new(Node {
        value: 3,
        parent: RefCell::new(Weak::new()),
        children: RefCell::new(vec![]),
    });
    println!("  leaf 建立后        strong={} weak={}", Rc::strong_count(&leaf), Rc::weak_count(&leaf));
    assert_eq!(Rc::strong_count(&leaf), 1);
    println!("  leaf.parent.upgrade() = {}", leaf.parent.borrow().upgrade().is_some());

    {
        let branch = Rc::new(Node {
            value: 5,
            parent: RefCell::new(Weak::new()),
            children: RefCell::new(vec![Rc::clone(&leaf)]),
        });
        *leaf.parent.borrow_mut() = Rc::downgrade(&branch);
        println!(
            "  branch 建立并挂上后 branch: strong={} weak={} / leaf: strong={} weak={}",
            Rc::strong_count(&branch),
            Rc::weak_count(&branch),
            Rc::strong_count(&leaf),
            Rc::weak_count(&leaf)
        );
        assert_eq!(Rc::strong_count(&branch), 1);
        assert_eq!(Rc::weak_count(&branch), 1);
        assert_eq!(Rc::strong_count(&leaf), 2);
        assert_eq!(Rc::weak_count(&leaf), 0);
        let up = leaf.parent.borrow().upgrade();
        println!("  leaf.parent.upgrade() → Some(branch.value={})", up.as_ref().unwrap().value);
        assert_eq!(up.unwrap().value, 5);
    } // branch 离开作用域：strong 归 0 → 被 drop
    println!(
        "  branch 离开作用域后 leaf: strong={} weak={}（weak 计数不影响释放）",
        Rc::strong_count(&leaf),
        Rc::weak_count(&leaf)
    );
    assert_eq!(Rc::strong_count(&leaf), 1);
    assert!(leaf.parent.borrow().upgrade().is_none(), "父节点已 drop → upgrade 返回 None");

    println!("\n断言全部通过（Rc 计数轨迹、RefCell panic、引用环泄漏、Weak 破环）。");
}
