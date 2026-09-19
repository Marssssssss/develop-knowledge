# 类型收窄与守卫：编译器沿「可能到达的路径」替你把类型越推越窄

> Narrowing 是 TypeScript 最不像「静态类型」的部分：类型会随语句位置**变化**。理解它的关键是——它不是靠聪明的推理，而是靠**穷举可达路径**。

## 一、typeof 只能回答 8 个问题

文档列出的取值只有 `"string" | "number" | "bigint" | "boolean" | "symbol" | "undefined" | "object" | "function"`。于是这个经典例子必须报错：

```ts
function printAllBadly(strs: string | string[] | null) {
  if (typeof strs === "object") {
    for (const s of strs) { /* 'strs' is possibly 'null' —— TS18047 */ }
  }
}
```

因为 **`typeof null === "object"`**（JS 的历史包袱），「判定为 object」并不等于「排除了 null」。`types/typeof_null.ts` 用 `TS18047` 固化了这一点；正确写法是先过一遍真值判断：`if (strs && typeof strs === "object")`。

## 二、truthiness 免费，但会把 `0` 与 `""` 一起吞掉

假值集合是 `0 / NaN / "" / 0n / null / undefined`。用它挡 null/undefined 很方便，代价是**合法的有效值被当成缺失值**：

```ts
function countUsersOnline(n: number) {
  if (n) return "online: " + n;
  return "nobody";          // n === 0 时也走到这里
}
```

`narrowing.ts` 的运行时断言实测：`countUsersOnline(0) === "nobody"`、`greetName("") === "anonymous"`。这不是类型系统的错——它如实反映了 JS 的真值语义，但收窄结果（`number` → 非零）与业务语义不符。

## 三、`in` 收窄：optional 成员会出现在**两侧**

> optional properties will exist in both sides for narrowing.

`Fish | Bird | Human`（Human 的 `swim?`、`fly?` 均可选）在 `"swim" in animal` 之后，true 分支是 `Fish | Human`，false 分支是 `Bird | Human` —— Human 两边都在。`types/in_optional.ts` 的证据：false 分支里 `animal.swim()` 报 `TS2339`（`Bird` 没有这个成员），并且 Human 即使在 true 分支也只保证「可能有」，要调用还得再确认一次。

另一个运行时事实：`in` 会**顺着原型链**找（`narrowing.ts` 实测 `Object.keys(derived).length === 0` 但 `"swim" in derived === true`），所以「对象里有这个 key」与「收窄到这个类型」是两件事。

## 四、类型谓词：把一次判断变成可复用的收窄

```ts
function isFish(pet: Fish | Bird): pet is Fish { return (pet as Fish).swim !== undefined; }
const typed: Fish[] = zoo.filter(isFish);   // 元素类型跟着变窄
```

`types/predicates.ts` 用对照说明它的必要性：同样的过滤逻辑写成内联箭头函数，元素类型仍是联合，`untyped[0].swim()` 直接 `TS2339`——**谓词签名的价值在调用点，不在函数体**。

**谓词从不校验实现**：写一个恒返回 `true` 的 `isFishLying` 照样通过编译。`narrowing.ts` 实测它导致运行时 `TypeError`。类型谓词是一份「人工担保」，不是检查。

## 五、never 与穷尽性检查

> The `never` type is assignable to every type; however, no type is assignable to `never` (except `never` itself).

于是可以把 `default` 分支的东西赋给 `never`：一旦 union 新增成员而 switch 没跟上，赋值立刻失败。`types/exhaustive.ts` 加入 `Triangle` 后得到 `TS2322: Type 'Triangle' is not assignable to type 'never'`。**这是把「未来新增成员的遗漏」变成编译错误**的标准手法，代价是要保持 `switch` 而非 `if` 链（或显式构造联合上的映射）。

## 六、控制流分析的边界（三条实测）

| 场景 | 结论 | 错误码 |
| --- | --- | --- |
| 收窄后调用外部函数，再读同一属性 | **不被重置** —— 编译器不建模被调用者的副作用 | 无错（这是坑，不是保护） |
| 在返回的闭包里读同一属性 | 被保守处理：回调执行时机未知 | `TS18048` |
| 收窄后对该变量重新赋值 | 收窄结果丢弃，回到声明类型 | `TS2339` |

第一条尤其要注意：`if (box.value !== undefined) { maybeMutate(box); box.value.length }` **能通过编译**，而运行时 `maybeMutate` 完全可能把 `value` 置回 `undefined`。流分析是「沿语法路径」的，不是别名分析——把乐观结论当成运行时保证会踩坑。

## 七、运行

```
python selfcheck.py      # node>=22.6 + typescript>=5（tsc 路径可用 TSC_JS 覆盖）
node narrowing.ts
```

5 个编译期 fixture（期望错误码集合精确匹配）+ 14 条运行时断言全绿。

## 八、边界与坑

- **`typeof x === "object"` 不排除 `null`**（`TS18047` 的源头）；用真值判断或 `!== null`。
- **truthiness 收窄会吞掉 `0` / `""` / `NaN`**，涉及数值计数与可选字符串时优先写显式比较。
- **`in` 看原型链**，`Object.keys` 只看自有属性，两者不要混为一谈。
- **`instanceof` 依赖 `.prototype` 引用相等**，跨 realm（iframe / worker / VM）会失效，此时只能退回判别式属性。
- **类型谓词、`as`、非空断言 `!` 都是「人工担保」**，编译器不做任何一致性检查。
- **穷尽性检查依赖 `never` 赋值**，若 `default` 分支直接 `throw` 而没赋给 `never`，新增分支不会被发现。

## 九、参考资料

- [TypeScript Handbook — Narrowing](https://www.typescriptlang.org/docs/handbook/2/narrowing.html)（typeof/instanceof/in/truthiness、type predicates、assertion functions、`never` 与 exhaustiveness、control flow analysis）
- 编译器版本 TypeScript 5.6.3；所有错误码均由 `tsc --strict --target es2020 --noEmit` 实测取得。
