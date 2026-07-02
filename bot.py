"""
Бот-трекер тренувань на aiogram 3.x.

Запуск локально:
    export BOT_TOKEN=123:abc
    python bot.py

Просто напиши боту вправу, наприклад:
    жим лежачи 60 3х10
    тяга блока 55 4х12
    підтягування 3х8
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BufferedInputFile

import database as db
from parser import parse_message
from charts import build_progress_chart, build_volume_chart, build_compare_chart

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Не заданий BOT_TOKEN. Візьми токен у @BotFather і задай змінну оточення.")

# часовий пояс для групування «сьогодні / тиждень». За замовчуванням — Париж.
TZ = ZoneInfo(os.getenv("TZ", "Europe/Paris"))

dp = Dispatcher()


def fmt_weight(w: float | None) -> str:
    if w is None:
        return "власна вага"
    return f"{w:g} кг"


def fmt_entry(e) -> str:
    reps = e.total_reps // e.sets if e.sets else e.total_reps
    line = f"{e.exercise} — {fmt_weight(e.weight)} · {e.sets}×{reps}"
    if e.est_1rm:
        line += f" · 1ПМ≈{e.est_1rm:g}"
    return line


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привіт! Я веду журнал твоїх тренувань.\n\n"
        "Просто надішли мені вправу в такому форматі:\n"
        "<code>жим лежачи 60 3х10</code>\n"
        "<code>тяга блока 55 4х12</code>\n"
        "<code>підтягування 3х8</code>  (без ваги = власна вага)\n"
        "<code>жим гантелей 22 3х12,10,8</code>  (різні повторення)\n\n"
        "Можна кілька вправ одним повідомленням — кожна з нового рядка.\n\n"
        "Команди:\n"
        "/today — тренування за сьогодні\n"
        "/week — за 7 днів\n"
        "/stats <вправа> — прогрес по вправі\n"
        "/chart <вправа> — 📈 графік прогресу ваги\n"
        "/volume [днів] — 📊 тоннаж по днях (типово 14)\n"
        "/compare A vs B — ⚖️ порівняти дві вправи\n"
        "/list — усі вправи, які ти робив\n"
        "/history — останні записи\n"
        "/undo — видалити останній запис\n"
        "/remind 3 — 🔔 нагадувати після N днів простою\n"
        "/export — вивантажити все у CSV\n"
        "/help — формат запису",
        parse_mode="HTML",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Формат запису</b>\n"
        "<code>назва [вага] підходиХповторення</code>\n\n"
        "Приклади:\n"
        "• <code>присідання 80 5х5</code>\n"
        "• <code>тяга 55кг 4x12</code> (х, x, *, × — усі підходять)\n"
        "• <code>віджимання 3х20</code> (без ваги)\n"
        "• <code>жим 60 3х10,8,6</code> (різні повторення в підходах)\n\n"
        "Вага може бути дробова: <code>розводка 12.5 3х15</code>",
        parse_mode="HTML",
    )


@dp.message(Command("today"))
async def cmd_today(message: Message):
    now_local = datetime.now(TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    since = start_local.astimezone(timezone.utc)
    entries = await db.entries_since(message.from_user.id, since)
    if not entries:
        await message.answer("Сьогодні ще нічого не записано. Час у зал 💪")
        return
    total_vol = sum(e.volume for e in entries)
    lines = [f"📅 <b>Сьогодні</b> ({now_local:%d.%m}):", ""]
    lines += [f"• {fmt_entry(e)}" for e in entries]
    lines.append("")
    lines.append(f"Вправ: {len(entries)} · Тоннаж: {total_vol:g} кг")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("week"))
async def cmd_week(message: Message):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    entries = await db.entries_since(message.from_user.id, since)
    if not entries:
        await message.answer("За тиждень записів немає.")
        return
    # групуємо по днях
    by_day: dict[str, list] = {}
    for e in entries:
        key = e.created_at.astimezone(TZ).strftime("%d.%m (%a)")
        by_day.setdefault(key, []).append(e)
    lines = ["📆 <b>Останні 7 днів</b>", ""]
    for day, items in by_day.items():
        vol = sum(i.volume for i in items)
        lines.append(f"<b>{day}</b> — {len(items)} вправ, тоннаж {vol:g} кг")
        lines += [f"   • {fmt_entry(e)}" for e in items]
        lines.append("")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    query = message.text.replace("/stats", "", 1).strip()
    if not query:
        await message.answer(
            "Вкажи вправу: <code>/stats жим лежачи</code>", parse_mode="HTML"
        )
        return
    entries = await db.exercise_history(message.from_user.id, query, limit=50)
    if not entries:
        await message.answer(f"Немає записів по «{query}».")
        return

    weighted = [e for e in entries if e.weight is not None]
    lines = [f"📈 <b>Прогрес: {query}</b>", f"Записів: {len(entries)}", ""]

    if weighted:
        max_w = max(e.weight for e in weighted)
        max_1rm = max((e.est_1rm for e in weighted if e.est_1rm), default=None)
        total_vol = sum(e.volume for e in weighted)
        lines.append(f"🏋️ Макс. вага: {max_w:g} кг")
        if max_1rm:
            lines.append(f"💥 Оцінка 1ПМ: {max_1rm:g} кг")
        lines.append(f"📦 Сумарний тоннаж: {total_vol:g} кг")
        lines.append("")

    lines.append("Останнє (нове зверху):")
    for e in entries[:10]:
        d = e.created_at.astimezone(TZ).strftime("%d.%m")
        lines.append(f"   {d}: {fmt_weight(e.weight)} · {e.sets}×{e.total_reps//e.sets if e.sets else 0}")
    lines.append(f"\n📈 Графік: /chart {query}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("chart"))
async def cmd_chart(message: Message):
    query = message.text.replace("/chart", "", 1).strip()
    if not query:
        await message.answer(
            "Вкажи вправу: <code>/chart жим лежачи</code>\n"
            "Список твоїх вправ — /list", parse_mode="HTML"
        )
        return
    entries = await db.exercise_series(message.from_user.id, query)
    if not entries:
        await message.answer(f"Немає записів по «{query}».")
        return
    if len({e.created_at.astimezone(TZ).date() for e in entries}) < 2:
        await message.answer(
            "Для графіка потрібно щонайменше 2 різні дні тренувань цієї вправи. "
            "Поки що показую тільки статистику — /stats " + query
        )
        return
    png = await asyncio.to_thread(build_progress_chart, query, entries, TZ)
    if not png:
        await message.answer("Не вдалося побудувати графік.")
        return
    photo = BufferedInputFile(png, filename="progress.png")
    await message.answer_photo(photo, caption=f"📈 Прогрес: {query} ({len(entries)} записів)")


@dp.message(Command("volume"))
async def cmd_volume(message: Message):
    arg = message.text.replace("/volume", "", 1).strip()
    days = 14
    if arg.isdigit():
        days = max(2, min(int(arg), 90))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    entries = await db.entries_since(message.from_user.id, since)
    if not entries:
        await message.answer(f"За останні {days} днів записів немає.")
        return
    png = await asyncio.to_thread(build_volume_chart, entries, TZ, days)
    if not png:
        await message.answer(
            "Тоннаж рахується лише для вправ з вагою — за цей період таких немає."
        )
        return
    photo = BufferedInputFile(png, filename="volume.png")
    await message.answer_photo(photo, caption=f"📊 Тоннаж за {days} днів")


@dp.message(Command("compare"))
async def cmd_compare(message: Message):
    arg = message.text.replace("/compare", "", 1).strip()
    # роздільники: "vs", "проти", "|"
    sep = None
    for s in (" vs ", " проти ", "|"):
        if s in arg:
            sep = s
            break
    if not sep:
        await message.answer(
            "Формат: <code>/compare жим лежачи vs присідання</code>", parse_mode="HTML"
        )
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
        await message.answer("Немає ваги для побудови (обидві вправи — власна вага?).")
        return
    photo = BufferedInputFile(png, filename="compare.png")
    await message.answer_photo(photo, caption=f"⚖️ {name1} vs {name2}")


@dp.message(Command("remind"))
async def cmd_remind(message: Message):
    arg = message.text.replace("/remind", "", 1).strip().lower()
    if arg in ("off", "вимкнути", "0"):
        await db.set_reminder(message.from_user.id, enabled=False)
        await message.answer("🔕 Нагадування вимкнено.")
        return
    if arg in ("on", "увімкнути", ""):
        rem = await db.set_reminder(message.from_user.id, enabled=True)
        await message.answer(
            f"🔔 Нагадування увімкнено. Нагадаю, якщо не тренуватимешся "
            f"{rem.threshold_days} дн. Змінити поріг: <code>/remind 5</code>",
            parse_mode="HTML",
        )
        return
    if arg.isdigit():
        days = max(1, min(int(arg), 30))
        await db.set_reminder(message.from_user.id, enabled=True, threshold_days=days)
        await message.answer(f"🔔 Нагадаю після {days} дн. без тренувань.")
        return
    await message.answer(
        "Використання:\n"
        "<code>/remind on</code> — увімкнути\n"
        "<code>/remind off</code> — вимкнути\n"
        "<code>/remind 5</code> — нагадувати після 5 днів простою",
        parse_mode="HTML",
    )


@dp.message(Command("list"))
async def cmd_list(message: Message):
    ex = await db.distinct_exercises(message.from_user.id)
    if not ex:
        await message.answer("Ти ще нічого не записував.")
        return
    lines = ["🗂 <b>Твої вправи:</b>", ""] + [f"• {e}" for e in ex]
    lines.append("\nПрогрес: /stats <назва> · Графік: /chart <назва>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("history"))
async def cmd_history(message: Message):
    entries = await db.recent_entries(message.from_user.id, limit=15)
    if not entries:
        await message.answer("Записів ще немає.")
        return
    lines = ["🕑 <b>Останні записи</b>", ""]
    for e in entries:
        d = e.created_at.astimezone(TZ).strftime("%d.%m %H:%M")
        lines.append(f"{d} — {fmt_entry(e)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("undo"))
async def cmd_undo(message: Message):
    entry = await db.delete_last(message.from_user.id)
    if entry:
        await message.answer(f"❌ Видалено: <code>{entry.raw}</code>", parse_mode="HTML")
    else:
        await message.answer("Немає що видаляти.")


@dp.message(Command("export"))
async def cmd_export(message: Message):
    entries = await db.all_entries(message.from_user.id)
    if not entries:
        await message.answer("Немає даних для експорту.")
        return
    rows = ["дата,вправа,вага,підходи,всього_повторень,тоннаж,оцінка_1пм,оригінал"]
    for e in entries:
        d = e.created_at.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        raw = e.raw.replace(",", ";")
        rows.append(
            f"{d},{e.exercise},{e.weight or ''},{e.sets},{e.total_reps},"
            f"{e.volume:g},{e.est_1rm or ''},{raw}"
        )
    data = "\n".join(rows).encode("utf-8-sig")
    file = BufferedInputFile(data, filename="workouts.csv")
    await message.answer_document(file, caption=f"📄 {len(entries)} записів")


@dp.message(F.text & ~F.text.startswith("/"))
async def log_workout(message: Message):
    parsed = parse_message(message.text)
    if not parsed:
        await message.answer(
            "Не зрозумів 🤔 Формат: <code>вправа вага підходиХповторення</code>\n"
            "Напр.: <code>жим лежачи 60 3х10</code>. Деталі — /help",
            parse_mode="HTML",
        )
        return
    saved = []
    for p in parsed:
        await db.add_entry(message.from_user.id, p)
        extra = f" · 1ПМ≈{p.est_1rm:g}" if p.est_1rm else ""
        saved.append(f"✅ {p.exercise} — {fmt_weight(p.weight)}, {p.sets}×{'/'.join(map(str,p.reps))}{extra}")
    await message.answer("\n".join(saved))


async def reminder_loop(bot: Bot):
    """
    Фоновий цикл: раз на годину перевіряє, хто давно не тренувався,
    і надсилає нагадування. Одне нагадування на «простій» (не спамить),
    і тільки в денний час (9:00–21:00 за локальним TZ).
    """
    await asyncio.sleep(30)  # даємо боту стартувати
    while True:
        try:
            now = datetime.now(timezone.utc)
            local_hour = now.astimezone(TZ).hour
            if 9 <= local_hour <= 21:
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
                    # уже нагадували за цей простій?
                    if rem.last_reminded_at and rem.last_reminded_at >= last.created_at:
                        continue
                    try:
                        await bot.send_message(
                            uid,
                            f"👋 Ти не тренувався вже {int(days_off)} дн. "
                            f"Час повертатися в зал 💪\n"
                            f"(вимкнути: /remind off)",
                        )
                        await db.set_reminder(uid, mark_reminded=True)
                    except Exception as e:
                        logging.warning("Не вдалось надіслати нагадування %s: %s", uid, e)
        except Exception as e:
            logging.exception("Помилка в reminder_loop: %s", e)
        await asyncio.sleep(3600)  # перевірка щогодини


async def main():
    await db.init_db()
    bot = Bot(token=BOT_TOKEN)
    logging.info("БД готова (%s). Запускаю polling…", db.DATABASE_URL.split("://")[0])
    asyncio.create_task(reminder_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
