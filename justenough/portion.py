"""份量模型：把「几个人、什么食量」换算成一顿饭的目标能量区间。

取值依据（写进 README，便于评审复核）：

* 成人一餐按中国居民膳食指南中 2000–2200 kcal/日 的 30%–37% 取整，落在 620–820 kcal；
* 青少年取 760–960 kcal（生长发育期能量需求更高）；
* 儿童（6–12 岁）取 380–520 kcal；
* 食量档位再乘系数：少 0.82 / 正常 1.00 / 大 1.20。

区间而不是单点，是因为「刚好」本来就该是一个带容差的判断；模型只负责把
「点多了」定义清楚，不替人做医疗或营养建议。
"""

from __future__ import annotations

from dataclasses import dataclass, field

KIND_MID = {"adult": 720.0, "teen": 860.0, "child": 450.0}
APPETITE_FACTOR = {"light": 0.82, "normal": 1.0, "big": 1.2}
TOLERANCE = 0.10  # 目标区间 ±10%

KIND_LABEL = {"adult": "成人", "teen": "青少年", "child": "儿童"}
APPETITE_LABEL = {"light": "食量小", "normal": "正常", "big": "食量大"}


@dataclass(frozen=True)
class DinerGroup:
    """一组条件相同的就餐者。"""

    count: int = 1
    kind: str = "adult"
    appetite: str = "normal"
    avoid: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError("人数必须大于 0")
        if self.kind not in KIND_MID:
            raise ValueError(f"未知人群类型：{self.kind}（可选 {', '.join(KIND_MID)}）")
        if self.appetite not in APPETITE_FACTOR:
            raise ValueError(f"未知食量档位：{self.appetite}（可选 {', '.join(APPETITE_FACTOR)}）")

    @property
    def label(self) -> str:
        text = f"{KIND_LABEL[self.kind]}×{self.count}（{APPETITE_LABEL[self.appetite]}）"
        if self.avoid:
            text += f" 忌口:{'/'.join(self.avoid)}"
        return text

    @property
    def per_person_mid(self) -> float:
        return KIND_MID[self.kind] * APPETITE_FACTOR[self.appetite]


@dataclass(frozen=True)
class Target:
    lo: float
    mid: float
    hi: float

    def describe(self) -> str:
        return f"{self.lo:.0f}–{self.hi:.0f} kcal"


def target_for(group: DinerGroup) -> Target:
    mid = group.per_person_mid * group.count
    return Target(lo=round(mid * (1 - TOLERANCE)), mid=round(mid), hi=round(mid * (1 + TOLERANCE)))


def total_target(groups: list[DinerGroup] | tuple[DinerGroup, ...]) -> Target:
    if not groups:
        raise ValueError("至少需要一组就餐者")
    targets = [target_for(group) for group in groups]
    return Target(
        lo=sum(t.lo for t in targets),
        mid=sum(t.mid for t in targets),
        hi=sum(t.hi for t in targets),
    )


def waste_penalty(kcal: float, target: Target) -> float:
    """把「离目标区间有多远」折算成 0~∞ 的惩罚值。

    点多了（剩食）比点少了更重：反浪费是本项目的主目标，因此 1.2 vs 0.8。
    """
    if target.hi <= 0:
        return 0.0
    if kcal > target.hi:
        return 1.2 * (kcal - target.hi) / target.hi
    if kcal < target.lo:
        return 0.8 * (target.lo - kcal) / max(target.lo, 1.0)
    return 0.05 * abs(kcal - target.mid) / max(target.mid, 1.0)
