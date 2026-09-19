// EXPECT: TS2322
// infer：从被匹配的类型结构里"挖"出局部类型变量；重载函数只取最后一个签名。
type Flatten<T> = T extends Array<infer Item> ? Item : T;
declare const flat: Flatten<string[]>;
const s: string = flat; // OK：string[] → string

type GetReturnType<T> = T extends (...args: never[]) => infer R ? R : never;

// 多调用签名时，推断来自**最后一个**签名（官方原文：the last signature），而非重载解析
declare function stringOrNum(x: string): number;
declare function stringOrNum(x: number): string;
declare function stringOrNum(x: string | number): string | number;

type OverloadedReturn = GetReturnType<typeof stringOrNum>;
declare const rt: OverloadedReturn;
const last: string | number = rt; // OK：最后一个签名返回 string | number
const first: number = rt; // TS2322：若取自首个签名，这里就不会报错

export { s, last, first };
