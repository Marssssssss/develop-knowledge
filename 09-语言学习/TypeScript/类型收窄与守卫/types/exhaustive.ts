// EXPECT: TS2322
// 穷尽性检查：default 分支赋给 never，一旦 union 新增成员，这里立刻报错。
interface Circle {
  kind: "circle";
  radius: number;
}
interface Square {
  kind: "square";
  sideLength: number;
}
interface Triangle {
  kind: "triangle";
  sideLength: number;
}
type Shape = Circle | Square | Triangle;

function area(shape: Shape): number {
  switch (shape.kind) {
    case "circle":
      return Math.PI * shape.radius ** 2;
    case "square":
      return shape.sideLength ** 2;
    default:
      const exhaustive: never = shape; // Triangle 没被处理 ⇒ 这里必须报错
      return exhaustive;
  }
}

export { area };
