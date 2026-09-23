// EXPECT: OK
// 接口合并后的**重载顺序**是压缩过的两条规则：
//   1. 后面的 interface 组整体排在前面（later wins）
//   2. 形参是「单个字符串字面量」的特化签名再冒泡到最顶
// 这份 fixture 故意让「合并后的顺序」与「书写顺序」给出不同的返回类型，
// 于是每个赋值都成了对合并顺序的断言 —— 顺序错一步就会 TS2322。

interface Base { k: string }
interface Elem extends Base { e: true }
interface Div extends Elem { d: true }
interface Span extends Elem { s: true }
interface Canvas extends Elem { c: true }

interface Factory { make(tag: any): Base }
interface Factory { make(tag: "div"): Div; make(tag: "span"): Span }
interface Factory { make(tag: string): Elem; make(tag: "canvas"): Canvas }

declare const f: Factory;

// 期望的合并结果（据此上面的赋值才成立）：
//   make(tag: "canvas"): Canvas      ← 特化签名冒泡到顶
//   make(tag: "div"): Div            ← 特化签名冒泡到顶
//   make(tag: "span"): Span          ← 特化签名冒泡到顶
//   make(tag: string): Elem          ← 后写的组整体前排
//   make(tag: any): Base
const d: Div = f.make("div");
const s: Span = f.make("span");
const c: Canvas = f.make("canvas");
const e: Elem = f.make("p");

// 第二类验证：later-wins 在没有特化签名时也能看出来。
// Sheep 比 Animal 多一个属性，若 Animal 那条签名排在前面，
// `clone(sheep)` 会返回 Animal，赋给 Sheep 就失败。
interface Animal { name: string }
interface Sheep extends Animal { wool: true }

interface Cloner { clone(a: Animal): Animal }
interface Cloner { clone(a: Sheep): Sheep }

declare const cloner: Cloner;
declare const sheep: Sheep;
const cloned: Sheep = cloner.clone(sheep);

export { d, s, c, e, cloned };
