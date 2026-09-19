# 结构类型：为什么两个「毫无关系」的类型可以互相赋值

> TypeScript 是**结构化类型系统**（structural typing）：两个类型是否兼容，只取决于成员的**形状**，与类型叫什么、有没有 `extends` 无关。这与 Java/C# 的名义子类型形成对照。

## 一、基本规则

官方文档的原话是：

> The basic rule for TypeScript’s structural type system is that `x` is compatible with `y` if `y` has at least the same members as `x`.
> —— [Type Compatibility](https://www.typescriptlang.org/docs/handbook/type-compatibility.html)

即**方向性**：把源赋给目标时，只检查**目标声明的成员**在源里是否都存在且兼容；源多出来的成员一概不管。这个比较是**递归**的——每个成员的类型再按同一规则往下比。

```ts
interface Pet { name: string }
const dog = { name: "Lassie", owner: "Rudd Weatherwax" };  // { name: string; owner: string }
const pet: Pet = dog;   // OK：只关心 name
```

之所以这么设计，文档说明得很直接：JS 里匿名对象、对象字面量、函数表达式遍地都是，用结构类型描述库之间的关系比名义类型「自然得多」。

## 二、唯一的例外：对象字面量的超额属性检查

结构类型放宽的代价是**拼错的属性会被静默吞掉**。TS 用一个针对性规则补漏洞：

> Object literals get special treatment and undergo *excess property checking* when assigning them to other variables, or passing them as arguments.
> —— [Object Types: Excess Property Checks](https://www.typescriptlang.org/docs/handbook/2/objects.html)

```ts
createSquare({ colour: "red", width: 100 });  // TS2561：colour 不存在于 SquareConfig
```

关键在「**special treatment**」：这条检查只作用于**新鲜的对象字面量**（freshness）。把它先赋给变量，新鲜度就消失了：

| 写法 | 现象 | 错误码 |
| --- | --- | --- |
| `createSquare({ colour, width })` | 字面量新鲜 → 检查多余 key | `TS2561` |
| `const o = {...}; createSquare(o)` | 变量已「冷却」→ 走普通结构比较 | 通过 |
| `const o = { colour }; createSquare(o)` | 无任何共同属性 → 退化检查 | `TS2559` |

三条都在 `types/freshness.ts` 里用 tsc 实测固化（期望错误码集合 `{TS2561, TS2559}`）。注意 `TS2559` 是「没有共同属性」，它比 `TS2561` 条件更弱——**只要有任意一个同名 property 就算结构比较通过**，这常被误当成「属性检查还在生效」。

## 三、private/protected：结构系统里唯一的名义成分

```ts
class Storage    { private readonly secret = 42; /* ... */ }
class Lookalike  { private readonly secret = 42; /* ... */ }
const bad: Storage = new Lookalike();   // TS2322
```

文档：

> Private and protected members in a class affect their compatibility… the source type must also contain a private member that **originated from the same class**.

两个形状完全相同的类，因为 `secret` 来自不同声明而不兼容。**这是 TS 里唯一一处「名字/来源」参与兼容判定的地方**，可以利用它做出半名义类型（无需 brand）。

## 四、泛型：类型参数不参与就比较不出来

```ts
interface Empty<T> {}          // T 没被任何成员使用
declare let x: Empty<number>, y: Empty<string>;
x = y;  // OK：结构完全相同

interface NotEmpty<T> { data: T }
// NotEmpty<number> 与 NotEmpty<string> 互不兼容 → TS2322
```

原因仍是「成员成员再成员」：`Empty<number>` 擦掉类型参数后是 `{}`，天然与 `Empty<string>` 等价。这也是 `WeakMap`/`Container` 之类空壳泛型写不好会「什么都塞得进去」的根因。

## 五、模拟名义类型：branding

```ts
declare const brandKey: unique symbol;
type Brand<T, B extends string> = T & { readonly [brandKey]: B };
type UserId  = Brand<string, "UserId">;
type OrderId = Brand<string, "OrderId">;
```

`UserId` 与 `OrderId` 在编译期互不相容（赋值 `TS2322` / 传参 `TS2345`），但 `type` 与 `interface` 一样**在运行时被完全擦除**：`UserId` 变量就是普通字符串，`typeof` 是 `string`，`Object.getOwnPropertySymbols` 为空数组（见 `structural_types.ts` 第 4 节实测）。

## 六、运行

```
python selfcheck.py            # 依赖 node>=22.6 + typescript>=5（tsc 可用 TSC_JS 覆盖）
node structural_types.ts        # Node 22 起内置类型擦除，可直接跑 .ts
```

自检做两件事：① 对 `types/*.ts` 跑 `tsc --strict`，比对每个 fixture 头部 `// EXPECT:` 声明的**错误码集合**（要求精确相等，多报少报都算失败）；② 跑 `structural_types.ts` 的 13 条运行时断言。当前结果：4 fixture + 13 断言全绿。

## 七、边界与坑

- **超额属性检查只覆盖「新鲜字面量」**：把它当成「属性白名单」防线是不可靠的——中间变量、spread 展开、函数返回值传参都会让它失效。真正的防御要落在类型声明本身（加索引签名或显式允许该 key）。
- **断言 (`as`) 会绕过一切递归检查**：`{ owner: {} } as unknown as DeepPet` 编译通过，运行时读到 `undefined`。「过了类型检查」永远不等于「运行时安全」。
- **private 只在编译期存在**：`(storage as any).secret` 能读到 42，`Object.keys(storage)` 里就有 `secret`（运行时实测 `check` 断言成功）。
- **brand 不跨运行时边界**：JSON 反序列化出来的字符串只能靠断言「宣称」自己是 `UserId`，branding 提供的是**调用点纪律**而非校验。
- **`TS2559` 只要求「一个共同属性」**，不是「多数属性」。

## 八、参考资料

- [TypeScript Handbook — Type Compatibility](https://www.typescriptlang.org/docs/handbook/type-compatibility.html)（结构子类型、函数双变、private 成员、泛型、assignability 表）
- [TypeScript Handbook — Object Types](https://www.typescriptlang.org/docs/handbook/2/objects.html)（可选属性、索引签名、Excess Property Checks）
