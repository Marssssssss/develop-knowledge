// EXPECT: TS2322
// 同一份「专门处理 Dog 的比较器」分别声明在方法位置与属性位置：
// strictFunctionTypes 只管后者（属性写法的函数类型），方法简写仍保持双变。
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

const asMethod: MethodStyle = dogMethod; // OK：方法参数在 strictFunctionTypes 下依旧双变
const asProperty: PropertyStyle = dogProperty; // TS2322：属性位置的参数做逆变检查

export { asMethod, asProperty };
