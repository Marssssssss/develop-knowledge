# 装饰器与元数据反射

> TypeScript 第二批 `649`。
> TypeScript 里存在**两套**装饰器：TC39 标准装饰器（5.0 起默认可用）与遗留装饰器
> （`--experimentalDecorators`）。元数据反射只属于后者，而且它的行为能从编译器源码里
> 逐条读出来 —— 这一篇就是把这套规则变成可执行断言。

## 一、两套装饰器的分界线

| | 标准装饰器 | 遗留装饰器（`--experimentalDecorators`） |
| --- | --- | --- |
| 语法 | 任何 ER 装饰器写法 | 同上，但需开关 |
| 能否装饰参数 | **不能** → `TS1206` | 可以 |
| `emitDecoratorMetadata` | **不支持**（与元数据 emit 不兼容） | 支持 |
| 第二个参数 | `context` 对象（`ClassMethodDecoratorContext` 等） | 无（只有 target/key/descriptor） |
| 写错签名的报错 | 类型不匹配 | `TS1329` accepts too few arguments |

TS 5.0 发布说明的原话：新提案 «is not compatible with `--emitDecoratorMetadata`,
and it does not allow decorating parameters»。实测对应：

- 不使用 `--experimentalDecorators` 时写参数装饰器 → **`TS1206 Decorators are not valid here`**
  （`types/param_decorator_standard.ts`）
- 只开 `--emitDecoratorMetadata` 不开 `--experimentalDecorators` → **`TS5052`**，
  注意它是**全局选项错误**，连行号都没有（`types/metadata_needs_legacy.ts`）
- 签名写对与否：遗留模式下方法装饰器要 `(target, key, descriptor)` 三个参数，
  少一个就是 `TS1329`（`types/legacy_signatures.ts` 用对了所以 `EXPECT: OK`）

标准装饰器的 well-typed 写法要把 `this`、参数表、返回值分别建模成三个类型参数
（`types/std_decorators.ts`）：

```ts
function loggedMethod<This, Args extends unknown[], Return>(
  target: (this: This, ...args: Args) => Return,
  context: ClassMethodDecoratorContext<This, (this: This, ...args: Args) => Return>
) { /* … */ }
```

## 二、元数据只发给「遗留」模式

源码第一句话就把门关了（`tsc.js` 的 `getTypeMetadata`）：

```js
function getTypeMetadata(node, container) {
  if (!legacyDecorators) return void 0;
  return USE_NEW_TYPE_METADATA_FORMAT ? getNewTypeMetadata(...) : getOldTypeMetadata(...);
}
```

`getOldTypeMetadata` 按顺序 append 三个 key：**`design:type` → `design:paramtypes` → `design:returntype`**。
谁该有谁没有，由这三个谓词决定：

| 谓词 | 命中的节点种类 |
| --- | --- |
| `shouldAddTypeMetadata` | MethodDeclaration / GetAccessor / SetAccessor / PropertyDeclaration |
| `shouldAddParamTypesMetadata` | ClassDeclaration、ClassExpression（**要有带 body 的构造函数**）；Method / Get / Set |
| `shouldAddReturnTypeMetadata` | **只有** MethodDeclaration |

实测出来的成员级结论：

| 成员 | `design:type` | `design:paramtypes` | `design:returntype` |
| --- | :-: | :-: | :-: |
| 普通属性 | ✅ | — | — |
| 方法 | ✅（恒为 `Function`） | ✅ | ✅ |
| getter | ✅ | ✅（空数组 `[]`） | — |
| setter | ✅ | ✅ | — |
| 类（有构造函数） | — | ✅ | — |
| 类（无构造函数） | — | — | — |

## 三、`__decorate` 是倒序的 ⇒ 应用顺序自下而上

```js
var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
  var c = arguments.length, r = ..., d;
  if (typeof Reflect === "object" && typeof Reflect.decorate === "function")
    r = Reflect.decorate(decorators, target, key, desc);
  else for (var i = decorators.length - 1; i >= 0; i--)
    if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
  return c > 3 && r && Object.defineProperty(target, key, r), r;
};
```

这个 `for (var i = decorators.length - 1; i >= 0; i--)` 有一个容易忽略的后果：
虽然 `getOldTypeMetadata` 是**按 type → paramtypes → returntype 的顺序拼数组**，
但真正跑起来被应用的次序是 **`design:returntype` → `design:paramtypes` → `design:type`**。
`selfcheck.py` 把这条次序也钉住了。

同一个成员上叠多个装饰器时同理：装饰器**工厂自上而下求值**，返回的装饰器**自下而上应用**

```ts
@first()
@second()
m(): void {}
// 实测：eval:first → eval:second → apply:second → apply:first
```

## 四、类型序列化表（这才是坑最多的一段）

`serializeTypeNode` 的实现是一张 switch，把类型节点映射成运行时能拿到的一段表达式。
下面这张表全部由 `metadata_sample.ts` 编译运行后实测得到：

| 声明的类型 | `design:type` 的值 | 源码分支 |
| --- | --- | --- |
| `string` / 模板字面量类型 | `String` | StringKeyword / TemplateLiteralType |
| `number` / `42` / `-1` | `Number` | NumberKeyword / LiteralType→NumericLiteral |
| `boolean` / `true` | `Boolean` | BooleanKeyword / TrueKeyword |
| `null` / `void` / `undefined` / `never` | `undefined`（发射的是 `void 0`） | 多条 |
| `number[]` / 元组 / `readonly number[]` | `Array` | Array/Tuple（Readonly 先解开） |
| `(a: number) => void` / `new () => T` | `Function` | FunctionType / ConstructorType |
| `bigint` | `BigInt` | 全局构造器，按 ES2020 版本要求 pick |
| `symbol` | `Symbol` | 全局构造器，ES2015 |
| 类引用 `Ref` | `Ref`（构造器本身） | TypeReference → TypeWithConstructSignatureAndValue |
| `any` / `unknown` / 类型字面量 / `T["a"]` / 映射类型 / `typeof X` / `this` | `Object` | 落到 switch 末尾的兜底 |
| 不写注解 | `Object` | `node === undefined` 时的默认值 |

**联合的三条特别规则**（`serializeUnionOrIntersectionConstituents`）：

1. 成员里有 `never` → **跳过**（`"a" | never` 得到 `String`，不是 `Object`）；
2. 成员里有 `unknown`（非交叉）→ 直接返回 `Object`；
3. 成员里有 `any` → 直接返回 `Object`；
4. 其余情况逐条序列化，**互相不等就 `Object`，全都相等才保留那一个**。

于是 `string | number` 是 `Object`，而 `"a" | "b"` 是 `String`；
`string | void` 是 `Object`（`void 0` 与 `String` 不等）。

交集同理：`{a:1} & {b:2}` 序列化两侧都得 `Object`。

> 一句话总结：**`design:type` 是「尽力」的**。拿不到具体引用时它给 `Object` 而不是报错，
> 所以别指望靠它做运行时校验 —— 它能分辨的只有「标量 / 数组 / 函数 / 类」这一层。

## 五、自检怎么跑

```bash
python selfcheck.py
```

1. 4 个 fixture 与 `// EXPECT:` 精确比对（fixture 支持 `// FLAGS:` 给逐文件开关）；
2. `metadata_sample.ts` 在 `--experimentalDecorators --emitDecoratorMetadata` 下编译成 JS，
   配 `Reflect.metadata` 桩跑一遍，检查成员级 key 与那条次序；
3. 38 个成员的 `design:type` 逐条比对序列化表；
4. 叠加装饰器的「求值 / 应用」顺序。

本机实测 TypeScript **5.6.3**。

## 参考资料（本 README 实际读过）

- TypeScript 5.0 发布说明 · Decorators / Differences with Experimental Legacy Decorators
  <https://www.typescriptlang.org/docs/handbook/release-notes/typescript-5-0.html>
  （新装饰器的 context 对象、well-typed 版本的 `This/Args/Return` 建模、
  «not compatible with --emitDecoratorMetadata, and it does not allow decorating parameters»、
  `@decorator export default` 与 `export default @decorator` 两种位置）
- TypeScript 5.6.3 编译器本体 `…/typescript/lib/tsc.js`
  （`getTypeMetadata` 的 `legacyDecorators` 门、`getOldTypeMetadata` 三个 key 的拼装顺序、
  `shouldAddTypeMetadata` / `shouldAddParamTypesMetadata` / `shouldAddReturnTypeMetadata`
  三个谓词、`__decorate` 的倒序遍历、`serializeTypeNode` 与
  `serializeUnionOrIntersectionConstituents` 的完整 switch）
