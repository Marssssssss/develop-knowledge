/**
 * 方差在运行时的样子：编译期的方向规则一旦违反，受害者是回调的参数。
 *
 * 官方 handbook 承认函数在参数位置默认是双变的（bivariance），理由是
 * 「a caller might end up being given a function that takes a more specialized
 * type, but invokes the function with a less specialized type」——
 * 下面把这个 unsound 场景真正跑一遍。
 *
 * 运行：node variance.ts
 */

type EventKind = "mouse" | "keyboard";

interface BaseEvent {
  kind: EventKind;
  timestamp: number;
}
interface MouseEvent2 extends BaseEvent {
  kind: "mouse";
  x: number;
  y: number;
}
interface KeyboardEvent2 extends BaseEvent {
  kind: "keyboard";
  keyCode: number;
}

type Handler = (e: BaseEvent) => void;

let failures = 0;
function check(cond: boolean, msg: string): void {
  if (cond) {
    console.log("ok " + msg);
  } else {
    failures += 1;
    console.log("FAIL " + msg);
  }
}

// 一个「事件总线」：按注册顺序分发，与 TypeScript 一样不做任何参数检查
class Bus {
  private handlers: Handler[] = [];
  on(handler: Handler): void {
    this.handlers.push(handler);
  }
  emit(e: BaseEvent): string[] {
    const log: string[] = [];
    for (const h of this.handlers) {
      h(e);
      log.push("called");
    }
    return log;
  }
}

let observedX: unknown = "untouched";
const mouseOnly = (e: MouseEvent2): void => {
  observedX = e.x; // 若被喂了非鼠标事件，这里会静默读到 undefined
};
const bus = new Bus();
bus.on(mouseOnly as Handler); // 双变使这行合法（实际运行的就是 mouseOnly 本身）

let threwTypeError = false;
try {
  bus.emit({ kind: "keyboard", keyCode: 13, timestamp: 0 } as unknown as BaseEvent);
} catch (err) {
  threwTypeError = err instanceof TypeError;
}
check(observedX === undefined,
  "窄参数处理器被喂了键盘事件 ⇒ 读到 undefined（双变的代价在运行时兑现）");
// 键盘事件没有 x/y：mouseOnly 不会因为拿到错误成员而抛 TypeError，
// 而是静默读到 undefined —— 这才是双变最危险的形态：不报错，只是结果变成 NaN。
const keyboardVictim = { kind: "keyboard", keyCode: 13, timestamp: 0 } as unknown as MouseEvent2;
const bogusSum = (keyboardVictim.x as number) + (keyboardVictim.y as number);
check(!threwTypeError, "错配的事件没有抛类型错误（不是 TypeError，而是静默 undefined）");
check(Number.isNaN(bogusSum), "窄参数处理器读到 undefined ⇒ 求和变成 NaN 而非报错");

// 逆变（正确方向）的写法：老老实实接受宽类型再自行收窄
const defensive = (e: BaseEvent): void => {
  if (e.kind === "mouse") {
    check(typeof (e as MouseEvent2).x === "number", "逆变写法先判 kind 再访问 x");
  }
};
const bus2 = new Bus();
bus2.on(defensive);
check(bus2.emit({ kind: "keyboard", keyCode: 13, timestamp: 0 }).length === 1,
  "逆变写法下事件被安全跳过");
check(bus2.emit({ kind: "mouse", x: 1, y: 2, timestamp: 0 }).length === 1,
  "逆变写法下鼠标事件被正确处理");

// 返回值方向则相反：协变要求"给得更多"才行
function makeAnimal(): { name: string } {
  return { name: "generic" };
}
function makeDog(): { name: string; breed: string } {
  return { name: "rex", breed: "lab" };
}
const producers: Array<() => { name: string }> = [makeAnimal, makeDog];
check(producers[1]().name === "rex", "返回值协变：给得更多的函数可以顶替给得少的");
check(typeof (producers[1]() as { breed?: string }).breed === "string",
  "额外的返回值成员不会被裁剪（结构比较只按目标成员比对）");

if (failures > 0) {
  throw new Error("运行时断言失败 " + failures + " 条");
}
console.log("=== variance.ts 运行时断言全绿 ===");
