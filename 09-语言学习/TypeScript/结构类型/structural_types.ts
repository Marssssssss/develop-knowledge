/**
 * 结构类型：编译期的约束如何在运行期彻底消失。
 *
 * 这里刻意把所有"类型层面的结论"都用可运行的 JS 行为反证一遍：
 * 不符合 runtime API 的写法（如装饰/断言 imposition）不会出现，
 * 以此说明 TypeScript 的结构类型系统只活在编译阶段。
 *
 * 运行：node structural_types.ts   （Node >= 22.6 内置类型擦除）
 */

interface Pet {
  name: string;
}

interface DeepPet {
  owner: { name: string; vip: boolean };
}

let failures = 0;
function check(cond: boolean, msg: string): void {
  if (cond) {
    console.log("ok " + msg);
  } else {
    failures += 1;
    console.log("FAIL " + msg);
  }
}

// ---- 1. 结构检查只看 target 的成员，多余的成员既不参与也不被剔除 ----
function greet(pet: Pet): string {
  return "Hello, " + pet.name;
}

const dog = { name: "Lassie", owner: "Rudd Weatherwax" };
check(greet(dog) === "Hello, Lassie", "多余成员不影响实参结构兼容");

// 超额属性检查是"编译期新鲜度"规则：同一个人写错的属性在运行时照样就在对象里
check("owner" in dog, "多出的 owner 成员在运行时真实存在（编译期检查不会裁剪对象）");

const misspelled = { name: "Rex", colour: "red" };
check((misspelled as Record<string, unknown>).colour === "red",
  "对象字面量的额外属性运行时存在，靠新鲜度检查在编译期拦下");

// ---- 2. 结构递归：成员逐层深比较，缺失只在访问时暴露为 undefined ----
const shallow: DeepPet = { owner: { name: "Ada", vip: true } };
check(shallow.owner.vip === true, "递归成员照样按结构检查通过");

const partiallyBroken = { owner: {} } as unknown as DeepPet;
check(partiallyBroken.owner.vip === undefined,
  "断言绕过了递归结构检查，运行时读到 undefined（类型系统救不了断言）");

// ---- 3. private 是编译期概念：运行时的属性完全公开 ----
class Storage {
  private readonly secret = 42;
  read(): number {
    return this.secret;
  }
}

const storage = new Storage();
check(storage.read() === 42, "private 成员在实例方法内可读");
check((storage as unknown as { secret: number }).secret === 42,
  "private 成员运行时可用下标/断言直接读到（编译期约束在运行期消失）");
check(Object.keys(storage).indexOf("secret") >= 0,
  "private 字段在运行时是普通自有属性，Object.keys 可见");

// ---- 4. branding：编译期不兼容，运行期零痕迹 ----
declare const brandKey: unique symbol;
type UserId = string & { readonly [brandKey]: "UserId" };
type OrderId = string & { readonly [brandKey]: "OrderId" };

const rawId = "u-1";
const uid = rawId as UserId;
const oid = rawId as OrderId;
check(uid === oid, "两个互斥 brand 的变量运行时完全相同（brand 不产生任何包装）");
check(typeof uid === "string" && JSON.stringify(uid) === '"u-1"',
  "brand 类型擦除后就是原始 string");
check(Object.getOwnPropertySymbols(Object(rawId)).length === 0,
  "string 上没有任何 symbol 字段，序列化/反序列化无法还原名义身份");

// 跨越运行时边界时 brand 立即失效：任何外部输入都只能靠断言"宣称自己是" brand
const fromWire = JSON.parse('"o-1"') as OrderId;
check(fromWire === "o-1", "反序列化产物被断言成 OrderId，编译期之外无人校验");

// ---- 5. 结构兼容带来的经典误吸：多余成员不会被结构检查排除 ----
interface Config {
  timeout: number;
}
function apply(c: Config): number {
  return c.timeout;
}
const misleading = { timeout: 1, retries: -5 };
check(apply(misleading) === 1, "多余成员不参与检查，语义错误成员同样能通过");

if (failures > 0) {
  throw new Error("运行时断言失败 " + failures + " 条");
}
console.log("=== structural_types.ts 运行时断言全绿 ===");
