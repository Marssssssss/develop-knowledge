// EXPECT: TS2322
// private / protected 成员把"结构相同"要求升级为"必须来自同一份声明" —— 这是 TS 结构系统里唯一的名义成分。
class Storage {
  private readonly secret = 42;
  read(): number {
    return this.secret;
  }
}
class Lookalike {
  private readonly secret = 42;
  read(): number {
    return this.secret;
  }
}
declare const lookalike: Lookalike;

const ok: Storage = new Storage(); // OK:同一个声明
const bad1: Storage = new Lookalike(); // TS2322
const bad2: Storage = lookalike; // TS2322

export { ok, bad1, bad2 };
