// ObjCKVO.m — KVO 底层 isa-swizzling 最小复刻(demo 135)
// 用真实 runtime API 还原 Apple KVO 的核心动作:
//   addObserver → objc_allocateClassPair(objc_getClass(obj), "NSKVONotifying_X")
//   → class_addMethod 重写 setter(willChange → super setter → didChange)
//   → object_setClass(obj, 新子类)(isa 调包)
//   → 重写 -class 返回原类"瞒天过海" + _isKVOA 私有标志
//   → removeObserver → object_setClass 复原 isa(派生类不销毁,缓存复用)
// 编译(仅供参考,本仓库不执行): clang -framework Foundation ObjCKVO.m -o kvo
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>

#pragma mark - 业务类

@interface Person : NSObject {
    @public NSString *_name;   // 手写 ivar,便于演示 setter 转发
}
@property (nonatomic, assign) NSInteger age;
@end
@implementation Person
- (void)setAge:(NSInteger)age { _age = age; }   // 原始 setter(将被派生类重写)
- (NSString *)description { return [NSString stringWithFormat:@"<Person age=%ld>", _age]; }
@end

#pragma mark - 观察者(实现 NSObject KVO 回调)

@interface Observer : NSObject
@property (nonatomic, copy) NSString *name;
@end
@implementation Observer
// KVO 通知最终落到这里(真实 KVO 由 _NSSetIntValueAndNotify 驱动)
- (void)observeValueForKeyPath:(NSString *)keyPath ofObject:(id)object
                        change:(NSDictionary *)change context:(void *)context {
    NSLog(@"    [通知] 观察者 %@ 收到 %@ 变更: %@ (kind=%@)", self.name, keyPath,
          change[NSKeyValueChangeNewKey], change[@"kind"]);
}
@end

#pragma mark - mini-KVO:isa-swizzling 全流程

static NSMutableDictionary *g_derivedClasses;   // 原类名 → 派生类(真实实现同样缓存复用)
// keyPath → 观察者数组(真实实现存在 InfoTableModel,按对象+keyPath 组织)
static NSMutableDictionary *g_observers;

static void kvObservationWillChange(id obj, NSString *key) {
    NSLog(@"    [willChangeValueForKey:%@]", key);
}
static void kvObservationDidChange(id obj, NSString *key, id oldValue, id newValue) {
    NSLog(@"    [didChangeValueForKey:%@]", key);
    // 真实实现:didChange 内部同步回调所有观察者的 observeValueForKeyPath:
    for (NSDictionary *p in g_observers[key] ?: @[]) {
        id observer = p[@"observer"];
        [observer observeValueForKeyPath:key ofObject:obj
            change:@{NSKeyValueChangeNewKey: newValue, @"kind": @"1"}
                 context:NULL];
    }
}

// 派生子类的重写 setter:willChange → 父类原 setter → didChange
static void kvSetAge(id self, SEL _cmd, NSInteger newAge) {
    NSInteger oldAge = ((Person *)self)->_age;
    kvObservationWillChange(self, @"age");
    struct objc_super sup = { self, class_getSuperclass(object_getClass(self)) };
    ((void (*)(struct objc_super *, SEL, NSInteger))objc_msgSendSuper)(&sup, @selector(setAge:), newAge);
    kvObservationDidChange(self, @"age", @(oldAge), @(newAge));
}
// 重写 -class 返回原类(隐藏 isa 调包)
static Class kvClass(id self, SEL _cmd) { return class_getSuperclass(object_getClass(self)); }
// _isKVOA 私有标志:KVO 派生类实例返回 YES
static BOOL kvIsKVOA(id self, SEL _cmd) { return YES; }

static Class kvoClassFor(id object) {
    Class original = object_getClass(object);        // 真实 isa,不受 -class 掩盖影响
    NSString *derivedName = [@"NSKVONotifying_" stringByAppendingString:NSStringFromClass(original)];
    Class derived = NSClassFromString(derivedName);
    if (derived) return derived;                     // 已缓存(真实实现也复用派生类)
    // ① objc_allocateClassPair 动态派生 NSKVONotifying_XXX
    derived = objc_allocateClassPair(original, derivedName.UTF8String, 0);
    // ② 重写被观察属性的 setter
    class_addMethod(derived, @selector(setAge:), (IMP)kvSetAge, "v@:q");
    // ③ 重写 -class / _isKVOA(瞒天过海)
    class_addMethod(derived, @selector(class), (IMP)kvClass, "#@:");
    class_addMethod(derived, sel_registerName("_isKVOA"), (IMP)kvIsKVOA, "B@:");
    objc_registerClassPair(derived);
    return derived;
}

static void kvAddObserver(id object, id observer, NSString *keyPath) {
    if (!g_observers) g_observers = [NSMutableDictionary new];
    Class derived = kvoClassFor(object);
    if (object_getClass(object) != derived) {
        NSLog(@"    [addObserver] isa 调包:%@ → %@",
              NSStringFromClass(object_getClass(object)), NSStringFromClass(derived));
        object_setClass(object, derived);            // ④ 修改对象 isa 指向派生类
    }
    g_observers[keyPath] = [g_observers[keyPath] ?: @[] arrayByAddingObject:@{@"observer": observer}];
}

static void kvRemoveObserver(id object, NSString *keyPath) {
    Class derived = object_getClass(object);
    if ([NSStringFromClass(derived) hasPrefix:@"NSKVONotifying_"]) {
        object_setClass(object, class_getSuperclass(derived));  // isa 复原(派生类保留)
        NSLog(@"    [removeObserver] isa 复原 → %@", NSStringFromClass(object_getClass(object)));
    }
}

#pragma mark - main

int main(void) {
    @autoreleasepool {
        Person *p = [[Person alloc] init];
        p.age = 10;
        Observer *obs = [Observer new]; obs.name = @"obs1";

        NSLog(@"== 1. 注册前 isa == %@", NSStringFromClass(object_getClass(p)));
        NSLog(@"   [p class] = %@(尚未被掩盖)", NSStringFromClass([p class]));

        kvAddObserver(p, obs, @"age");
        NSLog(@"== 2. 注册后真实 isa == %@,-class 仍报 %@", NSStringFromClass(object_getClass(p)), NSStringFromClass([p class]));
        NSLog(@"   _isKVOA = %@", [p respondsToSelector:@selector(_isKVOA)] ? @"YES" : @"NO");

        NSLog(@"== 3. 赋值触发派生类 setter(通知链)===");
        p.age = 20;                                  // 走 kvSetAge:will→super→did→回调

        kvRemoveObserver(p, @"age");
        NSLog(@"== 4. 移除后赋值不再通知 ==");
        p.age = 30;
    }
    return 0;
}
