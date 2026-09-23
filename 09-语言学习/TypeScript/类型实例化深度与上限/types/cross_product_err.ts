// EXPECT: TS2590
// 10 个字符的联合放在 5 个插值位上做笛卡尔积，结果规模 10^5 = 100000。
// 编译器在真正铺开之前先用 getCrossProductUnionSize 估大小判一次门：
//   size >= 1e5 就报 TS2590（Expression produces a union type that is too complex）。
// 这个 fixture 位于门的另一侧（拦截）。

type Digit = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9";
type IDs = `${Digit}${Digit}${Digit}${Digit}${Digit}`;

export type { IDs };
