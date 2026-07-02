"""
Бот-трекер тренувань на aiogram 3.x.

Формат запису тренування (одне повідомлення):
    02.07.2026                 <- дата (необовʼязкова, за замовч. сьогодні)
    жим лежачи: 60x10x3        <- вправа: вагаХповторенняХпідходи
    присідання: 80x5x5
    трицепс: 20x10x2 25x10x1   <- різна вага через пробіл
    підтягування: 0x8x3        <- 0 = власна вага
"""
import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, html
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, BufferedInputFile, InputMediaPhoto,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
from parser import parse_workout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Не заданий BOT_TOKEN. Візьми токен у @BotFather і задай змінну оточення.")

TZ = ZoneInfo(os.getenv("TZ", "Europe/Paris"))

from charts import build_progress_chart, build_volume_chart, build_compare_chart

dp = Dispatcher()

CHART_PERIODS = [(7, "7д"), (30, "30д"), (90, "90д"), (0, "Все")]
VOL_PERIODS = [(7, "7д"), (14, "14д"), (30, "30д"), (90, "90д")]


# ---------- допоміжне ----------

def fmt_weight(w) -> str:
    return "власна вага" if w is None else f"{w:g} кг"


def reps_per_set(e) -> int:
    return e.total_reps // e.sets if e.sets else e.total_reps


def fmt_entry(e) -> str:
    line = f"{html.quote(e.exercise)} — {fmt_weight(e.weight)} · {e.sets}×{reps_per_set(e)}"
    if e.est_1rm:
        line += f" · 1ПМ≈{e.est_1rm:g}"
    return line


def resolve_created_at(workout_date, index: int) -> datetime:
    """Дата тренування -> aware UTC; index зсуває на секунди для порядку/унікальності."""
    if workout_date is not None:
        base = workout_date.replace(tzinfo=TZ).astimezone(timezone.utc)
    else:
        base = datetime.now(timezone.utc)
    return base + timedelta(seconds=index)


def split_query_days(raw: str, default_days=None):
    """'жим 30' -> ('жим', 30);  'жим' -> ('жим', default)."""
    parts = raw.split()
    days = default_days
    if parts and parts[-1].isdigit():
        days = int(parts[-1])
        parts = parts[:-1]
    return " ".join(parts).strip(), days


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Сьогодні"), KeyboardButton(text="📆 Тиждень")],
            [KeyboardButton(text="🗂 Вправи"), KeyboardButton(text="📊 Тоннаж")],
            [KeyboardButton(text="🗑 Видалити"), KeyboardButton(text="❓ Довідка")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Кинь тренування або обери дію…",
    )


def chart_keyboard(exercise: str, active: int):
    b = InlineKeyboardBuilder()
    ex = exercise[:24]
    for days, label in CHART_PERIODS:
        text = f"• {label} •" if days == active else label
        b.button(text=text, callback_data=f"cp:{days}:{ex}")
    b.adjust(4)
    return b.as_markup()


def volume_keyboard(active: int):
    b = InlineKeyboardBuilder()
    for days, label in VOL_PERIODS:
        text = f"• {label} •" if days == active else label
        b.button(text=text, callback_data=f"vp:{days}")
    b.adjust(4)
    return b.as_markup()


# ---------- команди ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привіт! Я веду журнал твоїх тренувань.\n\n"
        "<b>Кидай тренування одним повідомленням</b> у такому форматі:\n\n"
        "<code>02.07.2026\n"
        "жим лежачи: 60x10x3\n"
        "присідання: 80x5x5\n"
        "трицепс: 20x10x2 25x10x1\n"
        "підтягування: 0x8x3</code>\n\n"
        "• формат вправи: <code>назва: вагаХповтореньХпідходів</code>\n"
        "• перший рядок з датою — необовʼязковий (без нього = сьогодні)\n"
        "• різна вага на одну вправу — через пробіл\n"
        "• власна вага — пиши вагу <b>0</b>\n\n"
        "Команди: /today /week /stats /chart /volume /compare /history /remind /export /help",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
@dp.message(F.text == "❓ Довідка")
async def cmd_help(message: Message):
    await message.answer(
        "<b>Формат тренування</b>\n"
        "<code>дд.мм.рррр</code> (необовʼязково)\n"
        "<code>назва: вагаХповтореньХпідходів</code>\n\n"
        "Приклади рядків:\n"
        "• <code>присідання: 80x5x5</code> — 80 кг, 5 повт, 5 підх\n"
        "• <code>трицепс: 20x10x2 25x10x1</code> — дві різні ваги\n"
        "• <code>віджимання: 0x20x3</code> — власна вага\n\n"
        "Роздільник чисел: х, x, ×, * — будь-який.\n\n"
        "<b>Графіки за період:</b> <code>/chart жим 30</code> або кнопки під графіком.\n"
        "<b>Видалення:</b> кнопка «🗑 Видалити» або /history.",
        parse_mode="HTML",
    )


@dp.message(Command("today"))
@dp.message(F.text == "📅 Сьогодні")
async def cmd_today(message: Message):
    now_local = datetime.now(TZ)
    since = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    entries = await db.entries_since(message.from_user.id, since)
    if not entries:
        await message.answer("Сьогодні ще нічого не записано. Час у зал 💪")
        return
    total_vol = sum(e.volume for e in entries)
    lines = [f"📅 <b>Сьогодні</b> ({now_local:%d.%m}):", ""]
    lines += [f"• {fmt_entry(e)}" for e in entries]
    lines.append(f"\nВправ: {len(entries)} · Тоннаж: {total_vol:g} кг")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("week"))
@dp.message(F.text == "📆 Тиждень")
async def cmd_week(message: Message):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    entries = await db.entries_since(message.from_user.id, since)
    if not entries:
        await message.answer("За тиждень записів немає.")
        return
    by_day = defaultdict(list)
    for e in entries:
        by_day[e.created_at.astimezone(TZ).strftime("%d.%m (%a)")].append(e)
    lines = ["📆 <b>Останні 7 днів</b>", ""]
    for day, items in by_day.items():
        vol = sum(i.volume for i in items)
        lines.append(f"<b>{day}</b> — {len(items)} вправ, тоннаж {vol:g} кг")
        lines += [f"   • {fmt_entry(e)}" for e in items]
        lines.append("")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    raw = message.text.replace("/stats", "", 1).strip()
    query, days = split_query_days(raw)
    if not query:
        await message.answer("Вкажи вправу: <code>/stats жим лежачи</code>", parse_mode="HTML")
        return
    entries = await db.exercise_series(message.from_user.id, query, days)
    if not entries:
        await message.answer(f"Немає записів по «{html.quote(query)}».")
        return
    period = f" за {days} дн." if days else ""
    weighted = [e for e in entries if e.weight is not None]
    lines = [f"📈 <b>Прогрес: {html.quote(query)}</b>{period}", f"Записів: {len(entries)}", ""]
    if weighted:
        max_w = max(e.weight for e in weighted)
        max_1rm = max((e.est_1rm for e in weighted if e.est_1rm), default=None)
        total_vol = sum(e.volume for e in weighted)
        lines.append(f"🏋️ Макс. вага: {max_w:g} кг")
        if max_1rm:
            lines.append(f"💥 Оцінка 1ПМ: {max_1rm:g} кг")
        lines.append(f"📦 Сумарний тоннаж: {total_vol:g} кг\n")
    lines.append("Останнє:")
    for e in sorted(entries, key=lambda x: x.created_at, reverse=True)[:10]:
        d = e.created_at.astimezone(TZ).strftime("%d.%m")
        lines.append(f"   {d}: {fmt_weight(e.weight)} · {e.sets}×{reps_per_set(e)}")
    lines.append(f"\n📈 Графік: /chart {query}")
    await message.answer("\n".join(lines), parse_mode="HTML")


async def _chart_png(uid: int, exercise: str, days: int):
    entries = await db.exercise_series(uid, exercise, days or None)
    n_days = len({e.created_at.astimezone(TZ).date() for e in entries})
    if n_days < 2:
        return None, len(entries)
    png = await asyncio.to_thread(build_progress_chart, exercise, entries, TZ)
    return png, len(entries)


@dp.message(Command("chart"))
async def cmd_chart(message: Message):
    raw = message.text.replace("/chart", "", 1).strip()
    query, days = split_query_days(raw, default_days=0)  # 0 = усе
    if not query:
        await message.answer("Вкажи вправу: <code>/chart жим лежачи</code>", parse_mode="HTML")
        return
    png, n = await _chart_png(message.from_user.id, query, days)
    if not png:
        await message.answer(
            "Для графіка потрібно щонайменше 2 різні дні цієї вправи.\n"
            f"Поки що є записів: {n}. Статистика: /stats {query}"
        )
        return
    photo = BufferedInputFile(png, filename="progress.png")
    await message.answer_photo(
        photo, caption=f"📈 Прогрес: {html.quote(query)}",
        parse_mode="HTML", reply_markup=chart_keyboard(query, days),
    )


@dp.callback_query(F.data.startswith("cp:"))
async def cb_chart_period(cb: CallbackQuery):
    _, days_s, exercise = cb.data.split(":", 2)
    days = int(days_s)
    png, n = await _chart_png(cb.from_user.id, exercise, days)
    if not png:
        await cb.answer("Замало даних за цей період (треба ≥2 дні).", show_alert=True)
        return
    media = InputMediaPhoto(
        media=BufferedInputFile(png, filename="progress.png"),
        caption=f"📈 Прогрес: {html.quote(exercise)}", parse_mode="HTML",
    )
    await cb.message.edit_media(media, reply_markup=chart_keyboard(exercise, days))
    await cb.answer()


@dp.message(Command("volume"))
@dp.message(F.text == "📊 Тоннаж")
async def cmd_volume(message: Message):
    arg = message.text.replace("/volume", "", 1).strip()
    days = int(arg) if arg.isdigit() else 14
    days = max(2, min(days, 90))
    await _send_volume(message, message.from_user.id, days)


async def _send_volume(message: Message, uid: int, days: int):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    entries = await db.entries_since(uid, since)
    png = await asyncio.to_thread(build_volume_chart, entries, TZ, days) if entries else None
    if not png:
        await message.answer(f"За {days} дн. немає вправ з вагою для тоннажу.")
        return
    photo = BufferedInputFile(png, filename="volume.png")
    await message.answer_photo(photo, caption=f"📊 Тоннаж за {days} днів",
                               reply_markup=volume_keyboard(days))


@dp.callback_query(F.data.startswith("vp:"))
async def cb_volume_period(cb: CallbackQuery):
    days = int(cb.data.split(":", 1)[1])
    since = datetime.now(timezone.utc) - timedelta(days=days)
    entries = await db.entries_since(cb.from_user.id, since)
    png = await asyncio.to_thread(build_volume_chart, entries, TZ, days) if entries else None
    if not png:
        await cb.answer("За цей період немає тоннажу.", show_alert=True)
        return
    media = InputMediaPhoto(media=BufferedInputFile(png, filename="volume.png"),
                            caption=f"📊 Тоннаж за {days} днів")
    await cb.message.edit_media(media, reply_markup=volume_keyboard(days))
    await cb.answer()


@dp.message(Command("compare"))
async def cmd_compare(message: Message):
    arg = message.text.replace("/compare", "", 1).strip()
    sep = next((s for s in (" vs ", " проти ", "|") if s in arg), None)
    if not sep:
        await message.answer("Формат: <code>/compare жим лежачи vs присідання</code>", parse_mode="HTML")
        return
    name1, name2 = (p.strip() for p in arg.split(sep, 1))
    if not name1 or not name2:
        await message.answer("Вкажи дві вправи через «vs».")
        return
    e1 = await db.exercise_series(message.from_user.id, name1)
    e2 = await db.exercise_series(message.from_user.id, name2)
    if not e1 and not e2:
        await message.answer("Немає даних по жодній із цих вправ.")
        return
    png = await asyncio.to_thread(build_compare_chart, name1, e1, name2, e2, TZ)
    if not png:
        await message.answer("Немає ваги для побудови (обидві — власна вага?).")
        return
    photo = BufferedInputFile(png, filename="compare.png")
    await message.answer_photo(photo, caption=f"⚖️ {html.quote(name1)} vs {html.quote(name2)}",
                               parse_mode="HTML")


@dp.message(Command("list"))
@dp.message(F.text == "🗂 Вправи")
async def cmd_list(message: Message):
    ex = await db.distinct_exercises(message.from_user.id)
    if not ex:
        await message.answer("Ти ще нічого не записував.")
        return
    lines = ["🗂 <b>Твої вправи:</b>", ""] + [f"• {html.quote(e)}" for e in ex]
    lines.append("\nПрогрес: /stats назва · Графік: /chart назва")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ---------- видалення ----------

async def _delete_markup(uid: int):
    entries = await db.recent_entries(uid, 12)
    if not entries:
        return None, None
    b = InlineKeyboardBuilder()
    for e in entries:
        d = e.created_at.astimezone(TZ).strftime("%d.%m")
        label = f"❌ {d} · {e.exercise} {fmt_weight(e.weight)} {e.sets}×{reps_per_set(e)}"
        b.button(text=label[:64], callback_data=f"del:{e.id}")
    b.adjust(1)
    return "🗑 <b>Обери запис для видалення:</b>", b.as_markup()


@dp.message(Command("history"))
@dp.message(Command("delete"))
@dp.message(F.text == "🗑 Видалити")
async def cmd_delete_list(message: Message):
    text, kb = await _delete_markup(message.from_user.id)
    if not text:
        await message.answer("Записів ще немає.")
        return
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("del:"))
async def cb_delete(cb: CallbackQuery):
    entry_id = int(cb.data.split(":", 1)[1])
    entry = await db.delete_entry(cb.from_user.id, entry_id)
    if not entry:
        await cb.answer("Запис уже видалено.", show_alert=False)
    else:
        await cb.answer(f"Видалено: {entry.exercise}")
    text, kb = await _delete_markup(cb.from_user.id)
    if not text:
        await cb.message.edit_text("✅ Список порожній.")
    else:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@dp.message(Command("undo"))
async def cmd_undo(message: Message):
    entry = await db.delete_last(message.from_user.id)
    if entry:
        await message.answer(f"❌ Видалено останній: <code>{html.quote(entry.exercise)}</code>",
                             parse_mode="HTML")
    else:
        await message.answer("Немає що видаляти.")


# ---------- нагадування / експорт ----------

@dp.message(Command("remind"))
async def cmd_remind(message: Message):
    arg = message.text.replace("/remind", "", 1).strip().lower()
    if arg in ("off", "вимкнути", "0"):
        await db.set_reminder(message.from_user.id, enabled=False)
        await message.answer("🔕 Нагадування вимкнено.")
    elif arg in ("on", "увімкнути", ""):
        rem = await db.set_reminder(message.from_user.id, enabled=True)
        await message.answer(f"🔔 Увімкнено. Нагадаю після {rem.threshold_days} дн. простою. "
                             f"Змінити: /remind 5")
    elif arg.isdigit():
        days = max(1, min(int(arg), 30))
        await db.set_reminder(message.from_user.id, enabled=True, threshold_days=days)
        await message.answer(f"🔔 Нагадаю після {days} дн. без тренувань.")
    else:
        await message.answer("Використання: /remind on | off | 5")


@dp.message(Command("export"))
async def cmd_export(message: Message):
    entries = await db.all_entries(message.from_user.id)
    if not entries:
        await message.answer("Немає даних для експорту.")
        return
    rows = ["дата,вправа,вага,підходи,всього_повторень,тоннаж,оцінка_1пм,оригінал"]
    for e in entries:
        d = e.created_at.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        rows.append(f"{d},{e.exercise},{e.weight or ''},{e.sets},{e.total_reps},"
                    f"{e.volume:g},{e.est_1rm or ''},{e.raw.replace(',', ';')}")
    data = "\n".join(rows).encode("utf-8-sig")
    await message.answer_document(BufferedInputFile(data, filename="workouts.csv"),
                                  caption=f"📄 {len(entries)} записів")


# ---------- логування тренування (має бути ОСТАННІМ) ----------

@dp.message(F.text & ~F.text.startswith("/"))
async def log_workout(message: Message):
    workout_date, sets = parse_workout(message.text)
    if not sets:
        await message.answer(
            "Не зрозумів 🤔 Формат:\n"
            "<code>назва: вагаХповтореньХпідходів</code>\n"
            "напр. <code>жим лежачи: 60x10x3</code>. Деталі — /help",
            parse_mode="HTML",
        )
        return

    for i, p in enumerate(sets):
        await db.add_entry(message.from_user.id, p, created_at=resolve_created_at(workout_date, i))

    # групуємо для підтвердження
    grouped = defaultdict(list)
    for p in sets:
        grouped[p.exercise].append(p)
    date_label = (workout_date or datetime.now(TZ)).strftime("%d.%m.%Y")
    lines = [f"✅ <b>Записано за {date_label}:</b>", ""]
    for ex, items in grouped.items():
        parts = "; ".join(f"{fmt_weight(p.weight)} {p.sets}×{p.reps[0]}" for p in items)
        lines.append(f"• {html.quote(ex)} — {parts}")
    total_vol = sum(p.volume for p in sets)
    lines.append(f"\nВправ: {len(sets)} · Тоннаж: {total_vol:g} кг")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ---------- фонові нагадування ----------

async def reminder_loop(bot: Bot):
    await asyncio.sleep(30)
    while True:
        try:
            now = datetime.now(timezone.utc)
            if 9 <= now.astimezone(TZ).hour <= 21:
                for uid in await db.all_user_ids():
                    rem = await db.get_reminder(uid)
                    if not rem.enabled:
                        continue
                    last = await db.last_entry(uid)
                    if last is None:
                        continue
                    days_off = (now - last.created_at).total_seconds() / 86400
                    if days_off < rem.threshold_days:
                        continue
                    if rem.last_reminded_at and rem.last_reminded_at >= last.created_at:
                        continue
                    try:
                        await bot.send_message(
                            uid,
                            f"👋 Ти не тренувався вже {int(days_off)} дн. Час у зал 💪\n"
                            f"(вимкнути: /remind off)",
                        )
                        await db.set_reminder(uid, mark_reminded=True)
                    except Exception as e:
                        logging.warning("Не вдалось надіслати нагадування %s: %s", uid, e)
        except Exception as e:
            logging.exception("reminder_loop: %s", e)
        await asyncio.sleep(3600)


async def main():
    await db.init_db()
    bot = Bot(token=BOT_TOKEN)
    logging.info("БД готова (%s). Запускаю polling…", db.DATABASE_URL.split("://")[0])
    asyncio.create_task(reminder_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
