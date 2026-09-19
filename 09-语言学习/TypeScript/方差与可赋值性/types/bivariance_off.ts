// EXPECT: OK
// 与 bivariance_strict.ts 逐字相同的代码，只是关掉 strictFunctionTypes
// （selfcheck 以 --strictFunctionTypes false 编译本文件）。
// 期望：零错误 ⇒ 上面那条 TS2322 确实由该开关造成。
interface Animal {
  animalStuff: number;
}
interface Dog extends Animal {
  dogStuff: number;
}

interface MethodStyle {
  compare(a: Animal, b: Animal): number;
}
interface PropertyStyle {
  compare: (a: Animal, b: Animal) => number;
}

declare const dogMethod: { compare(a: Dog, b: Dog): number };
declare const dogProperty: { compare: (a: Dog, b: Dog) => number };

const asMethod: MethodStyle = dogMethod;
const asProperty: PropertyStyle = dogProperty; // 关掉开关后放行

export { asMethod, asProperty };
