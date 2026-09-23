// EXPECT: OK
// 模板字面量类型的四条基本规则：拼接 / 交叉相乘 / 内建字符串操作 / 非 locale 敏感。

type Eq<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
  ? true
  : false;
type Expect<T extends true> = T;

// 1. 具体字面量拼接出一个新的字符串字面量类型
type World = "world";
type Greeting = `hello ${World}`;
type _1 = Expect<Eq<Greeting, "hello world">>;

// 2. 插值位置里放联合 ⇒ 笛卡尔积展开
type EmailLocaleIDs = "welcome_email" | "email_heading";
type FooterLocaleIDs = "footer_title" | "footer_sendoff";
type AllLocaleIDs = `${EmailLocaleIDs | FooterLocaleIDs}_id`;
type _2 = Expect<
  Eq<
    AllLocaleIDs,
    "welcome_email_id" | "email_heading_id" | "footer_title_id" | "footer_sendoff_id"
  >
>;

// 3. 多个插值位一起乘
type Lang = "en" | "ja" | "pt";
type Perm = `${Lowercase<Lang>}${"-x" | "-y"}`;
type _3 = Expect<Eq<Perm, "en-x" | "en-y" | "ja-x" | "ja-y" | "pt-x" | "pt-y">>;

// 4. 内建字符串操作类型（intrinsic：编译器内实现，不在 .d.ts 里）
type _4 = Expect<Eq<Uppercase<"my_app">, "MY_APP">>;
type _5 = Expect<Eq<Lowercase<"MY_APP">, "my_app">>;
type _6 = Expect<Eq<Capitalize<"hello, world">, "Hello, world">>;
type _7 = Expect<Eq<Uncapitalize<"HELLO WORLD">, "hELLO WORLD">>;

// 5. intrinsic 与模板字面量组合出一个 cache key
type ASCIICacheKey<Str extends string> = `ID-${Uppercase<Str>}`;
type _8 = Expect<Eq<ASCIICacheKey<"my_app">, "ID-MY_APP">>;

// 6. intrinsic 直接吃 JS 的 String.prototype 实现 ⇒ 与 toUpperCase() 同源，
//    同样不区分 locale（详见 README 里 applyStringMapping 的源码引用）
type _9 = Expect<Eq<Uppercase<"ß">, "SS">>;

export type { Greeting, AllLocaleIDs, Perm, ASCIICacheKey };
