// EXPECT: TS2322
// 分配律：裸类型参数上的条件类型会把联合"拆开逐个算";套上 [T] 就退化成整体比较。
type ToArray<T> = T extends any ? T[] : never;
type ToArrayNonDist<T> = [T] extends [any] ? T[] : never;

declare const v: ToArray<string | number>;
const positive: string[] | number[] = v; // OK：分发后联合被推到外层

// 数组是协变的，所以 `ToArray<...> → (string|number)[]` 方向两者都通过，没有鉴别力；
// 真正能区分的是**反方向**：把混合数组塞回结果类型。
declare const mixed: (string | number)[];
const rejected: ToArray<string | number> = mixed; // TS2322：分发结果只接受"纯"数组
const accepted: ToArrayNonDist<string | number> = mixed; // OK：非分发版本就是混合数组

// never 是"零元联合" —— 分发到零个成员上，结果自然是 never，而不是 never[]
declare const hole: ToArray<never>;
const emptyArrayIntoIt: ToArray<never> = []; // TS2322：连空数组都塞不进去 ⇒ 结果是 never 本身

declare const u: unknown;
const fromUnknown: ToArray<never> = u; // TS2322：unknown 也不可赋给它 ⇒ 它不是 any/unknown

const intoNonDist: ToArrayNonDist<never> = []; // OK：非分发版本确实是 never[]

export { positive, rejected, accepted, emptyArrayIntoIt, fromUnknown, intoNonDist };
