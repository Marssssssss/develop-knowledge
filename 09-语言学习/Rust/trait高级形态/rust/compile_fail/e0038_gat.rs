// E0038：关联类型**带泛型参数**就是 GAT，会让 trait 失去 dyn 兼容。
// （不带泛型的关联类型不受影响，`dyn Iterator<Item = u32>` 是合法的。）
trait Lend {
    type Lender<'a> where Self: 'a;
    fn lend<'a>(&'a mut self) -> Self::Lender<'a>;
}

struct ArrayLender<'a, T>(&'a mut [T; 16]);

impl<T> Lend for [T; 16] {
    type Lender<'a> = ArrayLender<'a, T> where Self: 'a;
    fn lend<'a>(&'a mut self) -> ArrayLender<'a, T> { ArrayLender(self) }
}

fn main() {
    let mut a = [0usize; 16];
    let _: Box<dyn Lend> = Box::new(a); // ERROR: the trait cannot be made into an object
}
