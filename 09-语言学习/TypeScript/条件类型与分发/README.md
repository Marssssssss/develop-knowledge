# 条件类型与分发：`never` 为什么不是 `never[]`

> 条件类型 `T extends U ? X : Y` 是 TypeScript 「类型层编程」的 if 语句。它的威力来自**和泛型结合**，而最大的坑来自当泛型接到联合类型时的**自动分发**。

## 一、基本形式

> Conditional types take a form that looks a little like conditional expressions in JavaScript: `SomeType extends OtherType ? TrueType : FalseType`.
> —— [Conditional Types](https://www.typescriptlang.org/docs/handbook/2/conditional-types.html)

单独用没意义（`Dog extends Animal ? number : string` 一眼就看得出结果）。真正的用途是配合泛型把「按输入类型选输出类型」编码成一个类型：

```ts
type NameOrId<T extends number | string> = T extends number ? IdLabel : NameLabel;
function createLabel<T extends number | string>(v: T): NameOrId<T> { /* ... */ }
```

`createLabel("x")` 得到 `NameLabel`，`createLabel(2.8)` 得到 `IdLabel`——**三个重载被一个类型替代**。

## 二、分配律：联合上的自动分发

官方文档：

> When conditional types act on a generic type, they become *distributive* when given a union type.

```ts
type ToArray<T> = T extends any ? T[] : never;
type StrArrOrNumArr = ToArray<string | number>;  // string[] | number[]
```

分发触发的条件是「**裸类型参数**」——`T extends ...` 里左边的 `T` 必须直接是类型参数本身。一旦把它包起来，分发立即停止：

```ts
type ToArrayNonDist<T> = [T] extends [any] ? T[] : never;
type ArrOfStrOrNum = ToArrayNonDist<string | number>;  // (string | number)[]
```

这就是 `Exclude` / `Extract` / `NonNullable` 与 `IsString<T>` 这类「整体判断」必须写成 `[T] extends [U]` 的原因。

**判据方向很容易写反**：下面这条「看起来有鉴别力」的断言实际上什么也没证明。

```ts
declare const v: ToArray<string | number>;
const negative: (string | number)[] = v;   // 竟然通过!
```

因为**数组是协变的**：`string[]` 与 `number[]` 各自都能赋给 `(string|number)[]`，联合自然也能。真正能区分的判据在**反方向**（把混合数组塞回分发结果），这条已固化在 `types/distribution.ts` 第 12 行。

## 三、分配律的必然后果：`ToArray<never>` 是 `never`

`never` 在分配律的语境下是**零元联合**（空集）。把条件类型分发到一个空集上，得到的是空集本身，而不是「装着 nothing 的数组」。实测证据（`types/distribution.ts`）：

```ts
declare const hole: ToArray<never>;
const emptyArrayIntoIt: ToArray<never> = [];
// error TS2322: Type 'never[]' is not assignable to type 'never'
declare const u: unknown;
const fromUnknown: ToArray<never> = u;
// error TS2322: Type 'unknown' is not assignable to type 'never'
const intoNonDist: ToArrayNonDist<never> = [];   // OK：非分发版本确实是 never[]
```

三条断言合起来唯一地钉住了 `ToArray<never> = never`：

| 判据 | 作用 |
| --- | --- |
| `never[] → ToArray<never>` 报错 | 排除 `never[]`（反向才有效，见第二节） |
| `unknown → ToArray<never>` 报错 | 排除 `any` / `unknown` |
| `never[] → ToArrayNonDist<never>` 通过 | 对照组：非分发时结果**不是** never |

这条差异解释了两个常见现场：`IsNever<T>` 必须写成 `[T] extends [never]`；`Exclude<A, B>` 遇到 `A = never` 时直接返回 `never` 而不是「把 never 排除掉」。

## 四、`infer`：从结构里挖出局部类型

```ts
type Flatten<T> = T extends Array<infer Item> ? Item : T;
type GetReturnType<T> = T extends (...args: never[]) => infer R ? R : never;
```

**重载函数的推断只作用于最后一个签名**，官方文档写得很明确：

> When inferring from a type with multiple call signatures … inferences are made from the *last* signature. It is not possible to perform overload resolution based on a list of argument types.

`types/infer_and_overloads.ts` 用「`last: string | number` 通过 / `first: number` 报 TS2322」锁住了这一点：若取自首个签名，`first` 那一行就不会报错。

## 五、真分支会把泛型再收紧一次

```ts
type MessageOfUnconstrained<T> = T["message"];   // TS2536
type MessageOf<T> = T extends { message: unknown } ? T["message"] : never;   // OK
```

条件成立时，编译器知道在这个分支里 `T` 必然有 `message`，于是索引访问合法——这是把「约束」从类型参数上**移到判断内部**的标准手法（假分支给 `never`，从而「不满足条件的输入自动被剔除」）。

## 六、运行

```
python selfcheck.py            # node>=22.6 + typescript>=5（tsc 路径可用 TSC_JS 覆盖）
node conditional_types.ts
```

`conditional_types.ts` 把「分发」写成运行时的 `flatMap`：分发 ⟷ 对每个成员各算一遍再合并，`never` ⟷ 空成员数组，map 到空集仍是空集。9 条运行时断言 + 3 个编译期 fixture 全绿。

## 七、边界与坑

- **分布与否取决于「裸不裸」**：`T extends X` 分发，`keyof T extends X`、`T[] extends X`、`[T] extends [X]` 都不分发。想「整体验证」一律加方括号。
- **`never` 不是 `never[]`**：写 `Exclude`/`IsNever` 之类的第一步就是决定要不要分布。
- **不要指望 `infer` 做重载解析**：多签名只按最后一个取。
- **运行时的形态完全不同**：编译期的分支/重载会被擦成单个 `if`（见运行时 demo 里 `pick` 的实现）。类型层做了多少「计算」，运行时成本一律是 0。
- **深层递归条件类型有递归深度上限**，超出报 `TS2589`（expression is excessively deep）；本目录的 demo 均为非递归，不涉及。

## 八、参考资料

- [TypeScript Handbook — Conditional Types](https://www.typescriptlang.org/docs/handbook/2/conditional-types.html)（基本形式、constraints、infer、distributive、`[T] extends [U]`）
- 编译器版本：TypeScript 5.6.3（`tsc --version`）；本目录所有错误码均由 `tsc --strict --target es2020 --noEmit` 实测取得。
