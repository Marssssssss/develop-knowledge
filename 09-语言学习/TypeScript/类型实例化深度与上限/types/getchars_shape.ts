// EXPECT: OK
// 同一个「把字符串拆成字符集合」的需求，两种写法：
//   - GetChars 非尾递归：拿到内层结果后还要参与 `Char | …` 的联合 ⇒ 必须保留中间状态
//   - GetCharsHelper 尾递归：把累积结果放进 Acc 参数，尾调用那一支一路往下走
// 短字符串上两者结果完全一致 —— 本 fixture 的重点是「结果相同，代价不同」。

type Eq<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
  ? true
  : false;
type Expect<T extends true> = T;

type GetChars<S> = S extends `${infer Char}${infer Rest}`
  ? Char | GetChars<Rest>
  : never;

type GetCharsOf<S> = GetCharsHelper<S, never>;
type GetCharsHelper<S, Acc> = S extends `${infer Char}${infer Rest}`
  ? GetCharsHelper<Rest, Char | Acc>
  : Acc;

// 结果严格相等：两种实现方式类型层面等价
type _1 = Expect<Eq<GetChars<"abc">, GetCharsOf<"abc">>>;
type _2 = Expect<Eq<GetCharsOf<"abc">, "a" | "b" | "c">>;

// 空字符串两端都是 never（非尾版最后是 never，尾版的 Acc 初值也是 never）
type _3 = Expect<Eq<GetChars<"">, never>>;
type _4 = Expect<Eq<GetCharsOf<"">, never>>;

export type { GetChars, GetCharsOf };
