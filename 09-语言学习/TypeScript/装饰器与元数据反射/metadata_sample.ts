// 这个文件不参与 types/ 的错误码比对，它专门用来测两件事：
//   1. `--emitDecoratorMetadata` 在每种成员上挂了哪几个 key，以及类型是怎么序列化的；
//   2. 同一个成员上叠多个装饰器时，「求值」与「应用」的先后顺序。
// 由 selfcheck.py 编译成 JS 后跑起来（需要 --experimentalDecorators）。

export interface Iface { a: number }

export class Ref {
  tag: string;
  constructor(tag = "ref") {
    this.tag = tag;
  }
}

export const evalLog: string[] = [];

function track(): PropertyDecorator &
  MethodDecorator &
  ParameterDecorator &
  ClassDecorator {
  const result = (): void => {};
  return result as PropertyDecorator &
    MethodDecorator &
    ParameterDecorator &
    ClassDecorator;
}

// 两个「有记忆」的方法装饰器：工厂被调用一次记 eval，返回的装饰器被调用一次记 apply
function first(): MethodDecorator {
  evalLog.push("eval:first");
  return ((_t: object, _k: string | symbol, _d: PropertyDescriptor): void => {
    evalLog.push("apply:first");
  }) as MethodDecorator;
}

function second(): MethodDecorator {
  evalLog.push("eval:second");
  return ((_t: object, _k: string | symbol, _d: PropertyDescriptor): void => {
    evalLog.push("apply:second");
  }) as MethodDecorator;
}

class NoCtor {
  @track() plain = 1;
}

class Sample {
  // —— 标量 ——
  @track() s: string = "";
  @track() n: number = 0;
  @track() b: boolean = false;
  @track() lit42: 42 = 42;
  @track() negNum: -1 = -1;
  @track() boolTrue: true = true;
  @track() nul: null = null;

  // —— 复合类型 ——
  @track() arr: number[] = [];
  @track() tup: [number, string] = [0, ""];
  @track() fn: (a: number) => void = () => {};
  @track() ctorType: new () => Ref = Ref;
  @track() objLit: { a: number } = { a: 0 };

  // —— 会降级成 Object 的那一批 ——
  @track() anyV: any = 1;
  @track() unknownV: unknown = 1;
  @track() typeLit: Iface = { a: 1 };
  @track() idxAcc: Iface["a"] = 1;
  @track() mapped: Record<string, number> = {};
  @track() typeQuery: typeof Ref = Ref;
  @track() thisType: this = this;

  // —— 联合与交叉 ——
  @track() sameUnion: "a" | "b" = "a";
  @track() neverUnion: string | never = "";
  @track() heteroUnion: string | number = 0;
  @track() anyUnion: string | any = "";
  @track() unknownUnion: string | unknown = "";
  @track() withVoid: string | void = "";
  @track() inter: { a: 1 } & { b: 2 } = { a: 1, b: 2 };

  // —— 剩余分支 ——
  @track() vd: void = undefined;
  @track() nvr: never = undefined as never;
  @track() big: bigint = 1n;
  @track() sym: symbol = Symbol("x");
  @track() tplLit: `id-${string}` = "id-1";
  @track() readonlyArr: readonly number[] = [];
  @track() classRef: Ref = new Ref();
  @track() noAnnotation = 1;

  // —— 方法与访问器 ——
  @track() plainMethod(): void {}
  @track() typedMethod(a: string, b: number): boolean {
    void a;
    void b;
    return true;
  }
  @track() get getter(): number {
    return 1;
  }
  @track() set setter(v: string) {
    void v;
  }

  constructor(@track() dep: Ref) {
    void dep;
  }
}

class Stacked {
  @first()
  @second()
  m(): void {}
}

export { NoCtor, Sample, Stacked, track, first, second };
