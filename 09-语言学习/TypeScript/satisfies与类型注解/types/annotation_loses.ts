// EXPECT: TS2322, TS2339
// 用【类型注解】约束对象：拼写错误能被抓到，但每个属性的精确类型被抬成 union。

type Colors = "red" | "green" | "blue";
type RGB = [red: number, green: number, blue: number];

// 注解成立：red / green / blue 三个 key 都在，值也都能赋给 string | RGB。
// 数组字面量受到 Record<Colors, string | RGB> 的上下文类型约束，所以能被推断成元组。
const palette: Record<Colors, string | RGB> = {
  red: [255, 0, 0],
  green: "#00ff00",
  blue: [0, 0, 255],
};

// 注解之后，palette.green 的静态类型是 string | RGB，而不是 "string"。
const asRgb: RGB = palette.green; // 反过来也不成立：string 不是 RGB
const render: number = palette.red.length; // RGB 元组有 length，OK

// 想要的信息丢了：green "可能"是元组，所以 string 的方法用不上。
const lifted: string = palette.green;
const scream: string = palette.green.toUpperCase();

export { asRgb, render, lifted, scream };
