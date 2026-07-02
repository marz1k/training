"""
Побудова графіків прогресу через matplotlib (backend Agg — без GUI).

build_progress_chart() повертає PNG у вигляді bytes, готовий надіслати
у Telegram як фото. Дані агрегуються по днях: для вправ з вагою беремо
найкращу вагу й найкращу оцінку 1ПМ за день, для власної ваги — макс.
повторень у підході.
"""
import io
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")  # важливо: жодного GUI на сервері
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# темна, спокійна палітра
_BG = "#0f1116"
_FG = "#e6e6e6"
_GRID = "#2a2d36"
_ACCENT = "#4fa3ff"
_ACCENT2 = "#ff9f43"


def _reps_per_set(e) -> int:
    return e.total_reps // e.sets if e.sets else e.total_reps


def build_progress_chart(exercise: str, entries: list, tz: ZoneInfo) -> bytes | None:
    """
    entries — записи по одній вправі в хронологічному порядку (зростання дати).
    Повертає PNG (bytes) або None, якщо малювати нема з чого.
    """
    if not entries:
        return None

    weighted = any(e.weight is not None for e in entries)

    # агрегація по календарних днях (локальний час)
    by_day_weight: dict[datetime, float] = {}
    by_day_1rm: dict[datetime, float] = {}
    by_day_reps: dict[datetime, int] = {}

    for e in entries:
        day = e.created_at.astimezone(tz).date()
        if e.weight is not None:
            by_day_weight[day] = max(by_day_weight.get(day, 0), e.weight)
            if e.est_1rm:
                by_day_1rm[day] = max(by_day_1rm.get(day, 0), e.est_1rm)
        by_day_reps[day] = max(by_day_reps.get(day, 0), _reps_per_set(e))

    fig, ax = plt.subplots(figsize=(9, 5), dpi=130)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    if weighted and by_day_weight:
        days = sorted(by_day_weight)
        weights = [by_day_weight[d] for d in days]
        ax.plot(days, weights, marker="o", color=_ACCENT, linewidth=2.2,
                markersize=6, label="Робоча вага, кг", zorder=3)

        if by_day_1rm:
            d1 = sorted(by_day_1rm)
            v1 = [by_day_1rm[d] for d in d1]
            ax.plot(d1, v1, marker="^", color=_ACCENT2, linewidth=1.6,
                    markersize=5, linestyle="--", label="Оцінка 1ПМ, кг", zorder=2)

        # підписи значень над точками робочої ваги
        for d, w in zip(days, weights):
            ax.annotate(f"{w:g}", (d, w), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8, color=_FG)
        ylabel = "Вага, кг"
    else:
        # власна вага — малюємо повторення
        days = sorted(by_day_reps)
        reps = [by_day_reps[d] for d in days]
        ax.plot(days, reps, marker="o", color=_ACCENT, linewidth=2.2,
                markersize=6, label="Повторень у підході", zorder=3)
        for d, r in zip(days, reps):
            ax.annotate(f"{r}", (d, r), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8, color=_FG)
        ylabel = "Повторення"

    # оформлення
    ax.set_title(f"Прогрес: {exercise}", color=_FG, fontsize=14, pad=14, fontweight="bold")
    ax.set_ylabel(ylabel, color=_FG, fontsize=11)
    ax.grid(True, color=_GRID, linewidth=0.7, alpha=0.8)
    ax.tick_params(colors=_FG, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(_GRID)

    # формат дат на осі X
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    if len(days) > 1:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=0, ha="center")

    leg = ax.legend(facecolor=_BG, edgecolor=_GRID, labelcolor=_FG, fontsize=9, loc="best")
    if leg:
        leg.get_frame().set_alpha(0.9)

    # трохи повітря зверху, щоб підписи не обрізались
    ymin, ymax = ax.get_ylim()
    ax.set_ylim(ymin, ymax + (ymax - ymin) * 0.12)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_volume_chart(entries: list, tz: ZoneInfo, days: int) -> bytes | None:
    """Стовпчиковий графік тоннажу (вага×повторення) по днях за останні `days`."""
    if not entries:
        return None

    by_day: dict = defaultdict(float)
    for e in entries:
        day = e.created_at.astimezone(tz).date()
        by_day[day] += e.volume

    if not any(by_day.values()):
        return None

    day_list = sorted(by_day)
    vols = [by_day[d] for d in day_list]

    fig, ax = plt.subplots(figsize=(9, 5), dpi=130)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    bars = ax.bar(day_list, vols, color=_ACCENT, width=0.7, zorder=3)
    for d, v in zip(day_list, vols):
        if v > 0:
            ax.annotate(f"{v/1000:.1f}т" if v >= 1000 else f"{v:g}",
                        (d, v), textcoords="offset points", xytext=(0, 5),
                        ha="center", fontsize=8, color=_FG)

    total = sum(vols)
    ax.set_title(f"Тоннаж за {days} днів  ·  сума {total/1000:.1f} т",
                 color=_FG, fontsize=14, pad=14, fontweight="bold")
    ax.set_ylabel("Тоннаж, кг", color=_FG, fontsize=11)
    ax.grid(True, axis="y", color=_GRID, linewidth=0.7, alpha=0.8)
    ax.tick_params(colors=_FG, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=0, ha="center")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _daily_max_weight(entries: list, tz: ZoneInfo) -> tuple[list, list]:
    by_day: dict = {}
    for e in entries:
        if e.weight is None:
            continue
        day = e.created_at.astimezone(tz).date()
        by_day[day] = max(by_day.get(day, 0), e.weight)
    days = sorted(by_day)
    return days, [by_day[d] for d in days]


def build_compare_chart(name1: str, entries1: list,
                        name2: str, entries2: list, tz: ZoneInfo) -> bytes | None:
    """Дві вправи на одному графіку: робоча вага по датах."""
    d1, w1 = _daily_max_weight(entries1, tz)
    d2, w2 = _daily_max_weight(entries2, tz)
    if not d1 and not d2:
        return None

    fig, ax = plt.subplots(figsize=(9, 5), dpi=130)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    if d1:
        ax.plot(d1, w1, marker="o", color=_ACCENT, linewidth=2.2,
                markersize=6, label=name1, zorder=3)
    if d2:
        ax.plot(d2, w2, marker="s", color=_ACCENT2, linewidth=2.2,
                markersize=6, label=name2, zorder=3)

    ax.set_title("Порівняння вправ", color=_FG, fontsize=14, pad=14, fontweight="bold")
    ax.set_ylabel("Робоча вага, кг", color=_FG, fontsize=11)
    ax.grid(True, color=_GRID, linewidth=0.7, alpha=0.8)
    ax.tick_params(colors=_FG, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=0, ha="center")
    leg = ax.legend(facecolor=_BG, edgecolor=_GRID, labelcolor=_FG, fontsize=10, loc="best")
    if leg:
        leg.get_frame().set_alpha(0.9)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
