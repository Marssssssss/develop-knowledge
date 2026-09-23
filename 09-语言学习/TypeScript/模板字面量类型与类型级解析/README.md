# 模板字面量类型与类型级解析

> TypeScript 第二批 `646`。
> 模板字面量类型把「字符串」变成了**可计算的值**：插值是拼接、联合是笛卡尔积、
> `infer` 是模式匹配。这一篇讲清四条规则，并把「类型级解析器」做到能真的用。

## 一、四条基本规则

### 1. 具体字面量 ⇒ 直接拼接

```ts
type World = "world";
type Greeting = `hello ${World}`;   // "hello world"
```

### 2. 插值位是联合 ⇒ 交叉相乘（笛卡尔积）

```ts
type EmailLocaleIDs = "welcome_email" | "email_heading";
type FooterLocaleIDs = "footer_title" | "footer_sendoff";
type AllLocaleIDs = `${EmailLocaleIDs | FooterLocaleIDs}_id`;
// 4 个成员：welcome_email_id | email_heading_id | footer_title_id | footer_sendoff_id
```

多个插值位**一起乘**：2 × 3 = 6（`Perm` 用例）。这是组合爆炸的源头，
也是 `TS2590` 那类错误的来源（见同批 `类型实例化深度与上限/`）。

### 3. 四个内建字符串操作类型（intrinsic）

`Uppercase` / `Lowercase` / `Capitalize` / `Uncapitalize`。它们**不在任何 `.d.ts` 里**，
由编译器直接实现。官方页面给出的实现源码是：

```ts
function applyStringMapping(symbol: Symbol, str: string) {
  switch (intrinsicTypeKinds.get(symbol.escapedName as string)) {
    case IntrinsicTypeKind.Uppercase:     return str.toUpperCase();
    case IntrinsicTypeKind.Lowercase:     return str.toLowerCase();
    case IntrinsicTypeKind.Capitalize:    return str.charAt(0).toUpperCase() + str.slice(1);
    case IntrinsicTypeKind.Uncapitalize:  return str.charAt(0).toLowerCase() + str.slice(1);
  }
  return str;
}
```

两个可直接推出来的结论，本 demo 都做成了断言：

- `Uppercase<"ß">` 的结果是 **`"SS"`**（不是 `"ß"`）—— 因为 `toUpperCase()` 会把 ſ/ß 扩成长两位；
- 这条实现**不是 locale 感知**的：`"i".toUpperCase()` 恒为 `"I"`，而 `toLocaleUpperCase("tr")` 给 `"İ"`。
  类型里的 `Uppercase<"i">` 只有前者那一种结果。

### 4. 模式匹配：`infer` + 模板

```ts
type ExtractParams<S extends string> =
  S extends `${string}:${infer P}/${infer Rest}` ? P | ExtractParams<Rest>
  : S extends `${string}:${infer P}`             ? P
  : never;
```

匹配是**从左到右、尽可能短**的：第一个 `${infer P}` 在遇到第一个 `/` 之前就停下来了，
所以 `:id/posts/:postId` 会被切成 `P = "id"`、`Rest = "posts/:postId"`，递归下去得到 `"id" | "postId"`。

> 注意顺序：必须**先**处理「后面还有一段」的分支（`…/${infer Rest}`），再处理结尾分支。
> 反过来写会在第一个的成功匹配上就返回，永远拿不到第二个参数。

## 二、把 overlay 拧回来的例子：`${Key}Changed`

```ts
type PropEventSource<Type> = {
  on<Key extends string & keyof Type>(
    eventName: `${Key}Changed`,
    callback: (newValue: Type[Key]) => void
  ): void;
};
```

要点是 **`${Key}Changed` 里的 `Key` 可以被反向推断出来**：调用 `person.on("firstNameChanged", …)`
时 TS 把 `Key` 匹配成 `"firstName"`，再用索引访问 `Type[Key]` 得到回调参数类型 `string`。
`string & keyof Type` 这一步是必要的 —— `keyof` 的结果可能含 `symbol`/`number`，必须先收窄到 string。

实测（`types/event_source.ts`，`tsc --strict`）：

- `person.on("firstName", …)` → `TS2345`（少了 `Changed` 后缀）
- `person.on("frstNameChanged", …)` → `TS2345`（拼错，不在合法联合里）

## 三、类型等价怎么断言

类型层的结论没法 `console.log`，本目录统一用这一对：

```ts
type Eq<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
  ? true : false;
type Expect<T extends true> = T;          // 不等就报 TS2344
```

`(<T>() => T extends A ? 1 : 2)` 这种写法刻意保留 `<T>` 的**同一性**（identity），
比 `A extends B ? true : false` 严格得多，能区分 `any`、区分联合成员顺序。

**必须有负控**：`selfcheck.py` 里额外生成一份探针文件，让 `Eq` 只少一个联合成员，
确认能得到 `TS2344`。否则整套类型断言可能全是恒真的。

## 四、类型版与运行时版不一致的地方（`string_level_runtime.ts`）

认真对过之后发现至少两处不一致，都在 README 里记账：

| 场景 | 类型层 | 运行时 |
| --- | --- | --- |
| `Split<"", D>` | `[]` | `String.prototype.split` 给 `[""]`（长度 1） |
| 「没有路径参数」 | `never` | 空数组 `[]`（不是 `undefined`） |

另外 `ExtractParams` 的运行时镜像对 `"/users/:id/"` 同样得到 `["id"]`，
与类型版在第 646 号 fixture 里的结论一致。

## 五、自检怎么跑

```bash
python selfcheck.py
```

- 3 个编译期 fixture（错误码精确比对，`OK` 表示必须零错误）；
- 1 个 `Eq/Expect` 负控探针（运行时生成后删除）；
- 15 条运行时断言（node 直跑 `.ts`，Node 22 内置类型擦除）。

本机实测 TypeScript **5.6.3**。

## 参考资料（本 README 实际读过）

- Template Literal Types（handbook）
  <https://www.typescriptlang.org/docs/handbook/2/template-literal-types.html>
  —— 拼接 / 笛卡尔积 / String Unions in Types / Inference with Template Literals /
  Intrinsic String Manipulation Types（含上面那段 `applyStringMapping` 源码）
- TypeScript 5.6.3 编译器本体 `…/typescript/lib/tsc.js`（本机跑自主版本，确认错误码语义）
