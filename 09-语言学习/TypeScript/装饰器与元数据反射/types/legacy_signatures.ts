// EXPECT: OK
// FLAGS: --experimentalDecorators
// 遗留装饰器的四种合法位置，各自的签名都不一样，写错参数个数就是 TS1329：
//   class   -> (target: Function)
//   method  -> (target: any, key: string, descriptor: PropertyDescriptor)
//   accessor-> 同 method
//   property-> (target: any, key: string)
//   param   -> (target: any, key: string | undefined, index: number)

function cls(target: Function): void {
  void target;
}
function method(_target: unknown, _key: string, _desc: PropertyDescriptor): void {}
function prop(_target: unknown, _key: string): void {}
function param(_target: unknown, _key: string | undefined, _index: number): void {}

@cls
class Legacy {
  @prop field = 1;

  @method
  greet(): void {}

  step(@param _x: string): void {}
}

const l = new Legacy();
const v: number = l.field;

export { l, v };
