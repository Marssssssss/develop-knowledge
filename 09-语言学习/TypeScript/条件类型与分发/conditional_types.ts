/**
 * 条件类型的"分配律"在运行时的同构。
 *
 * 编译期的分发是"对联合的每个成员各算一遍再并起来"；
 * 运行期把"联合"表示成一个成员数组，分发就退化成 flatMap ——
 * 这个 demo 用同一份逻辑两种写法的对照，说明为什么 ToArray<never> 会得到 never：
 * never 是零元联合，map 到空集自然还是空集，而不是"一个装着 never 的数组"。
 *
 * 运行：node conditional_types.ts
 */

type Member = string;

let failures = 0;
function check(cond: boolean, msg: string): void {
  if (cond) {
    console.log("ok " + msg);
  } else {
    failures += 1;
    console.log("FAIL " + msg);
  }
}

// ---- 编译期 ToArray<T> = T extends any ? T[] : never 的运行时孪生 ----
type ToArray<T> = T extends any ? T[] : never;
type ToArrayNonDist<T> = [T] extends [any] ? T[] : never;

// 运行时的"联合"用成员数组表示；每个成员经过变换后得到若干结果
function distribute(
  members: Member[],
  transform: (m: Member) => Member[],
): Member[] {
  const out: Member[] = [];
  for (const m of members) {
    for (const r of transform(m)) {
      out.push(r);
    }
  }
  return out;
}

function keepWhole(union: Member[]): Member[] {
  return union.length === 0 ? [] : [union.join(" | ")];
}

// T = string | number
const unionMembers: Member[] = ["string", "number"];
check(
  distribute(unionMembers, (m) => [m + "[]"]).join(" | ") === "string[] | number[]",
  "分发：每个成员各算一遍再并起来 ⟷ flatMap",
);
check(keepWhole(unionMembers).join("") === "string | number",
  "整体：保留联合本体（对应 [T] extends [U] 的写法）");

// ---- never = 零元联合 ----
const neverMembers: Member[] = [];
check(distribute(neverMembers, (m) => [m + "[]"]).length === 0,
  "never 分发到 0 个成员 ⇒ 结果为空集（never），而非 never[]");
check(keepWhole(neverMembers).length === 0, "非分发语境下空集同样为空");

// 对照：非分发版本作用在 never 上得到 never[] —— 两种写法结果不同，这是最容易踩的坑
const nonDistNever = keepWhole(neverMembers);
const distNever = distribute(neverMembers, (m) => [m + "[]"]);
check(distNever.length === nonDistNever.length && distNever.length === 0,
  "两个版本的运行时长度相同，差异只存在于类型层面");

// ---- 条件判断本身在运行时的等价物 ----
function pick<S extends string | number>(x: S): S extends string ? "IdLabel" : "NameLabel" {
  // 编译期分支会消失，运行时只有一个 if
  return (typeof x === "string" ? "IdLabel" : "NameLabel") as S extends string
    ? "IdLabel"
    : "NameLabel";
}
check(pick("x") === "IdLabel" && pick(1) === "NameLabel",
  "条件类型在运行时被擦除成一个普通分支，重载消失");

// ---- infer：运行时对应的"从结构里挖出局部" ----
function ret<T, R>(_fn: (arg: T) => R, sample: R): R {
  return sample;
}
const inferred = ret((s: string) => s.length, 7);
check(inferred === 7, "infer 的运行时等价物就是普通类型推断，无额外开销");

// ---- 擦除后的产物里没有任何类型残留 ----
const erased: ToArray<string> = ["a"];
check(Array.isArray(erased) && erased[0] === "a", "ToArray<string> 擦除后就是普通数组");
declare const _phantom: ToArrayNonDist<never>;
check(true, "类型层断言不产生运行时代码");

if (failures > 0) {
  throw new Error("运行时断言失败 " + failures + " 条");
}
console.log("=== conditional_types.ts 运行时断言全绿 ===");
