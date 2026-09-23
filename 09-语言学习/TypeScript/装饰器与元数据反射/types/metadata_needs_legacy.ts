// EXPECT: TS5052
// FLAGS: --emitDecoratorMetadata
// emitDecoratorMetadata 必须配 experimentalDecorators —— 单开前者是配置错误，
// 而且是**TS5052 全局选项错误**（连行号都没有），不是某个文件里的语义错误。
// 源码侧对应 getTypeMetadata 开头那句 `if (!legacyDecorators) return void 0;`。

class C {
  x = 1;
}

export { C };
