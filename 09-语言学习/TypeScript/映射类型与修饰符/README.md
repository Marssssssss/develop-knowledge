# 映射类型与修饰符：把「键的集合」当成可计算对象

> 映射类型是 TS 里唯一能**遍历成员**的类型构造：`{[P in keyof T]: X}` 把 `keyof T` 当作一个可以被改名、过滤、变换的列表。官方 Utility Types 里 `Partial` / `Required` / `Readonly` / `Pick` 几乎都建在它之上。

## 一、基本形式

> A mapped type is a generic type which uses a union of `PropertyKey`s (frequently created via a `keyof`) to iterate through keys to create a type.
> —— [Mapped Types](https://www.typescriptlang.org/docs/handbook/2/mapped-types.html)

```ts
type OptionsFlags<Type> = { [Property in keyof Type]: boolean };
// Features -> { darkMode: boolean; newUserProfile: boolean }
```

## 二、修饰符：用 `-` 剥掉，用 `+` 加上

> You can remove or add these modifiers by prefixing with `-` or `+`. If you don't add a prefix, then `+` is assumed.

```ts
type CreateMutable<Type> = { -readonly [Property in keyof Type]: Type[Property] };
type Concrete<Type>       = { [Property in keyof Type]-?: Type[Property] };
```

`types/modifiers.ts` 用「必须报错」的反向写法把效果锁死（没有报错就说明修饰符其实没被剥掉）：

| 代码 | 错误码 | 说明 |
| --- | --- | --- |
| `raw.id = "x"`（源类型 readonly） | `TS2540` | 对照组：只读确实挡得住写 |
| `unlocked.id = "x"`（`-readonly` 之后） | 无 | 只读被剥掉，写入放行 |
| `{ id: "u1" }` 赋给 `Concrete<MaybeUser>` | `TS2739` | `-?` 让 `name`/`age` 变必填，缺字段报错 |

注意 **对照组是断言的一部分**：只写「解锁后能赋值」无法证明 `-readonly` 生效（因为它本来就可能通过），必须有「没解锁时报 `TS2540`」这一条。

## 三、键重映射 `as`（TS 4.1+）

`as` 子句允许在遍历时改写键名，返回值若为 `never` 则该键被**删除**：

```ts
type Getters<Type> = {
  [Property in keyof Type as `get${Capitalize<string & Property>}`]: () => Type[Property]
};
type RemoveKindField<Type> = { [Property in keyof Type as Exclude<Property, "kind">]: Type[Property] };
```

实测判据（`types/remap.ts`）：

- `const mismatch: number = lazy.getName()` → `TS2322`（`getName` 返回 `string`，证明键确实被改成 `getName` 且值类型跟着变）；
- `kindless.kind` → `TS2339`（键被 `never` 过滤掉了）。

`Capitalize<string & Property>` 里的 `string &` 不是冗余：`Property` 的类型是 `keyof T`，可能包含 `number | symbol`，而模板字面量/内在字符串工具类型要求 `string` 的子类型。

## 四、同态：只有 `[P in keyof T]` 才算「原样复制形状」

把键集写成 `keyof T`，编译器会把原类型的**修饰符与容器形状**一并传播；一旦改写键集（哪怕语义上等价），这个「同态」性质就消失。`types/homomorphic.ts` 的两条实测证据：

```ts
type Homo<T>    = { [P in keyof T]: T[P] };
type NonHomo<T> = { [P in Extract<keyof T, string>]: T[P] };

const homoSame: Homo<MaybeBox> = {};      // OK：可选性被保留
const nonHomoNow: NonHomo<MaybeBox> = {}; // TS2741：a 变成必填（属性还在，修饰符没了）

const stillArray: string[] = homoArr;     // OK：同态版本仍是 string[]
const notArrayAnymore: string[] = nonHomoArr;
// TS2739：missing [Symbol.iterator], [Symbol.unscopables]
```

第二条尤其有价值：**属性一个不少，也不等于还是数组**—— `Extract<keyof T, string>` 把数字索引签名剔掉了，剩下的只是「恰好拥有 push/ length 等成员的对象」。这也是为什么自己手写 `MyPartial<T>` 时死活效果不对——多半是无意间丢掉了 `keyof T` 这个字面写法。

## 五、运行期的同构物

`mapped_types.ts` 把四种编译期操作翻译成普通 JavaScript：

| 编译期 | 运行期等价物 |
| --- | --- |
| `as \`get${Capitalize<P>}\`` | 遍历 `Object.keys` 后批量改名 |
| `as Exclude<P, "kind">` | 建新对象时跳过这些 key |
| `-?` | 用默认值填充缺失字段 |
| `-readonly` | **什么都不做** —— 类型是编译期约定 |

最后一条是这个 demo 的落点：`readonly` 约束下直接赋值会报 `TS2540`，但通过任一映射结果的引用写进去，运行时对象照改不误（实测 `box.value` 从 1 变成 2）。真正冻结要靠 `Object.freeze`，二者没有因果关系。

## 六、运行

```
python selfcheck.py      # node>=22.6 + typescript>=5（tsc 路径可用 TSC_JS 覆盖）
node mapped_types.ts
```

3 个编译期 fixture（期望错误码集合精确匹配）+ 9 条运行时断言全绿。

## 七、边界与坑

- **`-?` 不会把 `undefined` 从值域里去掉**：可选成员的类型是 `T | undefined`，`-?` 后变成「必须显式给出」的成员，值域仍含 `undefined`。
- **`Extract<keyof T, string>` 等改写会静默丢掉修饰符与 index signature**，写工具类型时优先保留 `keyof T` 原样。
- **`as never` 只能删 key，不能改 key 的值类型**；改值类型要动右侧的表达式。
- **深层递归映射类型会撞 `TS2589`**（递归深度上限），处理嵌套结构时应显式限制层数。
- **`readonly` 数组与 `ReadonlyArray` 也不是运行期不可变**，需要 `as const` + `Object.freeze` 才有运行时保证。

## 八、参考资料

- [TypeScript Handbook — Mapped Types](https://www.typescriptlang.org/docs/handbook/2/mapped-types.html)（基本形式、mapping modifiers、Key Remapping via `as`、further exploration）
- 本页所有错误码由 TypeScript 5.6.3 `tsc --strict --target es2020 --noEmit` 实测取得。
