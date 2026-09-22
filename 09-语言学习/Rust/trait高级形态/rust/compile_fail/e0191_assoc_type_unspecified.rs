// E0191：写 trait object 类型时，泛型参数与关联类型都必须写全。
trait Iter {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
}

struct Counter;
impl Iter for Counter {
    type Item = u32;
    fn next(&mut self) -> Option<u32> { Some(1) }
}

fn take(it: &mut dyn Iter) {
    // ERROR: the value of the associated type `Item` must be specified
    while let Some(v) = it.next() {
        println!("{v}");
    }
}

fn main() {
    let mut c = Counter;
    take(&mut c);
    // 正确写法：`&mut dyn Iter<Item = u32>`
}
