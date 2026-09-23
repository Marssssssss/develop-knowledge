// EXPECT: TS2589
// 无限嵌套的类型拉开 Unpack：`T extends { item: infer U } ? Unpack<U> : T` 每解开一层就
// 递归一次，而 InfiniteBox 每一层都还能再解 ⇒ 永不收敛，由深度护栏兜住。
// 例子取自 TypeScript 4.5 发布说明「Tail-Recursion Elimination on Conditional Types」。

type InfiniteBox<T> = { item: InfiniteBox<T> };
type Unpack<T> = T extends { item: infer U } ? Unpack<U> : T;

// ❌ TS2589 Type instantiation is excessively deep and possibly infinite.
type Test = Unpack<InfiniteBox<number>>;

// 同样的「解嵌套」换成有终点的数据就正常：元组一直拆到 never
type Unwrap<T> = T extends [infer H, ...infer R] ? [H, ...Unwrap<R>] : [];
type Fine = Unwrap<[1, 2, 3]>;

export type { Test, Fine };
