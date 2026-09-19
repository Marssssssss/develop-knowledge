// EXPECT: TS18048, TS2339
// 控制流分析的边界：它沿语法路径前进，不建模副作用/别名；只在跨函数体（闭包）时保守起来。
interface Box {
  value?: string;
}
declare function maybeMutate(box: Box): void;

// 1) 函数调用**不会**重置属性访问的收窄 —— 编译器不假设别人会改这个对象
function readsThroughObject(box: Box): number {
  if (box.value !== undefined) {
    maybeMutate(box); // 这个调用完全可能把 value 置回 undefined
    return box.value.length; // 通过：流分析不建模被调用函数的副作用
  }
  return 0;
}

// 2) 闭包里的读取会被保守处理 —— 回调何时执行无从得知
function capturedBox(box: Box): () => number {
  if (box.value !== undefined) {
    return () => box.value.length; // TS18048：跨函数体后收窄失效
  }
  return () => 0;
}

// 3) 显式重新赋值则立刻回到声明类型
function reassigned(v: string | number): number {
  let cur: string | number = v;
  if (typeof cur === "string") {
    cur = v; // 重新赋值 ⇒ 收窄结果被丢弃
    return cur.length;
  }
  return 0;
}

export { readsThroughObject, capturedBox, reassigned };
