// EXPECT: TS2353
// satisfies 会做「多余属性检查」：Record<Colors, unknown> 只列出三个 key，
// 多出来的 platypus 会被拦下（`as`/断言不会）。

type Colors = "red" | "green" | "blue";

const favoriteColors = {
  red: "yes",
  green: false,
  blue: "kinda",
  platypus: false,
} satisfies Record<Colors, unknown>;

const keeps: boolean = favoriteColors.green;
export { keeps };
