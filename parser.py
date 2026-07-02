"""
Парсер записів про тренування.

Підтримувані формати (регістр не важливий):
    жим лежачи 60 3х10
    жим лежачи 60кг 3x10
    тяга блока 55 4*12
    присідання 80кг 5х5
    підтягування 3х8              (без ваги = власна вага)
    жим гантелей 22 3х12,10,8      (різні повторення в підходах)

Роздільник підходів×повторень: х (укр), x (лат), *, ×
"""
import re
from dataclasses import dataclass, field


# токен виду 3х10 / 4x12 / 5*5 / 3×8
SETS_REPS_RE = re.compile(r"^(\d{1,2})\s*[хx×*]\s*([\d,]+)$", re.IGNORECASE)
# токен ваги: 60 / 60кг / 60kg / 22.5 / 22,5кг
WEIGHT_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:кг|kg|кґ)?$", re.IGNORECASE)


@dataclass
class ParsedSet:
    exercise: str
    weight: float | None          # None = власна вага
    sets: int
    reps: list[int]               # список повторень по підходах
    raw: str

    @property
    def reps_avg(self) -> float:
        return sum(self.reps) / len(self.reps) if self.reps else 0.0

    @property
    def total_reps(self) -> int:
        return sum(self.reps)

    @property
    def volume(self) -> float:
        """Тоннаж = вага × сумарні повторення (0 для власної ваги)."""
        if self.weight is None:
            return 0.0
        return self.weight * self.total_reps

    @property
    def est_1rm(self) -> float | None:
        """Оцінка 1ПМ за формулою Еплі по найкращому підходу."""
        if self.weight is None or not self.reps:
            return None
        best_reps = max(self.reps)
        return round(self.weight * (1 + best_reps / 30), 1)


def _parse_reps(reps_token: str, sets_count: int) -> list[int]:
    """'10' + 3 підходи -> [10,10,10]; '10,8,6' -> [10,8,6]."""
    parts = [int(p) for p in reps_token.split(",") if p.strip().isdigit()]
    if not parts:
        return []
    if len(parts) == 1:
        return parts * sets_count
    return parts


def parse_line(line: str) -> ParsedSet | None:
    """Парсить один рядок. Повертає None, якщо це не схоже на запис вправи."""
    line = line.strip()
    if not line:
        return None
    tokens = line.split()
    if len(tokens) < 2:
        return None

    # знаходимо токен підходи×повторення (шукаємо з кінця)
    sr_idx = None
    sr_match = None
    for i in range(len(tokens) - 1, -1, -1):
        m = SETS_REPS_RE.match(tokens[i])
        if m:
            sr_idx, sr_match = i, m
            break
    if sr_match is None:
        return None

    sets_count = int(sr_match.group(1))
    reps = _parse_reps(sr_match.group(2), sets_count)
    if not reps:
        return None
    # якщо в підходах перелічені повторення — кількість підходів = їх кількість
    if len(reps) > 1:
        sets_count = len(reps)

    # вага — токен одразу перед підходами×повтореннями, якщо він числовий
    weight = None
    name_end = sr_idx
    if sr_idx > 0:
        wm = WEIGHT_RE.match(tokens[sr_idx - 1])
        # має містити цифру (щоб "жим" не з'їло)
        if wm and any(ch.isdigit() for ch in tokens[sr_idx - 1]):
            weight = float(wm.group(1).replace(",", "."))
            name_end = sr_idx - 1

    exercise = " ".join(tokens[:name_end]).strip().lower()
    if not exercise:
        return None

    return ParsedSet(
        exercise=exercise,
        weight=weight,
        sets=sets_count,
        reps=reps,
        raw=line,
    )


def parse_message(text: str) -> list[ParsedSet]:
    """Кілька рядків = кілька вправ."""
    result = []
    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed:
            result.append(parsed)
    return result
