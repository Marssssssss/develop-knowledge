# 方差与可赋值性：`strictFunctionTypes` 到底管住了谁

> 方差描述的是「容器/函数的类型参数变化时，外层类型往哪个方向才算兼容」。TypeScript 在结构类型的框架里用**实用性**换掉了部分严密性，最典型的就是**函数参数双变**。

## 一、返回值协变：给得更多是可以的

```ts
let x = () => ({ name: "Alice" });
let y = () => ({ name: "Alice", location: "Seattle" });
x = y;   // OK
y = x;   // Error, because x() lacks a location property
```

官方措辞：

> The type system enforces that the source function's return type be a subtype of the target type's return type.
> —— [Type Compatibility](https://www.typescriptlang.org/docs/handbook/type-compatibility.html)

## 二、参数位置默认是双变（并且承认这不安全）

> When comparing the types of function parameters, assignment succeeds if either the source parameter is assignable to the target parameter, or vice versa. **This is unsound** because a caller might end up being given a function that takes a more specialized type, but invokes the function with a less specialized type.

这就是 handbook 里 `listenEvent(EventType.Mouse, (e: MyMouseEvent) => ...)` 能通过的原因。这段「社会责任」用 `--strictFunctionTypes` 开启，但它**只作用于函数类型的属性位置**，方法简写保持双变（历史包袱：DOM 的 `addEventListener` 之类写法太多）。

`types/bivariance_strict.ts` 与 `types/bivariance_off.ts` 内容逐字相同，唯一差别是后者用 `--strictFunctionTypes false` 编译：

| 位置 | strict | strictFunctionTypes=off |
| --- | --- | --- |
| `compare(a: Dog, b: Dog): number`（方法简写） | OK | OK |
| `compare: (a: Dog, b: Dog) => number`（属性签名） | `TS2322` | OK |

「同一份代码两种编译结果」把这个开关的作用域钉死了：它修的是**函数类型**，不是**所有方法参数**。

## 三、显式方差标注（TS 4.7+）

> `out` and `in` are used here because a type parameter's variance depends on whether it's used in an *output* or an *input*.
> —— [Announcing TypeScript 4.7](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-4-7.html)

```ts
type Getter<out T> = () => T;          // 协变
type Setter<in T>  = (value: T) => void; // 逆变
interface State<in out T> { get: () => T; set: (value: T) => void } // 不变
```

`types/variance_annotations.ts` 实测：`Getter<Dog> → Getter<Animal>` 通过（协变）、`Setter<Animal> → Setter<Dog>` 通过（逆变）、`State<Dog> → State<Animal>` 报 `TS2322`（不变）。

标错会被当场纠正（错误文案与 4.7 公告一字不差）：

```
TS2636: Type 'State<sub-T>' is not assignable to type 'State<super-T>' as implied by variance annotation.
```

## 四、方差推断会失手，显式标注能补上 —— 但方向会变

4.7 公告给了这组相互递归的类型：

```ts
type Foo<T> = { x: T; f: Bar<T> }
type Bar<U> = (x: Baz<U[]>) => void
type Baz<V> = { value: Foo<V[]> }
declare let foo1: Foo<unknown>, foo2: Foo<string>;
foo1 = foo2;  // Should be an error but isn't ❌
foo2 = foo1;  // Error - correct ✅
```

实测（TypeScript 5.6.3）与公告完全一致：`types/recursive_variance.ts` 只报 `fooString = fooUnknown` 一条。

给 `Foo` 加上 `<in out T>` 之后（`types/recursive_variance_fixed.ts`），报错的**不是同一行**：原本通过的 `fooUnknown = fooString` 现在被拦住（`TS2322`），而原本报错的 `fooString = fooUnknown` 反而放行了。也就是说：**显式方差标注替换了比较路径，不是单纯给原结论加严**。文档只说「can help stop the problematic assignment」，本 demo 的实测把这句话的边界补清楚了——换路径意味着两边的行为都可能变化。

## 五、运行时的兑现

`variance.ts` 把双变的代价跑出来：一个只认 `MouseEvent` 的回调注册到只会发 `BaseEvent` 的总线上，编译通过；运行时喂给它一个键盘事件，`e.x` 读到 `undefined`，再参与运算得到 `NaN`——**不抛异常，只是结果变错**（8 条运行时断言全绿）。

对照的正确写法（逆变方向）是接受宽参数、在函数内自行判别 `kind`：安全性从「参数位置的类型约束」转移到了「函数体内的分支」，代价是每个回调都要写一遍判别。

## 六、运行

```
python selfcheck.py      # node>=22.6 + typescript>=5（tsc 路径可用 TSC_JS 覆盖）
node variance.ts
```

6 个编译期 fixture（其中 `bivariance_off.ts` 以 `--strictFunctionTypes false` 编译）+ 8 条运行时断言全绿。

## 七、边界与坑

- **`strictFunctionTypes` 不管方法**：把 `{ m(x: Dog): void }` 赋给 `{ m(x: Animal): void }` 依旧通过。想彻底逆变就把成员写成属性签名。
- **可选/剩余参数不做严格比对**：source 多出可选参数是允许的，文档明确说这也不安全（回调被调用时参数个数由调用方决定）。
- **枚举是数值兼容的**：`enum` 与 `number` 双向兼容，不同 enum 之间互不兼容（见 handbook Enums 一节）。
- **泛型参数不参与比较时，容器形状相同就互容**（见同目录「结构类型」demo 的 `Empty<T>`）。
- **方差标注不改变运行时任何行为**，它只影响类型检查的走法；写得「比实际更严」（例如把协变标成不变）编译器不会拦。

## 八、参考资料

- [TypeScript Handbook — Type Compatibility](https://www.typescriptlang.org/docs/handbook/type-compatibility.html)（返回类型协变、Function Parameter Bivariance、strictFunctionTypes、可选/rest 参数、枚举、类私有成员）
- [Announcing TypeScript 4.7 — Optional Variance Annotations for Type Parameters](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-4-7.html)（`in`/`out`、错误信息原句、递归类型的推断失手例子）
- 编译器版本 TypeScript 5.6.3；所有错误码由 `tsc --strict --target es2020 --noEmit` 实测取得。
