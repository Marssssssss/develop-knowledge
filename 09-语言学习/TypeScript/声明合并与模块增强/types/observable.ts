// EXPECT: OK
// 「被增强」的目标模块：只有 export 出来的东西才有名字可以 patch
// （模块增强要求「按导出名」去增强，default 是保留字，见 module_augment_default.ts）。

export class Observable<T> {
  constructor(public value: T) {}
}

export default class Fallback<T> {
  constructor(public value: T) {}
}
