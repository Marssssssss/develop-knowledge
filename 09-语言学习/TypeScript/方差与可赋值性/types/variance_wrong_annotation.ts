// EXPECT: TS2636
// 标错了方差：State 里 T 既被读（get）又被写（set），只标 out 会被编译器当场指出。
interface State<out T> {
  get: () => T;
  set: (value: T) => void;
}

export type Broken = State<string>;
