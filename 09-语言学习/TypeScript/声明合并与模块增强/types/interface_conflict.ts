// EXPECT: TS2717
// 接口合并的规则：非函数成员同名时类型必须一致；不一致就是 TS2717。

interface Box {
  height: number;
  width: number;
}

interface Box {
  scale: number; // 不同名的成员：合并成功
}

interface Box {
  height: string; // ❌ TS2717 同名的 height 类型与之前不同
}

declare const box: Box;
const h: number = box.height;
export { h };
