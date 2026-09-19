// EXPECT: TS2339, TS2322
// 键重映射（as）：既能批量改名，也能通过产出 never 来过滤成员。
type Getters<T> = {
  [P in keyof T as `get${Capitalize<string & P>}`]: () => T[P];
};
interface Person {
  name: string;
  age: number;
}
declare const lazy: Getters<Person>;
const nm: string = lazy.getName(); // OK
const mismatch: number = lazy.getName(); // TS2322：getName 的返回类型是 string

interface Circle {
  kind: "circle";
  radius: number;
}
type RemoveKindField<T> = {
  [P in keyof T as Exclude<P, "kind">]: T[P];
};
declare const kindless: RemoveKindField<Circle>;
const radius: number = kindless.radius; // OK
const gone: string = kindless.kind; // TS2339：键已 never 过滤掉

export { nm, mismatch, radius, gone };
