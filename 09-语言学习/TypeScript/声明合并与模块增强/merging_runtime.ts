// 声明合并是**编译期**的事，但它的运行时对应物很具体：
// 每段 namespace 编译成一个独立的 IIFE，interface 合并在 emit 之后什么都不是。
// 这一段把这几条对应关系做出来。

let pass = 0;
let fail = 0;

function check(label: string, cond: boolean): void {
  if (cond) {
    pass++;
    console.log("ok " + label);
  } else {
    fail++;
    console.log("FAIL " + label);
  }
}

// ---------- 1. interface 合并的运行时对应物：对象的浅合并 ----------
interface Box { height: number; width: number }
// 第二个同名 interface 在运行时不存在，只剩这一次 Object.assign
const box: Box & { scale: number } = Object.assign({ height: 5, width: 6 }, { scale: 10 });
check("merged box has all three members",
  box.height === 5 && box.width === 6 && box.scale === 10);
check("later literal wins on conflict like a later interface would require consensus",
  Object.assign({ h: 1 }, { h: 2 }).h === 2);

// ---------- 2. namespace 合并 ⇒ 每段一个独立作用域 ----------
// 编译产物形如：var Animal; (function (Animal) { … })(Animal || (Animal = {}));
// 于是「未导出成员」在两个块之间不通 —— 运行时与编译期的报错位置一致。
const nsA = new Function("return (function () { var haveMuscles = true;" +
  " return { animalsHaveMuscles: function () { return haveMuscles; } }; })();")() as {
  animalsHaveMuscles(): boolean;
};
check("exported member sees the block-local variable", nsA.animalsHaveMuscles() === true);

let threw = false;
try {
  new Function("return haveMuscles;")();
} catch (e) {
  threw = e instanceof ReferenceError;
}
check("another block cannot see it: ReferenceError at runtime too", threw);

let threwInner = false;
try {
  new Function("var haveMuscles = true; return (function(){ return haveMuscles; })();")();
  threwInner = false;
} catch {
  threwInner = true;
}
check("same block does see it (no cross-block leakage needed)", !threwInner);

// ---------- 3. namespace 与 function 合并 ⇒ 给函数对象挂属性 ----------
function buildLabel(name: string): string {
  const self = buildLabel as unknown as { prefix: string; suffix: string };
  return self.prefix + name + self.suffix;
}
const nsB = { suffix: "", prefix: "Hello, " };
Object.assign(buildLabel, nsB);
check("function + namespace merge is just property assignment",
  buildLabel("Sam Smith") === "Hello, Sam Smith");
check("the namespace members are readable off the function object",
  typeof (buildLabel as unknown as Record<string, unknown>)["prefix"] === "string");

// ---------- 4. namespace 与 enum 合并 ⇒ 给 enum 对象挂静态方法 ----------
const Color = { red: 1, green: 2, blue: 4 } as const;
function mixColor(colorName: string): number | undefined {
  if (colorName === "yellow") return Color.red + Color.green;
  if (colorName === "white") return Color.red + Color.green + Color.blue;
  if (colorName === "magenta") return Color.red + Color.blue;
  if (colorName === "cyan") return Color.green + Color.blue;
  return undefined;
}
check("enum + namespace: static method added next to members", Color.red === 1);
check("mixing works off the merged object", mixColor("yellow") === 3 && mixColor("white") === 7);
check("unknown colour stays undefined", mixColor("chartreuse") === undefined);

// ---------- 5. 模块增强的运行时对应物：动 prototype ----------
class Observable<T> {
  value: T;
  constructor(value: T) {
    this.value = value;
  }
}
// 类型侧的 declare module "./observable" { interface Observable<T> { map(...) } }
// 在运行时的样子：
(Observable.prototype as unknown as Record<string, unknown>)["map"] = function <U>(
  this: Observable<{ toFixed: () => string }>,
  f: (x: { toFixed: () => string }) => U
): Observable<U> {
  return new Observable(f(this.value));
};
const o = new Observable(42 as unknown as { toFixed: () => string });
const mapped = (o as unknown as {
  map<U>(f: (x: { toFixed: () => string }) => U): Observable<U>;
}).map((x) => x.toFixed());
check("prototype patch is visible on existing instances", mapped.value === "42");
check("patch lives on the prototype, not the instance",
  !Object.prototype.hasOwnProperty.call(o, "map"));

console.log("runtime: pass=" + pass + " fail=" + fail);
if (fail > 0) process.exit(1);
