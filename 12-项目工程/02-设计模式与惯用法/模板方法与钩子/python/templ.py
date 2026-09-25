# -*- coding: utf-8 -*-
"""模板方法与钩子模型。

口径(实读源):refactoring.guru Template Method 页——
骨架在基类、子类覆写步骤不覆写结构;三类步骤(抽象/默认/空体钩子);
钩子放在关键步骤前后;官方 Cons(骨架限制、LSP 风险、步骤多难维护)
与 Relations(Template=类级静态/继承,Strategy=对象级运行时/组合;
Factory Method 是 Template Method 的特化)。
"""


class AlgorithmError(Exception):
    pass


class GameAI:
    """基类:模板方法 turn() 定死结构;步骤分三类。"""

    def template_turn(self):
        trace = []
        trace.append(self.before_build_hook())          # 钩子:空体,可不复写
        trace.append(self.collect_resources())          # 默认实现,可覆写
        trace += self.build_structures()                # 抽象:必须实现
        trace += self.build_units()
        trace.append(self.attack())                     # 默认实现
        return [t for t in trace if t]

    # ---- 默认步骤 ----
    def collect_resources(self):
        return "collect:shared"

    def attack(self):
        return "attack:default"

    # ---- 空体钩子 ----
    def before_build_hook(self):
        return ""

    # ---- 抽象步骤 ----
    def build_structures(self):
        raise NotImplementedError

    def build_units(self):
        raise NotImplementedError


class OrcsAI(GameAI):
    def build_structures(self):
        return ["build:farm", "build:barracks"]

    def build_units(self):
        return ["build:peon", "build:grunt"]


class MonstersAI(GameAI):
    """覆写默认步骤为空操作——官方点名的 LSP 风险场景。"""

    def collect_resources(self):
        return ""                                        # 压制默认实现

    def build_structures(self):
        return ["spawn:lair"]

    def build_units(self):
        return ["spawn:beast"]

    def before_build_hook(self):
        return "hook:monsters-awake"                     # 钩子扩展点


STEP_KINDS = {
    "abstract": "子类必须实现",
    "default": "基类已有默认,可覆写",
    "hook": "空体可选步骤,不复写也能跑,放在关键步骤前后",
}
