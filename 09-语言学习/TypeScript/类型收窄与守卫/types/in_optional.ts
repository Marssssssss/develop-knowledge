// EXPECT: TS2339
// `in` 收窄：optional 成员会让该成员类型同时出现在 true 与 false 两侧。
type Fish = { swim: () => void };
type Bird = { fly: () => void };
type Human = { swim?: () => void; fly?: () => void };

function move(animal: Fish | Bird | Human): void {
  if ("swim" in animal) {
    animal.swim!(); // true 分支：Fish | Human，Human 的 swim 是可选的
  } else {
    animal.swim(); // 这里 swim 一定不存在 / 可选，直接调用不被允许
  }
}

function explicit(animal: Fish | Bird | Human): void {
  if ("swim" in animal && animal.swim) {
    animal.swim(); // OK：先确认存在再调用
  }
}

export { move, explicit };
