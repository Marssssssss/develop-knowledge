// EXPECT: TS1206
// 标准装饰器（不带 --experimentalDecorators）**不允许装饰参数**。
// TS 5.0 发布说明的原话：「it does not allow decorating parameters」。
// 实测的错误是 TS1206 Decorators are not valid here.

function noop(): void {}

class C {
  greet(@noop _x: string): void {}
}

export { C };
