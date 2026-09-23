# satisfies 与类型注解

> TypeScript 第二批 `645`。
> `satisfies`（TS 4.9）解决的矛盾：**既想校验表达式符合某个类型，又想保留表达式自身最精确的推断类型**。

## 一、三种写法的分工

```ts
const a: Record<Colors, string | RGB> = { … };   // 注解：改结果类型
const b = { … } satisfies Record<Colors, string | RGB>; // satisfies：只校验
const c = { … } as Record<Colors, string | RGB>;         // 断言：绕过检查
```

| 写法 | 是否检查缺失/多余 key | 是否保留每个属性的精确类型 | 副作用 |
| --- | --- | --- | --- |
| 类型注解 | ✅ | ❌ 抬成目标类型 | 后续读取全是 union |
| `satisfies` | ✅ | ✅ | 无（不改动推断结果） |
| `as` 断言 | ❌（不做多余属性检查） | ✅ | 可能掩盖真实不匹配 |

一句话：**注解「抬类型」、`satisfies`「保类型」、`as`「闭眼」。**

## 二、为什么注解会丢信息（`types/annotation_loses.ts`）

```ts
const palette: Record<Colors, string | RGB> = {
  red: [255, 0, 0], green: "#00ff00", blue: [0, 0, 255],
};
const lifted: string = palette.green;             // 目标类型是 string | RGB
const scream = palette.green.toUpperCase();       // ❌ TS2339
```

注解把变量本身的类型替换成 `Record<Colors, string | RGB>`，于是每个属性的静态类型都是
`string | RGB`。**拼错的 key 被抓到了（TS2739/TS2739 类缺失错误），但代价是 `green` 再也不能当 string 用** ——
这正是 4.9 发布说明里说的「we'd lose the information about each property」。

实测（`tsc --strict`，本 demo 自检跑通）：

- `annotation_loses.ts` → `TS2322`（`string | RGB` 赋给 `RGB`）+ `TS2339`（union 上没有 `toUpperCase`）

## 三、为什么 `satisfies` 不丢信息（`types/satisfies_keeps.ts`）

```ts
const palette = {
  red: [255, 0, 0], green: "#00ff00", blue: [0, 0, 255],
} satisfies Record<Colors, string | RGB>;

const lifted: string = palette.green;   // ✅ 仍是 string
const tuple: RGB = palette.red;         // ✅ 仍是元组
```

关键点：**`satisfies` 的目标类型会作为上下文类型参与推断**。数组字面量 `[255, 0, 0]` 在
`string | RGB` 的上下文里被推断成元组 `RGB`，而不是默认的 `number[]`；如果不带 `satisfies`，
同一个字面量会退化成 `number[]`，连检查都过不去。所以这里不是「先推断出来再校验」这么简单，
推断本身就被目标类型引导了。

`EXPECT: OK` —— 四个 fixture 里唯一的零错误文件之一（另一个是下面第 5 节）。

## 四、`satisfies` 也做多余属性检查（`types/satisfies_excess.ts`）

```ts
const favoriteColors = {
  red: "yes", green: false, blue: "kinda",
  platypus: false,          // ❌ TS2353
} satisfies Record<Colors, unknown>;
```

`Record<Colors, unknown>` 只有 `red | green | blue` 三个 key，对象字面量的**新鲜度**
仍然存在，于是多余属性被拦下。这就是发布说明里「ensure that an object has all the keys
of some type, but no more」的用法。

注意：与此同时每条属性的类型信息仍然保留了 —— `favoriteColors.green` 还是 `boolean`。

## 五、换成索引签名后这条检查就失效（`types/satisfies_index_signature.ts`）

```ts
const palette = {
  red: [255, 0, 0], green: "#00ff00", platypus: 1,
} satisfies Record<string, string | RGB | number>;   // ✅ 不报错
```

`Record<string, …>` 带字符串索引签名，多余属性检查不再触发。所以：

- 想说「**值都得是某类型**」 → `Record<string, T>`
- 想说「**键不多不少正好这些，且值符合类型**」 → `Record<具名联合, T>`

这两句在 4.9 发布说明里是相邻的两个例子，差别就在目标类型有没有索引签名。

## 六、运行时没有任何残留（`satisfies_runtime.ts`）

`satisfies` 与类型注解、`as` 一样**纯编译期**：，运行时 objects 的形状完全不变，
`Object.keys`、可写性、原型都不受影响（自检里专门断言了这三条）。

真正不能省的是运行时兜底 —— 自检脚本演示了两件编译期做不到的事：

1. 往 `LIMITS` 里塞一个 `carrier-pigeon` 键在 JS 里完全可行（`satisfies` 的「不多不少」
   **不是**运行时 invariant）；
2. 每条通道的长度上限（`email 1024 / sms 140 / push 512`）必须靠运行时才生效，
   因为类型里 `sms` 与 `push` 都是 `number`，静态层面看不出谁更短。

结论：`satisfies` 把「形状」钉在编译期，把「业务规则」留给运行时。

## 七、自检怎么跑

```bash
python selfcheck.py
```

- **编译期**：对 `types/*.ts` 逐个跑 `tsc --strict --target es2020 --noEmit`，
  把实际错误码集合与文件头 `// EXPECT:` 声明的集合**判等**（多报少报都失败）；
- **运行期**：`node satisfies_runtime.ts`（Node 22 内置类型擦除），11 条断言全绿。

本机实测 TypeScript **5.6.3**（错误码以该版本为准）。

## 参考资料（本 README 实际读过）

- TypeScript 4.9 发布说明 · The satisfies Operator
  <https://www.typescriptlang.org/docs/handbook/release-notes/typescript-4-9.html>
  （`palette` / `favoriteColors` / `Record<string, …>` 三个例子与上下文推断的说明）
- TypeScript 5.6.3 编译器本体
  `~/.workbuddy/binaries/node/workspace/node_modules/typescript/lib/tsc.js`
  （用于确认本机错误码与`tsc --strict`的默认行为）
