// EXPECT: TS2741, TS2739
// 同态映射类型：只有写成 `[P in keyof T]` 才保留修饰符（可选/只读）与容器形状（数组）；
// 一旦把键集改写为 `[P in Extract<keyof T, string>]`，可选标记丢失、数组不再真的是数组。
type Homo<T> = { [P in keyof T]: T[P] };
type NonHomo<T> = { [P in Extract<keyof T, string>]: T[P] };

interface MaybeBox {
  a?: string;
}
const homoSame: Homo<MaybeBox> = {}; // OK：可选性被原样保留
const nonHomoNow: NonHomo<MaybeBox> = {}; // TS2741：a 变成必填（属性还在，修饰符没了）

declare const homoArr: Homo<string[]>;
declare const nonHomoArr: NonHomo<string[]>;
const stillArray: string[] = homoArr; // OK：同态版本还是 string[]
const nowElem: string = homoArr[0]; // OK：数字索引还在
const notArrayAnymore: string[] = nonHomoArr; // TS2739：丢了数字索引签名

export { homoSame, nonHomoNow, stillArray, nowElem, notArrayAnymore };
