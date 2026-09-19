// EXPECT: TS2322
// TypeScript 4.7 引入的显式方差标注：out = 协变，in = 逆变，in out = 不变。
interface Animal {
  animalStuff: number;
}
interface Dog extends Animal {
  dogStuff: number;
}

type Getter<out T> = () => T;
type Setter<in T> = (value: T) => void;
interface State<in out T> {
  get: () => T;
  set: (value: T) => void;
}

declare const getterOfDog: Getter<Dog>;
const getterOfAnimal: Getter<Animal> = getterOfDog; // OK：协变，Dog 可以出现在要 Animal 的地方

declare const setterOfAnimal: Setter<Animal>;
const setterOfDog: Setter<Dog> = setterOfAnimal; // OK：逆变，能吃 Animal 的就能吃 Dog

declare const stateOfDog: State<Dog>;
const stateOfAnimal: State<Animal> = stateOfDog; // TS2322：不变，两端必须同一个 T

export { getterOfAnimal, setterOfDog, stateOfAnimal };
