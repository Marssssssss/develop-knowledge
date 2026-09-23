// EXPECT: TS2300
// 不被允许的合并：class 既不能与 class 合，也不能与 variable 合。
// 想要类似效果要走 mixin。

class Album {
  label = "";
}

class Album {
  artist = ""; // ❌ TS2300 Duplicate identifier 'Album'
}

class Track {
  title = "";
}

var Track: number = 1; // ❌ TS2300 Duplicate identifier 'Track'

// 反过来：class 与 interface 是可以合并的（interface 只贡献类型侧成员）
class Player {
  play(): void {}
}
interface Player {
  volume: number;
}
const p = new Player();
const v: number = p.volume; // ✅ 合并出来的实例成员

export { Album, Track, v };
