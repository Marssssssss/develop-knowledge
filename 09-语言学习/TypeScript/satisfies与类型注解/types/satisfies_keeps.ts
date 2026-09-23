// EXPECT: OK
// satisfies 只做「校验」不改「结果类型」：既保住每个属性的精确类型，又检查 Shape。

type Colors = "red" | "green" | "blue";
type RGB = [red: number, green: number, blue: number];

const palette = {
  red: [255, 0, 0],
  green: "#00ff00",
  blue: [0, 0, 255],
} satisfies Record<Colors, string | RGB>;

// green 仍是 string
const lifted: string = palette.green;
const scream: string = palette.green.toUpperCase();

// red 仍是元组 RGB（array literal 在 satisfies 的上下文类型下被推断成元组）
const tuple: RGB = palette.red;
const first: number = palette.red[0];

// 反过来的方向也成立：string 不能塞回 RGB 的位置
type Heads = { rgb: RGB; css: string };
const h: Heads = { rgb: palette.red, css: palette.green };

export { lifted, scream, tuple, first, h };
