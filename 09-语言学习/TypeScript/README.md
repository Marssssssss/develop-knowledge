# TypeScript

> 09-语言学习 的第四门语言。选它的理由:TS 的"语言机制"不在运行时,而在**类型系统本身** ——
> 结构类型、条件类型、映射类型、方差,这些是其它语言目录里没有的维度。

## 子目录规划(一个语言特性 = 一个 demo)

```
TypeScript/
├── README.md
├── 结构类型/            # structural typing、超额属性检查、接口 vs 别名的可见差异
├── 条件类型与分发/       # T extends U ? X : Y、分布式条件类型、infer、递归类型
├── 映射类型与修饰符/     # { [K in keyof T] }、as 重映射、-? / -readonly
├── 类型收窄与守卫/       # typeof / instanceof / in、类型谓词、assertion function、收窄的可达性
├── 方差与可赋值性/       # 协变/逆变/双变、strictFunctionTypes、方法参数双变的历史包袱
└── 声明合并与模块/       # interface merging、namespace、declare、模块解析与 .d.ts
```

## 与其它语言目录的分工

| 语言 | 主线 |
| --- | --- |
| Python | 动态类型 + 引用计数 GC + 语法糖 |
| Golang | GC + CSP 并发 |
| Rust | 无 GC 的内存安全:所有权 / 借用检查 / trait |
| **TypeScript** | **纯编译期**:结构化类型系统 + 类型级编程(类型本身可计算) |

Rust 的 trait 与 TS 的 interface 都做"编译期约束",但**约束的时机和代价完全不同**:
Rust 用约束换内存安全(零运行时开销),TS 的约束在运行时**全部被擦除**。

## 与已有 demo 的边界

- `02-Web开发/01-前端框架/` 下的 TS 文件是**框架模型的类型化孪生**,讲的是框架原理
- 本目录讲的是**类型系统本身**:同一段逻辑,为什么这样写能通过检查、那样写不能

## 已完成 demo(2026-09-19 首批 5 个)

| demo | 核心机制 | 哨兵结论(tsc 实测) |
| --- | --- | --- |
| [结构类型/](./结构类型/) | 结构子类型、对象字面量新鲜度、private 的名义性、branding | `TS2561`+`TS2559`(新鲜度两条)、`TS2322`(不同源 private / 不同 T 的泛型)、`TS2345`(brand 实参) |
| [条件类型与分发/](./条件类型与分发/) | 分配律、`[T] extends [U]`、`infer`、真分支收紧泛型 | 分发是真 ⟷ 混合数组塞不进结果(`TS2322`);`ToArray<never>` 不接受 `[]` ⇒ 结果是 `never` |
| [映射类型与修饰符/](./映射类型与修饰符/) | `-?` / `-readonly`、`as` 重映射、同态保持 | `-readonly` 生效 ⟷ 未解锁时必报 `TS2540`;键集改写后 `string[]` 不再是数组(`TS2739`) |
| [类型收窄与守卫/](./类型收窄与守卫/) | typeof/truthiness/`in`、类型谓词、`never` 穷尽性、CFA 边界 | `typeof null === "object"` ⇒ `TS18047`;谓词不做运行时校验;闭包内读取 ⇒ `TS18048` |
| [方差与可赋值性/](./方差与可赋值性/) | 双变、`strictFunctionTypes` 作用域、`in`/`out` 标注 | 同一份代码开关前后 `TS2322` ↔ 无错;标错方差 ⇒ `TS2636` |
| [satisfies与类型注解/](./satisfies与类型注解/) | 注解抬类型 vs `satisfies` 保类型 vs `as` 闭眼、上下文推断 | 注解 ⇒ `TS2322`+`TS2339`;`Record<联合,…>` 拦多余 key(`TS2353`)而 `Record<string,…>` 不拦;`satisfies` 是纯编译期 |
| [模板字面量类型与类型级解析/](./模板字面量类型与类型级解析/) | 拼接、插值位笛卡尔积、四个 intrinsic、`infer` 模式匹配做析串 | 事件名写错 ⇒ `TS2345`;`Uppercase<"ß">` 是 `"SS"`(非 locale 感知);`Split<"",D>` 与 `String.split` 不一致 |
| [类型实例化深度与上限/](./类型实例化深度与上限/) | `TS2589`/`TS2590` 三道护栏、尾递归消除、联合交叉乘积 | 非尾递归 48 放行 / 49 报警;尾递归 999 / 1000(=`tailCount === 1e3`);316² 放行 / 317² `TS2590`(=`>= 1e5`) |
| [声明合并与模块增强/](./声明合并与模块增强/) | namespace/type/value 三格记账、重载顺序与特化冒泡、增强限制 | 同名非函数成员类型冲突 ⇒ `TS2717`;`class+class`/`class+var` ⇒ `TS2300`;未导出跨 block ⇒ `TS2304`;增强 default ⇒ `TS2427` |
| [装饰器与元数据反射/](./装饰器与元数据反射/) | 标准 vs 遗留两套装饰器、`design:*` 三个 key、类型序列化表 | 标准模式参数装饰器 ⇒ `TS1206`;`emitDecoratorMetadata` 单开 ⇒ `TS5052`;getter/setter 无 `design:returntype` |

每个 demo 自带 `selfcheck.py`:**编译期**对 `types/*.ts` 跑 `tsc --strict` 并精确比对期望错误码集合,
**运行期**用 node 直跑主 TS(Node 22 起内置类型擦除)校验协作行为 ——
这是唯一能把"类型系统结论"变成可执行断言的方式。

## 第二批(2026-09-23 · ID 645-649)

首批五个 demo 把「类型是怎么被算出来的」讲完了(结构子类型、条件类型、映射类型、收窄、方差),
第二批往**工程实践边界**走:`satisfies` 解决保类型的校验矛盾、模板字面量类型把字符串变成可计算值、
静态的语言需要护栏(2589/2590)、同名声明怎么拼、装饰器的两套模型。

每个 demo 自带 `selfcheck.py`:**编译期**对 `types/*.ts` 跑 `tsc` 并精确比对期望错误码集合,
第二批新增两类更强的手段 —— **负控**(改掉一个条件后必须真的报错,否则原断言是恒真的)
与**边界扫描**(现场生成探针,把 49 / 1000 / 1e5 这类阈值正反各钉一次)。
**运行期**用 node 直跑主 TS(Node 22 起内置类型擦除),部分 demo 还会**编译成 JS 再跑**
(装饰器那一题必须走 `tsc --experimentalDecorators` 的 emit,类型擦除跑不了遗留装饰器)。

## 待研究

- [x] 结构类型与名义类型的分界(TS 没有 nominal typing,怎么模拟)
- [x] 分布式条件类型的分配律陷阱
- [x] `satisfies` 与类型注解的语义差异
- [x] 模板字面量类型与类型级字符串解析
- [x] 编译期计算的上限(实例化深度、递归类型爆栈)
- [x] 声明合并与模块
- [ ] 装饰器的两套模型之外:`ClassMethodDecoratorContext.addInitializer` 的初始化时机
- [ ] `import type` / `verbatimModuleSyntax` / `isolatedModules` 三者的 emit 差异
- [ ] `--moduleResolution` 四档(node10 / node16 / nodenext / bundler)与 package.json `exports`
- [ ] `tsconfig` strict 家族:`noUncheckedIndexedAccess` / `exactOptionalPropertyTypes` / `useUnknownInCatchVariables`
- [ ] 项目引用(project references)与增量构建的边界

## 第二批新增来源(实际读过)

- Release Notes: TypeScript 4.5(尾递归消除)、4.9(`satisfies`)、5.0(标准装饰器)
- Handbook: Template Literal Types、Declaration Merging、Modules - Reference
- 编译器本体:本机 TypeScript **5.6.3** 的 `lib/tsc.js`
  (`instantiateTypeWithAlias`、`getConditionalType`、`checkCrossProductUnion`、
  `getTypeMetadata` / `shouldAdd*Metadata` / `serializeTypeNode`、`__decorate`)

## 进度

由 [`_docs/STATE.md`](../../_docs/STATE.md) 统一追踪。本文件为**索引与路线图**,
未包含需要引用的机制论断;各 demo 的 README 按「内容来源铁律」先查官方手册再归纳。
