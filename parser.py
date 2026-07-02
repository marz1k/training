"""
Парсер тренування, що надсилається одним повідомленням.

Формат:
    дд.мм.рррр                    <- необовʼязковий перший рядок (дата)
    вправа: КГхПОВТОРЕННЯхПІДХОДИ
    вправа: КГхПОВТхПІДХ КГхПОВТхПІДХ   <- кілька ваг через пробіл

Приклади:
    02.07.2026
    жим лежачи: 60x10x3
    присідання: 80x5x5
    трицепс: 20x10x2 25x10x1        <- різна вага на одну вправу
    підтягування: 0x8x3             <- 0 = власна вага

Роздільник чисел: х (укр), x (лат), *, × — будь-який.
Назва вправи завжди зводиться до нижнього регістру (для збігів).
"""
import re
from dataclasses import dataclass
from datetime import datetime


# КГхПОВТхПІДХ, напр. 60x10x3 або 22.5х12х4
GROUP_RE = re.compile(r"^(\d+(?:[.,]\d+)?)[хx×*](\d{1,3})[хx×*](\d{1,2})$", re.IGNORECASE)
# дата дд.мм.рррр (також / та -, і 2-значний рік)
DATE_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})$")


@dataclass
class ParsedSet:
    exercise: str
    weight: float | None          # None = власна вага (введено як 0)
    sets: int
    reps: list[int]               # повторення по підходах (тут однакові)
    raw: str

    @property
    def total_reps(self) -> int:
        return sum(self.reps)

    @property
    def volume(self) -> float:
        if self.weight is None:
            return 0.0
        return self.weight * self.total_reps

    @property
    def est_1rm(self) -> float | None:
        if self.weight is None or not self.reps:
            return None
        return round(self.weight * (1 + max(self.reps) / 30), 1)


def parse_date(line: str) -> datetime | None:
    """Парсить рядок-дату. Повертає naive datetime на 12:00 того дня або None."""
    m = DATE_RE.match(line.strip())
    if not m:
        return None
    d, mth, y = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    try:
        return datetime(y, mth, d, 12, 0, 0)
    except ValueError:
        return None


def _parse_group(token: str) -> ParsedSet | None:
    m = GROUP_RE.match(token)
    if not m:
        return None
    weight = float(m.group(1).replace(",", "."))
    reps = int(m.group(2))
    sets = int(m.group(3))
    if reps <= 0 or sets <= 0:
        return None
    return ParsedSet(
        exercise="",                       # заповнюється у виклику
        weight=None if weight == 0 else weight,
        sets=sets,
        reps=[reps] * sets,
        raw=token,
    )


def parse_exercise_line(line: str) -> list[ParsedSet]:
    """'трицепс: 20x10x2 25x10x1' -> два ParsedSet з exercise='трицепс'."""
    if ":" not in line:
        return []
    name, rest = line.split(":", 1)
    name = name.strip().lower()
    if not name:
        return []
    out: list[ParsedSet] = []
    for token in rest.split():
        ps = _parse_group(token)
        if ps:
            ps.exercise = name
            out.append(ps)
    return out


def parse_workout(text: str) -> tuple[datetime | None, list[ParsedSet]]:
    """
    Розбирає повне повідомлення.
    Повертає (дата | None, список ParsedSet).
    Дата None означає «сьогодні» (вирішує викликач).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, []

    workout_date = None
    if parse_date(lines[0]):
        workout_date = parse_date(lines[0])
        lines = lines[1:]

    sets: list[ParsedSet] = []
    for ln in lines:
        sets.extend(parse_exercise_line(ln))
    return workout_date, sets
