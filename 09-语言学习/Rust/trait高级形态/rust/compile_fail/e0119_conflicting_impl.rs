// E0119：关联类型版 trait 对同一类型只能实现一次；
// The Book 的对照是「泛型参数版可以对同一类型实现多次」。
trait IteratorAssoc {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
}

struct Counter;

impl IteratorAssoc for Counter {
    type Item = u32;
    fn next(&mut self) -> Option<u32> { Some(0) }
}

impl IteratorAssoc for Counter {
    // ERROR: conflicting implementations of trait `IteratorAssoc` for type `Counter`
    type Item = i64;
    fn next(&mut self) -> Option<i64> { Some(0) }
}

fn main() {
    let mut c = Counter;
    let _ = c.next();
}
