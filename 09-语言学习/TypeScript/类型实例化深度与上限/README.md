# 类型实例化深度与上限

> TypeScript 第二批 `647`。
> 类型层是**图灵完备**的，代价是必须有护栏。这一篇把 TypeScript 5.6.3 编译器里的
> 三道护栏挖出来，并现场测出每条护栏的**精确阈值**。

## 一、三道护栏（源码位置与判据）

常量直接抄自本机 `node_modules/typescript/lib/tsc.js`（5.6.3 的产物）：

| 护栏 | 判据 | 产出错误 |
| --- | --- | --- |
| `instantiateTypeWithAlias` | `instantiationDepth === 100 \|\| instantiationCount >= 5e6` | `TS2589` |
| `getConditionalType` | `tailCount === 1e3` | `TS2589` |
| `checkCrossProductUnion` | `getCrossProductUnionSize(types) >= 1e5` | `TS2590` |
| `removeSubtypes` | `count === 1e5` 且估算值 `> 1e6` | `TS2590` |

错误文案分别是 *Type instantiation is excessively deep and possibly infinite*（2589）与
*Expression produces a union type that is too complex to represent*（2590）。

注意 `instantiationDepth` 的判据是 **`===` 而不是 `>=`**：因为每递归一层都会 `++`，
正常执行到第 101 层之前就已经在第 100 层返回错误类型了，后面的分支走不到。

## 二、无限嵌套：`TS2589` 的经典触发（`types/infinite_unpack.ts`）

```ts
type InfiniteBox<T> = { item: InfiniteBox<T> };
type Unpack<T> = T extends { item: infer U } ? Unpack<U> : T;
type Test = Unpack<InfiniteBox<number>>;   // ❌ TS2589
```

`InfiniteBox` 每一层都还能再展开一次，`Unpack` 于是永远停不下来 —— 这不是「写错了」，
而是**类型层的停机不可判定**，护栏只能凭深度一刀切。
同一个文件里把数据换成有终点的元组（`Unwrap<[1,2,3]>`）就正常，用来隔离「数据无限」与「写法无限」。

## 三、尾递归消除：4.5 引入的那次性能修复

TypeScript 4.5 的发布说明给的例子是 **`TrimLeft`**：

```ts
type TrimLeft<T extends string> = T extends ` ${infer Rest}` ? TrimLeft<Rest> : T;
```

它是**单支尾递归** —— 真分支里除了再次调用自己什么都没做，所以不需要保存中间结果，
编译器可以在 `getConditionalType` 里用 `root = newRoot; mapper = newRootMapper` 直接转下一圈，
不产生新的实例化。`canTailRecurse` 的关键一行是：

```js
if (newRoot.aliasSymbol) { tailCount++; return true; }
```

**只有递归目标是「有名字的类型别名」时才会计入 `tailCount`** —— 内联写的条件类型没有
`aliasSymbol`，计数一直是 0（那种情况由 `instantiationDepth` 那道 guard 兜着）。

对照「非尾递归」版本：

```ts
type GetChars<S> = S extends `${infer C}${infer R}` ? C | GetChars<R> : never;   // 联合 ⇒ 有中间结果
type GetCharsOf<S> = GetCharsHelper<S, never>;                                   // 累加器 ⇒ 尾递归
type GetCharsHelper<S, Acc> = S extends `${infer C}${infer R}` ? GetCharsHelper<R, C | Acc> : Acc;
```

`types/getchars_shape.ts` 断言两者在短字符串上**结果完全相同**（`EXPECT: OK`）——
差别不在结果，在代价。

### 实测阈值（`selfcheck.py` 现场扫出来）

| 写法 | 最后放行的长度 | 首次报警的长度 | 走的护栏 |
| --- | --- | --- | --- |
| `GetChars` 非尾递归 | 48 字符 | **49** 字符 | `instantiationDepth === 100` |
| `GetCharsHelper` 尾递归 | 999 字符 | **1000** 字符 | `tailCount === 1e3` |
| `TrimLeft`（尾递归） | 999 前导空格 | **1000** 前导空格 | `tailCount === 1e3` |

尾递归那一栏的 1000 与源码里的 `1e3` **严格相等**，这条对应关系是最好的证据：
不是「大约」，就是同一个常量。

非尾递归那一栏 ≈ 每个字符消耗 2 次实例化再加常数项；
拟合出来的模型 `2n + 3`（48 → 99 放行，49 → 101 报警）在 README 里明说是**拟合**结果，
不是源码里的公式。

## 四、联合爆炸：`TS2590`

多个插值位的模板字面量类型会把联合**乘**起来。`checkCrossProductUnion` 在真正铺开之前先估一次大小：

```js
function getCrossProductUnionSize(types) {
  return reduceLeft(types, (n, t) =>
    t.flags & Union ? n * t.types.length : t.flags & Never ? 0 : n, 1);
}
// size >= 1e5 ⇒ TS2590
```

两个重要的细节：

- 任一位置是 `never`，整个乘积立刻是 **0**（乘到 0 之后不会再有机会恢复）；
- 阈值按**估算的大小**判定，不需要真的构造出 10 万个成员 —— 所以 100000 这个量级的检查是瞬时的。

实测（`selfcheck.py` 的第三段）：

| 组合 | 规模 | 结果 |
| --- | --- | --- |
| `10^4` | 10000 | 放行 |
| `10^5` | 100000 | `TS2590` |
| `316^2` | 99856 | 放行 |
| `317^2` | 100489 | `TS2590` |

316/317 这一对把阈值**夹住了**：真实阈值就在 99856 与 100489 之间，
与源码里的 `1e5` 完全一致。

## 五、第四道护栏：`removeSubtypes` 的估算

删除联合里的子类型时也有门：

```js
if (count === 1e5) {
  const estimatedCount = count / (len - i) * len;
  if (estimatedCount > 1e6) { /* TS2590 */ }
}
```

它先算「照这个速度全跑完会有多少」，只有估算值**严格大于** `1e6` 才报错。
注意 `count === 1e5` 也是 `===`：只在刚好数到十万那一刻判一次。

## 六、自检怎么跑

```bash
python selfcheck.py
```

1. 4 个 fixture 与 `// EXPECT:` 错误码集合精确比对；
2. **边界扫描**：现场生成探针文件，把 48/49、999/1000、316²/317² 三组阈值正反各钉一次；
3. 17 条运行时断言（`guard_limits_runtime.ts`，把上面四个常量搬进 JS）。

本机实测 TypeScript **5.6.3**。这里要分清：运行时脚本**不是**「编译器在报错」的证据，
第 2 步的边界扫描才是 —— 前者只是把源码里的常量变成可执行断言。

## 参考资料（本 README 实际读过）

- TypeScript 4.5 发布说明 · Tail-Recursion Elimination on Conditional Types
  <https://www.typescriptlang.org/docs/handbook/release-notes/typescript-4-5.html>
  （`InfiniteBox` / `Unpack` / `TrimLeft` / `GetChars` 与「何谓单支尾递归」的说明）
- TypeScript 5.6.3 编译器本体 `…/typescript/lib/tsc.js`
  （`instantiateTypeWithAlias`、`getConditionalType` 的 `tailCount`、`canTailRecurse` 的
  `aliasSymbol` 判定、`checkCrossProductUnion` / `getCrossProductUnionSize`、
  `removeSubtypes` 的估算式、`Diagnostics` 里 2589/2590 两条文案）
