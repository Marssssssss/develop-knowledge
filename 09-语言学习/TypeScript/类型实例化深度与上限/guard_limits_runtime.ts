// 编译器的三道「编译期递归护栏」在本机 TypeScript 5.6.3 的 tsc.js 里长这样：
//   instantiateTypeWithAlias: instantiationDepth === 100 || instantiationCount >= 5e6  -> TS2589
//   getConditionalType      : tailCount === 1e3                                        -> TS2589
//   checkCrossProductUnion  : size >= 1e5                                              -> TS2590
//   removeSubtypes          : count === 1e5 && estimatedCount > 1e6                    -> TS2590
// 这一段把这些常量搬过来做可执行断言。

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

const DEPTH_LIMIT = 100;
const COUNT_LIMIT = 5_000_000;
const TAIL_LIMIT = 1000;
const CROSS_LIMIT = 100_000;

// ---- 1. instantiateTypeWithAlias 的护栏 ----
function depthHit(depth: number, count: number): boolean {
  return depth === DEPTH_LIMIT || count >= COUNT_LIMIT;
}
check("depth guard fires exactly at 100", !depthHit(99, 0) && depthHit(100, 0));
check("count guard fires exactly at 5e6", !depthHit(0, 4_999_999) && depthHit(0, 5_000_000));
check("depth guard is == not >=", !depthHit(101, 0));

// ---- 2. getConditionalType 的尾调用计数 ----
function tailHit(tailCount: number): boolean {
  return tailCount === TAIL_LIMIT;
}
check("tail guard fires exactly at 1e3", !tailHit(999) && tailHit(1000));
check("tail guard uses === so only exactly 1000 trips", tailHit(1001) === false);

// canTailRecurse 里只有 `if (newRoot.aliasSymbol) tailCount++`：
// 尾调用必须落在「有名字的类型别名」上才会计数，内联的条件类型不会。
function countedSteps(isAliased: boolean, steps: number): number {
  return isAliased ? steps : 0;
}
check("inlined tail call never increments tailCount", countedSteps(false, 5000) === 0);
check("aliased tail call counts every step", countedSteps(true, 1000) === TAIL_LIMIT);

// ---- 3. checkCrossProductUnion 的大小判定 ----
// getCrossProductUnionSize：联合乘以成员数、never 直接归零
function crossProductSize(sizes: (number | "never")[]): number {
  let n = 1;
  for (const s of sizes) {
    if (s === "never") return 0;
    n *= s;
  }
  return n;
}
function crossHit(sizes: (number | "never")[]): boolean {
  return crossProductSize(sizes) >= CROSS_LIMIT;
}
check("never anywhere collapses the whole product to 0",
  crossProductSize([10, "never", 10]) === 0);
check("10^4 passes", crossProductSize([10, 10, 10, 10]) === 10_000 && !crossHit([10, 10, 10, 10]));
check("10^5 trips the guard", crossProductSize([10, 10, 10, 10, 10]) === 100_000 && crossHit([10, 10, 10, 10, 10]));
check("316^2 = 99856 just passes", crossProductSize([316, 316]) === 99_856 && !crossHit([316, 316]));
check("317^2 = 100489 just trips", crossProductSize([317, 317]) === 100_489 && crossHit([317, 317]));

// ---- 4. removeSubtypes 的估算式护栏 ----
// count === 1e5 时先估一遍「全跑完会有多少」，只有估算值 > 1e6 才报错
function removeSubtypesTrips(count: number, i: number, len: number): boolean {
  if (count === 100_000) {
    const estimatedCount = (count / (len - i)) * len;
    return estimatedCount > 1_000_000;
  }
  return false;
}
check("estimated size exactly 1e6 does not trip (strict >)",
  !removeSubtypesTrips(100_000, 90_000, 100_000)); // 1e5 / 1e4 * 1e5 = 1e6
check("estimated size 1e7 trips",
  removeSubtypesTrips(100_000, 99_000, 100_000)); // 1e5 / 1e3 * 1e5 = 1e7

// ---- 5. 非尾递归与尾递归在同一输入下的代价差 ----
// 实测：字符数 48 放行 / 49 报警 ⇒ 每个字符约带来 2 次实例化再加常数开销
function nonTailInstantiations(chars: number): number {
  return 2 * chars + 3;
}
check("non-tail 48 chars stays under the depth guard", nonTailInstantiations(48) < DEPTH_LIMIT);
check("non-tail 49 chars crosses it", nonTailInstantiations(49) >= DEPTH_LIMIT);
check("tail-recursive version scales with tailCount instead", tailHit(999) === false && tailHit(1000));

console.log("runtime: pass=" + pass + " fail=" + fail);
if (fail > 0) process.exit(1);
