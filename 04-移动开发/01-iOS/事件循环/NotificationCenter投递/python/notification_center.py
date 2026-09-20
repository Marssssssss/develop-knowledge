"""NotificationCenter 的投递语义,可执行模型。

模型口径(全部对应 Apple 官方文档,见 README 参考资料):
* addObserver(forName:object:queue:using:) 返回不透明 observer,**通知中心强持有它**;
* 通知中心**会拷贝 block**,并强持有这份拷贝,直到 removeObserver;
* queue 为 nil 时,block 在**投递线程上同步执行**;queue 非 nil 时排进那个 OperationQueue;
* name / object 为 nil 表示「不筛选」;两个都给就要两个都匹配;
* 一个通知命中多个 observer block 时,这些 block **可以并发执行**;
* 必须在 addObserver 涉及的任何对象被释放**之前** removeObserver;
* 当 self 强持有 observer 时,block 里要用 weak self 避免 retain cycle。
"""

import types
import weakref
import gc


class Notification:
    def __init__(self, name, obj=None, user_info=None):
        self.name = name
        self.object = obj
        self.user_info = user_info or {}

    def __repr__(self):
        return f"Notification({self.name})"


class Token:
    """addObserver 返回的「不透明 observer 对象」。"""

    _seq = 0

    def __init__(self):
        Token._seq += 1
        self.id = Token._seq

    def __repr__(self):
        return f"<observer#{self.id}>"


class Observer:
    def __init__(self, name, obj, queue, block, token):
        self.name = name
        self.object = obj
        self.queue = queue
        self.block = block
        self.token = token


def copy_block(block):
    """通知中心会「拷贝」block —— 模型用重建函数对象来体现身份不同。"""
    return types.FunctionType(block.__code__, block.__globals__, block.__name__,
                              block.__defaults__, block.__closure__)


class OperationQueue:
    def __init__(self, label):
        self.label = label
        self.pending = []

    def __repr__(self):
        return f"<queue:{self.label}>"


class NotificationCenter:
    def __init__(self, label="default"):
        self.label = label
        self._observers = []
        self._hold = []                 # 强持有:observer token 与 block 的拷贝
        self._queues = {}

    # -- 注册
    def add_observer(self, name=None, obj=None, queue=None, using=None):
        token = Token()
        obs = Observer(name, obj, queue, copy_block(using), token)
        self._observers.append(obs)
        self._hold.append(obs)          # 强持有,直到 removeObserver
        return token

    def remove_observer(self, token):
        before = len(self._observers)
        self._observers = [o for o in self._observers if o.token is not token]
        self._hold = [o for o in self._hold if o.token is not token]
        return before - len(self._observers)

    def observer_count(self):
        return len(self._observers)

    # -- 投递
    def post(self, name, obj=None, user_info=None, thread="main"):
        """同步部分:queue 为 nil 的 block 当场在「投递线程」上跑。"""
        delivered = []
        snapshot = list(self._observers)     # 投递期间增删不影响本轮
        for obs in snapshot:
            if not self._matches(obs, name, obj):
                continue
            note = Notification(name, obj, user_info)
            if obs.queue is None:
                obs.block(note)
                delivered.append((obs.token, thread))
            else:
                self._queues.setdefault(obs.queue, []).append((obs, note))
        return delivered

    @staticmethod
    def _matches(obs, name, obj):
        if obs.name is not None and obs.name != name:
            return False
        if obs.object is not None and obs.object is not obj:
            return False
        return True

    def pending(self, queue):
        return len(self._queues.get(queue, []))

    def drain(self, queue):
        """OperationQueue 开始执行排队的 block。"""
        out = []
        for obs, note in self._queues.pop(queue, []):
            obs.block(note)
            out.append(obs.token)
        return out


class Owner:
    """演示 strong self / weak self 的持有关系差异。"""

    def __init__(self, center, name="N", weak_self=False):
        self.hits = []
        self.center = center
        if weak_self:
            ref = weakref.ref(self)

            def block(note):
                owner = ref()
                if owner is not None:
                    owner.hits.append(note.name)
        else:
            def block(note):
                self.hits.append(note.name)
        self.token = center.add_observer(name=name, using=block)
