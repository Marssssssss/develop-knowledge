# 声明合并与模块增强

> TypeScript 第二批 `648`。
> 「同一个名字声明两次，编译器帮你拼成一个」—— 这句话背后是一整套按 **namespace / type / value**
> 三个维度分账的规则，以及几条明确禁止的组合。

## 一、先记住这张表（手册 Declaration Merging 的核心）

| 声明 | Namespace | Type | Value |
| --- | :-: | :-: | :-: |
| Namespace | ✅ | | ✅ |
| Class | | ✅ | ✅ |
| Enum | | ✅ | ✅ |
| Interface | | ✅ | |
| Type Alias | | ✅ | |
| Function | | | ✅ |
| Variable | | | ✅ |

**只有落在同一个格子里的东西才谈得上「冲突」。** 于是：

- `class` + `interface`：两边都只占 type 格 ⇒ 可以合（`types/disallowed_merge.ts` 里成功了）；
- `class` + `class`、`class` + `var`：都在 value 格 ⇒ `TS2300 Duplicate identifier`；
- `type alias` + `interface`：都在 type 格 ⇒ 也会冲突。

## 二、接口合并的两条规则

### 1. 非函数成员同名必须同类型，否则 `TS2717`

```ts
interface Box { height: number }
interface Box { height: string }   // ❌ TS2717
```

`TS2717` 的原文是 *Subsequent property declarations must have the same type*。
注意它只在**非函数**成员上生效 —— 同名的函数成员会被当成重载。

### 2. 函数成员按「组」重载，后写的组整体前排，特化签名再冒泡

```ts
interface Factory { make(tag: any): Base }
interface Factory { make(tag: "div"): Div;  make(tag: "span"): Span }
interface Factory { make(tag: string): Elem; make(tag: "canvas"): Canvas }
```

合并结果是：

```ts
make(tag: "canvas"): Canvas   // 特化签名冒泡到顶
make(tag: "div"): Div
make(tag: "span"): Span
make(tag: string): Elem       // 后写的组整体前排
make(tag: any): Base
```

`types/overload_order.ts` 让「合并后的顺序」与「书写顺序」给出**不同的返回类型**，
于是四个赋值都成了判据（顺序错一步就报错）。

> 这条 demo 自带**负控**（`selfcheck.py`）：把后写的两组删掉、只留 `make(tag: string)` 之后，
> 同一句 `const d: Div = f.make("div")` 变成 `TS2741`。
> 也就是说 `EXPECT: OK` 不是因为「反正都能赋上」，而是真的锁住了重载顺序。

### 写这段时踩到的一件事

第一版让子接口收窄父接口的字段类型（`interface Div extends Elem { kind: "div" }` 而
`Elem` 的 `kind` 是 `"elem"`），直接得到 **`TS2430 Interface 'Elem' incorrectly extends interface 'Base'`**。
`extends` 要求子类型是真子集，而互斥的字面量不是 —— 这个报错与本主题无关，但很容易误认为是合并出的错。

## 三、namespace 合并：导出才跨界

```ts
namespace Animal {
  let haveMuscles = true;                       // 未导出
  export function animalsHaveMuscles() { return haveMuscles; }   // ✅
}
namespace Animal {
  export function doAnimalsHaveMuscles() { return haveMuscles; } // ❌ TS2304
}
```

手册的说法是：未导出成员**只在原来那个 un-merged 块里可见**。

运行时的样子完全对称 —— 每段 `namespace` 会被编译成一个独立的 IIFE，
所以另一个块在运行时也 `ReferenceError`（`merging_runtime.ts` 里用 `new Function` 复刻了这个行为）。

namespace 还可以和 **class / function / enum** 合并，但**必须写在它们后面**：

```ts
class Album { label: Album.AlbumLabel }
namespace Album { export class AlbumLabel {} }        // 内部类

function buildLabel(name: string) { return buildLabel.prefix + name + buildLabel.suffix; }
namespace buildLabel { export let suffix = ""; export let prefix = "Hello, "; }

enum Color { red = 1, green = 2, blue = 4 }
namespace Color { export function mixColor(n: string) { /* … */ } }
```

## 四、模块增强（Module Augmentation）

JavaScript 模块本身不支持合并，但可以打补丁：

```ts
import { Observable } from "./observable";
declare module "./observable" {
  interface Observable<T> { map<U>(f: (x: T) => U): Observable<U>; }
}
```

三条规则（前两条来自手册，第三条是实测补的）：

1. **不能**增强 default export —— 实测 `TS2427 Interface name cannot be 'default'`
   （手册里指向 issue #14080：只能按「导出名」增强，而 `default` 是保留字）；
2. 增强里的**各级声明表现得就像写在原文件里一样**参与合并；
3. 手册那句「不能在增强里声明新的顶层声明」**在 5.6.3 上并没有被强制**：
   `declare module "./observable" { interface Brand { gap: number } }` 照样编译通过（`selfcheck.py` 里有探针钉住）。
   真正会报错的是在已经 ambient 的上下文里再写 `declare` ⇒ `TS1038`。

另外 `declare global { interface Array<T> { toObservable(): … } }` 是从模块内部给全局加声明的正式通道。

## 五、这些「合并」在运行时到底是什么（`merging_runtime.ts`）

| 编译期机制 | 运行时对应物 |
| --- | --- |
| 两个 interface 合并 | 什么都没有 / `Object.assign` 出来的形状 |
| namespace 合并 + 未导出成员 | 每段一个 IIFE ⇒ 跨块的 `ReferenceError` |
| `namespace` 合并进 `function` | 给函数对象挂属性（`buildLabel.prefix`） |
| `namespace` 合并进 `enum` | 给 enum 对象挂静态方法（`Color.mixColor`） |
| 模块增强 | `Observable.prototype.map = function …` |

11 条断言把这几行对照关系钉住，其中包括「补丁挂在 prototype 上而不是实例上」
（`Object.prototype.hasOwnProperty.call(o, "map") === false`）。

## 六、自检怎么跑

```bash
python selfcheck.py
```

1. 7 个 fixture 与 `// EXPECT:` 错误码集合精确比对；
2. 重载顺序负控（应报 `TS2741`）；
3. 增强边界探针（新顶层 interface 放行 / ambient 里 `declare` 报 `TS1038`）；
4. 12 条运行时断言。

本机实测 TypeScript **5.6.3**。

## 参考资料（本 README 实际读过）

- Declaration Merging（handbook）
  <https://www.typescriptlang.org/docs/handbook/declaration-merging.html>
  （namespace/type/value 三格表、接口合并的重载顺序与特化签名冒泡、namespace 与
  class/function/enum 的合并、Disallowed Merges、Module Augmentation 的两条限制）
- Modules - Reference（handbook）
  <https://www.typescriptlang.org/docs/handbook/modules/reference.html>
  （哪些 TS 专属声明可以被 import/export）
- TypeScript 5.6.3 编译器本体 `…/typescript/lib/tsc.js`（确认 2300 / 2304 / 2427 / 2717 / 1038 的出现条件）
