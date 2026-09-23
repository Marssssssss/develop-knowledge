// EXPECT: TS2427
// 手册里明说了：default export 不能被增强（只能按「导出名」增强，而 default 是保留字，
// 见 GitHub issue #14080）。实测编译器在这里给的是 TS2427。

import Fallback from "./observable";

declare module "./observable" {
  interface default {
    extra: number;
  }
}

export { Fallback };
