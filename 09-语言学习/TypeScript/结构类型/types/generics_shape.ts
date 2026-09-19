// EXPECT: TS2322
// 结构系统里"泛型参数只在成员位置被消费时才影响兼容性"。
interface Empty<T> {}
declare let e1: Empty<number>;
declare let e2: Empty<string>;
e1 = e2; // OK:T 没有出现在任何成员里,两个结构完全相同
e2 = e1; // OK

interface NotEmpty<T> {
  data: T;
}
declare let n1: NotEmpty<number>;
declare let n2: NotEmpty<string>;
n1 = n2; // TS2322:data 的结构不同
n2 = n1; // TS2322

export { e1, e2, n1, n2 };
