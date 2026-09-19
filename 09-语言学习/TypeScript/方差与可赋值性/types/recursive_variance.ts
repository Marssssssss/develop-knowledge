// EXPECT: TS2322
// 方差推断遇上环时会失手：单向本该拒绝的赋值悄悄通过了。
// 标注 in out 之后（见 recursive_variance_fixed.ts）同一行才会报错。
type Foo<T> = {
  x: T;
  f: Bar<T>;
};
type Bar<U> = (x: Baz<U[]>) => void;
type Baz<V> = {
  value: Foo<V[]>;
};

declare let fooUnknown: Foo<unknown>;
declare let fooString: Foo<string>;

fooUnknown = fooString; // 这里本该报错却没有——方差推断在环里给不出答案
fooString = fooUnknown; // TS2322：反方向倒是报错了

export { fooUnknown, fooString };
