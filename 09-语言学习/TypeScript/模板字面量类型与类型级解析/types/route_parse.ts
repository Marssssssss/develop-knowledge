// EXPECT: OK
// 类型级字符串解析：把 `:param` 从路由模板里「读」出来，以及按分隔符切分。

type Eq<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
  ? true
  : false;
type Expect<T extends true> = T;

// 1. 抽路径参数：先吃掉「中间还剩下一段」的情况，再处理结尾那一段
type ExtractParams<S extends string> = S extends `${string}:${infer P}/${infer Rest}`
  ? P | ExtractParams<Rest>
  : S extends `${string}:${infer P}`
    ? P
    : never;

type _1 = Expect<Eq<ExtractParams<"/users/:id">, "id">>;
type _2 = Expect<Eq<ExtractParams<"/users/:id/posts/:postId">, "id" | "postId">>;
type _3 = Expect<Eq<ExtractParams<"/static/home">, never>>;

// 结尾带斜杠时 Rest 是空串，递归进去得到 never —— 尾部参数仍然被认出来
type _4 = Expect<Eq<ExtractParams<"/users/:id/">, "id">>;

// 2. 按分隔符切分
type Split<S extends string, D extends string> = string extends S
  ? string[]
  : S extends ""
    ? []
    : S extends `${infer H}${D}${infer T}`
      ? [H, ...Split<T, D>]
      : [S];

type _5 = Expect<Eq<Split<"a-b-c", "-">, ["a", "b", "c"]>>;
type _6 = Expect<Eq<Split<"a", "-">, ["a"]>>;
type _7 = Expect<Eq<Split<"", "-">, []>>;
type _8 = Expect<Eq<Split<string, "-">, string[]>>;

// 3. 抽出来的结果与「该对象有哪些 key」可以到 1:1 —— 这正是运行时 handler 表想要的形状
type Only<P extends string> = ExtractParams<P>;
type Keys = keyof { id: string; postId: string };
type _9 = Expect<Eq<Only<"/users/:id/posts/:postId">, Keys>>;

export type { ExtractParams, Split };
