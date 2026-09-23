// EXPECT: OK
// 换成 Record<string, ...> 之后目标类型带索引签名，多余属性检查就失效了：
// 「值都符合某类型」可以这么写，但「不多不少正好这些 key」必须用具名联合。

type RGB = [red: number, green: number, blue: number];

const palette = {
  red: [255, 0, 0],
  green: "#00ff00",
  platypus: 1,
} satisfies Record<string, string | RGB | number>;

const red0: number = palette.red[0];
const green: string = palette.green;
export { red0, green };
