// EXPECT: OK
// TypeScript 5.0 起，不带 --experimentalDecorators 时走的是 TC39 标准装饰器：
// 装饰器拿到的第二个参数是 context 对象，标准形式为 (value, context)。
// 这里用手册里「well-typed 版本」的写法：把 this / 参数 / 返回值分别建模。

type AnyMethod<This, Args extends unknown[], Return> = (
  this: This,
  ...args: Args
) => Return;

function loggedMethod<This, Args extends unknown[], Return>(
  target: AnyMethod<This, Args, Return>,
  context: ClassMethodDecoratorContext<This, AnyMethod<This, Args, Return>>
): AnyMethod<This, Args, Return> {
  const methodName = String(context.name);
  function replacementMethod(this: This, ...args: Args): Return {
    return target.call(this, ...args);
  }
  void methodName;
  return replacementMethod;
}

function bound<This, Args extends unknown[], Return>(
  originalMethod: AnyMethod<This, Args, Return>,
  context: ClassMethodDecoratorContext<This, AnyMethod<This, Args, Return>>
): void {
  const methodName = String(context.name);
  context.addInitializer(function (this: This): void {
    void methodName;
  });
}

class Person {
  name: string;
  constructor(name: string) {
    this.name = name;
  }

  @loggedMethod
  greet(): number {
    return this.name.length;
  }

  @bound
  measure(): number {
    return 1;
  }
}

const p = new Person("Ray");
const n: number = p.greet();

export { p, n };
