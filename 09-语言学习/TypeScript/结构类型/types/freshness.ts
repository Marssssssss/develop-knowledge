// EXPECT: TS2561, TS2559
// 结构类型 + 对象字面量"新鲜度"(freshness):只有直接写入的对象字面量才会被超额属性检查(Object literal may only specify known properties)。
interface SquareConfig {
  color?: string;
  width?: number;
}
declare function createSquare(config: SquareConfig): void;

// 1) 字面量实参 -> 触发超额属性检查(TS2561)。注意 width 是合法属性,只有拼错的 colour 被拦下。
createSquare({ colour: "red", width: 100 });

// 2) 先落到变量再传 -> 新鲜度已消失,不再做超额属性检查,按普通结构规则通过。
const opts = { colour: "red", width: 100 };
createSquare(opts);

// 3) 但结构规则仍然要求"至少有一个共同属性",否则报 TS2559。
const alien = { colour: "red" };
createSquare(alien);
