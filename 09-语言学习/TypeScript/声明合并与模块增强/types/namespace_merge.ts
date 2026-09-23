// EXPECT: TS2304
// namespace 合并：导出成员会并入同一个命名空间，但**未导出**的成员只在写它的
// 那一个 namespace 块里可见 —— 合并之后，另一个块看不到它。

namespace Animal {
  let haveMuscles = true;

  export function animalsHaveMuscles(): boolean {
    return haveMuscles; // ✅ 与 haveMuscles 同在一个 un-merged 块里
  }
}

namespace Animal {
  export function doAnimalsHaveMuscles(): boolean {
    return haveMuscles; // ❌ TS2304 Cannot find name 'haveMuscles'
  }
}

export { Animal };
