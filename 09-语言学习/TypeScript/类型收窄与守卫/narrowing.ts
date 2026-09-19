/**
 * 收窄（narrowing）在运行时的对手：
 * 编译期那套"按路径判断类型"的结论，全都建立在几个运行时约定上 ——
 * typeof 只有 8 个取值、in 会顺着原型链、instanceof 依赖 .prototype、
 * 而类型谓词**完全不做运行期校验**。下面逐条把它们打回原形。
 *
 * 运行：node narrowing.ts
 */

type Fish = { swim: () => void; kind?: string };
type Bird = { fly: () => void; kind?: string };

let failures = 0;
function check(cond: boolean, msg: string): void {
  if (cond) {
    console.log("ok " + msg);
  } else {
    failures += 1;
    console.log("FAIL " + msg);
  }
}

// ---- 1. typeof 的取值数量：null 就是 "object" ----
check(typeof null === "object", "typeof null === 'object'（收窄到 object 不等于排除 null）");
check(typeof (() => 0) === "function" && typeof Symbol() === "symbol",
  "typeof 只能返回 8 个字符串，没有 'array' / 'date'");

// ---- 2. truthiness 会把 0 与 '' 当成缺失值 ----
function countUsersOnline(n: number): string {
  return n ? "online: " + n : "nobody";
}
check(countUsersOnline(0) === "nobody", "0 被 truthiness 吞掉：有人数为 0 时报 'nobody'");
check(countUsersOnline(3) === "online: 3", "非零人数正常");

function greetName(name: string | undefined): string {
  return name ? "hi " + name : "anonymous";
}
check(greetName("") === "anonymous", "空字符串也会被当作未提供");

// ---- 3. in 会沿原型链查找 ----
class Base {
  swim(): void {
    /* noop */
  }
}
class Derived extends Base {}
const derived = new Derived();
check("swim" in derived, "`in` 命中原型链上的成员，不只是自有属性");
check(Object.keys(derived).length === 0, "但 Object.keys 只看自有属性 ⇒ in 与 Object.keys 不等价");

const own = { swim: () => 0 };
check("swim" in own && Object.keys(own).length === 1, "自有属性两者都命中");

// ---- 4. instanceof 依赖 prototype 链，跨 realm 会失效 ----
check(derived instanceof Base, "instanceof 沿原型链判断");
const duck = { kind: "Fish", swim: () => 0 } as Fish;
check(!(duck instanceof Base), "结构相同的对象不是任何 class 的实例");

// ---- 5. 类型谓词不做任何运行时校验 ----
function isFishTrusted(pet: Fish | Bird): pet is Fish {
  return typeof (pet as Fish).swim === "function";
}
function isFishLying(_pet: Fish | Bird): pet is Fish {
  return true; // 撒谎也能编译：谓词不校验实现
}
const bird: Bird = { fly: () => undefined };
check(isFishTrusted(bird as Fish | Bird) === false, "诚实谓词按约定返回 false");
check(isFishLying(bird as Fish | Bird) === true, "撒谎谓词同样通过编译 ⇒ 谓词只是承诺");

let broke = false;
try {
  const lied = bird as Fish;
  isFishLying(lied);
  lied.swim();
} catch (e) {
  broke = true;
}
check(broke, "谓词撒谎导致运行时 TypeError（类型系统无法阻止）");

// ---- 6. 宽联合上的收窄在运行期的等价写法 ----
function describe(v: string | number | boolean): string {
  if (typeof v === "string") return "str:" + v.length;
  if (typeof v === "number") return "num:" + v.toFixed(1);
  return "bool:" + String(v);
}
check(describe("ab") === "str:2" && describe(1) === "num:1.0" && describe(true) === "bool:true",
  "编译期的分段收窄在运行期就是一串 typeof 分支");

if (failures > 0) {
  throw new Error("运行时断言失败 " + failures + " 条");
}
console.log("=== narrowing.ts 运行时断言全绿 ===");
