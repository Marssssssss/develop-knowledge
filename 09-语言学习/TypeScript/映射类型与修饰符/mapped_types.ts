/**
 * 映射类型在运行时的同构物。
 *
 * 编译期的 `{[P in keyof T]: X}` / `as` 重映射 / `-?` / `-readonly`，
 * 在运行期分别对应 Key 遍历改名、挑 key、补默认值、（什么都不做 —— readonly 没有运行时语义）。
 *
 * 运行：node mapped_types.ts
 */

type Homo<T> = { [P in keyof T]: T[P] };
type NonHomo<T> = { [P in Extract<keyof T, string>]: T[P] };

let failures = 0;
function check(cond: boolean, msg: string): void {
  if (cond) {
    console.log("ok " + msg);
  } else {
    failures += 1;
    console.log("FAIL " + msg);
  }
}

const person = { name: "Ada", age: 36 };

// ---- 1. 键重映射 `get${Capitalize<P>}` ⟷ 运行期重命名 ----
function toGetters(obj: Record<string, unknown>): Record<string, () => unknown> {
  const out: Record<string, () => unknown> = {};
  for (const key of Object.keys(obj)) {
    const camel = "get" + key.charAt(0).toUpperCase() + key.slice(1);
    out[camel] = () => obj[key];
  }
  return out;
}
const getters = toGetters(person);
check(typeof getters.getName === "function" && getters.getName() === "Ada",
  "as 重映射在运行期就是批量 key 改名");
check(Object.keys(getters).sort().join(",") === "getAge,getName",
  "映射结果与原对象一一对应（不含原型方法）");

// ---- 2. 用 never 过滤键 ⟷ 运行期 pick/skip ----
function omitKeys(obj: Record<string, unknown>, drop: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(obj)) {
    if (drop.indexOf(key) < 0) {
      out[key] = obj[key];
    }
  }
  return out;
}
const kindless = omitKeys({ kind: "circle", radius: 3 }, ["kind"]);
check("kind" in kindless === false && kindless.radius === 3,
  "as 子句产出 never 的 key 在运行期等价于被 omit 掉");

// ---- 3. `?` → `-?` ⟷ 运行期填默认值 ----
function fillDefaults(obj: Record<string, unknown>, defs: Record<string, unknown>): Record<string, unknown> {
  const out = Object.assign({}, defs);
  for (const key of Object.keys(obj)) {
    out[key] = obj[key];
  }
  return out;
}
const concrete = fillDefaults({ id: "u1" }, { name: "", age: 0 });
check(concrete.name === "" && concrete.age === 0,
  "-? 抹掉可选性后「缺失」不再被允许，运行期对应默认值填充");

// ---- 4. readonly ⟷ 运行期什么都没发生 ----
interface ReadonlyBox {
  readonly value: number;
}
const box: ReadonlyBox = { value: 1 };
const writable: Homo<ReadonlyBox> = box;
const stillWritable: NonHomo<ReadonlyBox> = box;
(writable as { value: number }).value = 2;
check(box.value === 2, "readonly 只是编译期约定，运行期依旧可写");
check(stillWritable.value === 2, "映射类型本身也不产生任何运行时保护");

const frozen = Object.freeze({ value: 7 });
check(Object.isFrozen(frozen), "真正冻住对象要靠 Object.freeze，与 readonly 无因果关系");

// ---- 5. 同态与非同态在运行期的差别为零 ----
const arr: string[] = ["a", "b"];
const homoRuntime: Homo<string[]> = arr;
check(homoRuntime === arr, "同态映射在运行期就是原引用本身");
check(typeof arr[Symbol.iterator] === "function",
  "Symbol.iterator 存在于运行时原型链上（类型层 NonHomo 丢的是它的类型，运行时仍在）");

if (failures > 0) {
  throw new Error("运行时断言失败 " + failures + " 条");
}
console.log("=== mapped_types.ts 运行时断言全绿 ===");
