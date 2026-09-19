// EXPECT: TS2322
// 与 recursive_variance.ts 同构，仅给 Foo 的 T 加上 <in out>：失手的那一侧也被拦住了。
type Foo<in out T> = {
  x: T;
  f: Bar<T>;
};
type Bar<U> = (x: Baz<U[]>) => void;
type Baz<V> = {
  value: Foo<V[]>;
};

declare let fooUnknown: Foo<unknown>;
declare let fooString: Foo<string>;

fooUnknown = fooString; // TS2322：显式不变标注补齐了推断缺口
fooString = fooUnknown; // TS2322

export { fooUnknown, fooString };
