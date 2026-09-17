# Android · 组件生命周期

研究 Android 四大组件的状态机:Activity 的任务栈与启动模式、Fragment 的双层生命周期与 ViewModel 作用域、Service 的三种形态与进程优先级。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [Activity启动模式/](./Activity启动模式/) | launchMode 5 种(standard / singleTop / singleTask / singleInstance / singleInstancePerTask)+ Intent flags 运行时覆盖 + 任务栈/affinity |
| [Fragment生命周期与ViewModel/](./Fragment生命周期与ViewModel/) | `mState` 9 态状态机(含 `AWAITING_*` 过渡态)、视图生命周期 vs Fragment 生命周期、ViewModel 作用域与 `viewModelScope` 清理顺序 |
| [Service三种形态/](./Service三种形态/) | started / bound / foreground、`onStartCommand` 返回值与重建策略、`stopSelf(startId)` 排序语义、前台服务权限与 type |

## 共同主线

| 组件 | 状态由谁驱动 | 销毁的判定依据 |
| --- | --- | --- |
| Activity | `ActivityTaskManager`(任务栈) | `finish()` / 被清栈 / 进程死亡 |
| Fragment | `FragmentStateManager`(`mState` 逐级升降) | 从事务中移除;进返回栈只销毁**视图** |
| Service | `ActiveServices`(started 计数 + 连接计数) | `started == false` 且 `connections == 0` |

## 待研究

- [ ] `FragmentStateManager` 的状态推进与副作用执行顺序(源码级逐步复刻)
- [ ] `ViewModelStore` 在配置变更中的保留机制(`NonConfigurationInstances` 传递路径)
- [ ] `ProcessLifecycleOwner` 与应用级前台/后台判定
- [ ] `SavedStateHandle` 与进程死亡恢复(`onSaveInstanceState` 的 Bundle 传递链)
- [ ] Activity 的 `configChanges` 与热重载(`onConfigurationChanged` 覆盖哪些场景)
- [ ] Service 与 `ForegroundService` 在 Android 14+ 的启动限制矩阵(按 type 分类)
- [ ] `ContentProvider` 的生命周期与 `Application.onCreate` 之前被调用的问题
- [ ] `BroadcastReceiver` 的静态注册限制与 `goAsync`
