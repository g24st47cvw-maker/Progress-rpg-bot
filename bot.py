"""
Life-RPG Telegram-бот.
Та же система квестов и XP, что и в веб-панели, но с хранением
в собственной базе данных SQLite — не зависит от облачного
хранилища Claude-артефактов.
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import date, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("life-rpg-bot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "life_rpg.db")

# ---------------------------------------------------------------------------
# Данные системы (см. life-rpg-system.md)
# ---------------------------------------------------------------------------

SPHERES = [
    ("quit_smoking", "Бросить курить", "🚭"),
    ("health", "Здоровье", "❤️"),
    ("family", "Семья", "👨‍👩‍👧"),
    ("work", "Работа / А330 → КВС", "✈️"),
    ("english", "Английский", "🗣️"),
    ("kennel", "Питомник", "🐾"),
    ("home", "Дом / участок", "🏡"),
    ("blog", "Блог / контент", "✍️"),
    ("finance", "Финансы", "💰"),
    ("rest", "Отдых", "🌿"),
    ("order", "Порядок", "🧹"),
]
SPHERE_LABEL = {sid: f"{icon} {label}" for sid, label, icon in SPHERES}
SPHERE_IDS = [sid for sid, _, _ in SPHERES]

A330_BLOCKS = [
    "Limitations", "Memory Items", "SOP", "Performance", "Supplementary Procedures",
    "Правила полётов", "РПП А", "ECAM", "Аэродинамика", "QRH",
    "Abnormal Procedures", "MEL", "Заправка ВС топливом",
    "Противообледенительная обработка", "Оформление тех. документации", "Loadsheet & Trim Sheet",
]
A330_STATUS_LABEL = ["⬜ Не начато", "🟡 В процессе", "✅ Готово"]

XP_BY_TYPE = {"mini": 10, "normal": 30, "main": 100}
TYPE_LABEL = {"mini": "Мини", "normal": "Обычный", "main": "Главный"}
BASE_LEVEL_COST = 500
LEVEL_STEP = 100


def compute_level(total_xp: int):
    level = 1
    remaining = total_xp
    cost = BASE_LEVEL_COST
    while remaining >= cost:
        remaining -= cost
        level += 1
        cost += LEVEL_STEP
    return level, remaining, cost


def today_str() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            sphere TEXT NOT NULL,
            type TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sphere_xp (
            chat_id INTEGER NOT NULL,
            sphere TEXT NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, sphere)
        );
        CREATE TABLE IF NOT EXISTS daily_log (
            chat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, day)
        );
        CREATE TABLE IF NOT EXISTS a330 (
            chat_id INTEGER NOT NULL,
            block TEXT NOT NULL,
            status INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, block)
        );
        """
    )
    conn.commit()
    conn.close()


def ensure_user(chat_id: int):
    conn = get_conn()
    for sid, _, _ in SPHERES:
        conn.execute(
            "INSERT OR IGNORE INTO sphere_xp (chat_id, sphere, xp) VALUES (?, ?, 0)",
            (chat_id, sid),
        )
    for block in A330_BLOCKS:
        conn.execute(
            "INSERT OR IGNORE INTO a330 (chat_id, block, status) VALUES (?, ?, 0)",
            (chat_id, block),
        )
    conn.commit()
    conn.close()


def get_total_xp(chat_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(xp), 0) AS s FROM sphere_xp WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    return row["s"]


def get_sphere_xp(chat_id: int) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT sphere, xp FROM sphere_xp WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    return {r["sphere"]: r["xp"] for r in rows}


def bump_sphere_xp(chat_id: int, sphere: str, delta: int):
    conn = get_conn()
    conn.execute(
        """INSERT INTO sphere_xp (chat_id, sphere, xp) VALUES (?, ?, ?)
           ON CONFLICT(chat_id, sphere) DO UPDATE SET xp = MAX(0, xp + excluded.xp)""",
        (chat_id, sphere, delta),
    )
    conn.commit()
    conn.close()


def bump_daily_log(chat_id: int, delta: int):
    conn = get_conn()
    day = today_str()
    conn.execute(
        """INSERT INTO daily_log (chat_id, day, xp) VALUES (?, ?, ?)
           ON CONFLICT(chat_id, day) DO UPDATE SET xp = MAX(0, xp + excluded.xp)""",
        (chat_id, day, delta),
    )
    conn.commit()
    conn.close()


def add_quest(chat_id: int, text: str, sphere: str, qtype: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO quests (chat_id, text, sphere, type, done) VALUES (?, ?, ?, ?, 0)",
        (chat_id, text, sphere, qtype),
    )
    conn.commit()
    conn.close()


def get_quests(chat_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM quests WHERE chat_id = ? ORDER BY id", (chat_id,)
    ).fetchall()
    conn.close()
    return rows


def get_quest(chat_id: int, quest_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM quests WHERE chat_id = ? AND id = ?", (chat_id, quest_id)
    ).fetchone()
    conn.close()
    return row


def toggle_quest(chat_id: int, quest_id: int):
    q = get_quest(chat_id, quest_id)
    if not q:
        return
    xp = XP_BY_TYPE[q["type"]]
    will_be_done = 0 if q["done"] else 1
    delta = xp if will_be_done else -xp
    conn = get_conn()
    conn.execute("UPDATE quests SET done = ? WHERE id = ?", (will_be_done, quest_id))
    conn.commit()
    conn.close()
    bump_sphere_xp(chat_id, q["sphere"], delta)
    bump_daily_log(chat_id, delta)


def delete_quest(chat_id: int, quest_id: int):
    q = get_quest(chat_id, quest_id)
    if not q:
        return
    if q["done"]:
        xp = XP_BY_TYPE[q["type"]]
        bump_sphere_xp(chat_id, q["sphere"], -xp)
        bump_daily_log(chat_id, -xp)
    conn = get_conn()
    conn.execute("DELETE FROM quests WHERE id = ?", (quest_id,))
    conn.commit()
    conn.close()


def new_day(chat_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM quests WHERE chat_id = ? AND done = 1", (chat_id,))
    conn.commit()
    conn.close()


def get_a330(chat_id: int) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT block, status FROM a330 WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    return {r["block"]: r["status"] for r in rows}


def cycle_a330(chat_id: int, block: str):
    a330 = get_a330(chat_id)
    cur = a330.get(block, 0)
    nxt = (cur + 1) % 3
    conn = get_conn()
    conn.execute(
        "UPDATE a330 SET status = ? WHERE chat_id = ? AND block = ?",
        (nxt, chat_id, block),
    )
    conn.commit()
    conn.close()


def get_week_xp(chat_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT day, xp FROM daily_log WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    by_day = {r["day"]: r["xp"] for r in rows}
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    return [(d, by_day.get(d, 0)) for d in days]


def get_streak(chat_id: int) -> int:
    conn = get_conn()
    rows = conn.execute(
        "SELECT day, xp FROM daily_log WHERE chat_id = ?", (chat_id,)
    ).fetchall()
    conn.close()
    by_day = {r["day"]: r["xp"] for r in rows}
    streak = 0
    for i in range(0, 90):
        d = (date.today() - timedelta(days=i)).isoformat()
        if by_day.get(d, 0) > 0:
            streak += 1
        else:
            break
    return streak


def export_data(chat_id: int) -> dict:
    return {
        "sphereXP": get_sphere_xp(chat_id),
        "quests": [dict(q) for q in get_quests(chat_id)],
        "a330": get_a330(chat_id),
        "totalXP": get_total_xp(chat_id),
    }


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Сегодня"), KeyboardButton(text="➕ Новый квест")],
        [KeyboardButton(text="📊 Прогресс"), KeyboardButton(text="✈️ А330")],
        [KeyboardButton(text="📅 Неделя"), KeyboardButton(text="🔁 Новый день")],
        [KeyboardButton(text="💾 Экспорт")],
    ],
    resize_keyboard=True,
)


def sphere_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for sid, label, icon in SPHERES:
        row.append(InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"sphere:{sid}"))
        if len(row) == 1:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{TYPE_LABEL[t]} · {xp} XP", callback_data=f"type:{t}")]
            for t, xp in XP_BY_TYPE.items()
        ]
    )


def quests_kb(chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    for q in get_quests(chat_id):
        mark = "☑" if q["done"] else "⬜"
        label = f"{mark} {q['text'][:28]} (+{XP_BY_TYPE[q['type']]})"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"toggle:{q['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"del:{q['id']}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else InlineKeyboardMarkup(inline_keyboard=[])


def a330_kb(chat_id: int) -> InlineKeyboardMarkup:
    a330 = get_a330(chat_id)
    rows = []
    for block in A330_BLOCKS:
        status = a330.get(block, 0)
        icon = ["⬜", "🟡", "✅"][status]
        rows.append([InlineKeyboardButton(text=f"{icon} {block}", callback_data=f"a330:{block}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# FSM для добавления квеста
# ---------------------------------------------------------------------------

class QuestForm(StatesGroup):
    text = State()
    sphere = State()


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    ensure_user(message.chat.id)
    await message.answer(
        "Панель командира на связи. Выбери действие внизу ⬇️",
        reply_markup=MAIN_KB,
    )


@router.message(F.text == "➕ Новый квест")
async def new_quest_start(message: Message, state: FSMContext):
    await state.set_state(QuestForm.text)
    await message.answer("Что нужно сделать?")


@router.message(QuestForm.text)
async def new_quest_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text.strip())
    await state.set_state(QuestForm.sphere)
    await message.answer("В какую сферу?", reply_markup=sphere_kb())


@router.callback_query(QuestForm.sphere, F.data.startswith("sphere:"))
async def new_quest_sphere(callback: CallbackQuery, state: FSMContext):
    sphere = callback.data.split(":", 1)[1]
    await state.update_data(sphere=sphere)
    await callback.message.edit_text("Тип квеста?", reply_markup=type_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("type:"))
async def new_quest_type(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "text" not in data or "sphere" not in data:
        await callback.answer("Начни заново: ➕ Новый квест", show_alert=True)
        return
    qtype = callback.data.split(":", 1)[1]
    add_quest(callback.message.chat.id, data["text"], data["sphere"], qtype)
    await state.clear()
    xp = XP_BY_TYPE[qtype]
    await callback.message.edit_text(
        f"✅ Квест добавлен: {data['text']}\n{SPHERE_LABEL[data['sphere']]} · {TYPE_LABEL[qtype]} · +{xp} XP"
    )
    await callback.answer()


@router.message(F.text == "📋 Сегодня")
async def show_quests(message: Message):
    ensure_user(message.chat.id)
    quests = get_quests(message.chat.id)
    if not quests:
        await message.answer("Квестов пока нет — добавь через ➕ Новый квест.")
        return
    await message.answer("Полётный лист:", reply_markup=quests_kb(message.chat.id))


@router.callback_query(F.data.startswith("toggle:"))
async def cb_toggle(callback: CallbackQuery):
    quest_id = int(callback.data.split(":", 1)[1])
    toggle_quest(callback.message.chat.id, quest_id)
    await callback.message.edit_reply_markup(reply_markup=quests_kb(callback.message.chat.id))
    await callback.answer()


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(callback: CallbackQuery):
    quest_id = int(callback.data.split(":", 1)[1])
    delete_quest(callback.message.chat.id, quest_id)
    kb = quests_kb(callback.message.chat.id)
    if kb.inline_keyboard:
        await callback.message.edit_reply_markup(reply_markup=kb)
    else:
        await callback.message.edit_text("Квестов не осталось.")
    await callback.answer("Удалено")


@router.message(F.text == "🔁 Новый день")
async def cmd_new_day(message: Message):
    new_day(message.chat.id)
    await message.answer("Новый день начат — выполненные квесты убраны из списка.")


@router.message(F.text == "📊 Прогресс")
async def show_progress(message: Message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    total = get_total_xp(chat_id)
    level, xp_in_level, xp_for_next = compute_level(total)
    streak = get_streak(chat_id)
    today_xp = dict(get_week_xp(chat_id)).get(today_str(), 0)
    sphere_xp = get_sphere_xp(chat_id)

    lines = [
        f"Ур. {level} · {xp_in_level}/{xp_for_next} XP (след. уровень: {xp_for_next + LEVEL_STEP})",
        f"Сегодня заработано: {today_xp} XP",
        f"Серия дней подряд: {streak}",
        "",
        "Сферы:",
    ]
    for sid, label, icon in SPHERES:
        lines.append(f"{icon} {label}: {sphere_xp.get(sid, 0)} XP")
    await message.answer("\n".join(lines))


@router.message(F.text == "✈️ А330")
async def show_a330(message: Message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    a330 = get_a330(chat_id)
    done = sum(1 for v in a330.values() if v == 2)
    await message.answer(
        f"Вопросы А330 → ввод в КВС: {done}/{len(A330_BLOCKS)}\nНажми на блок, чтобы изменить статус.",
        reply_markup=a330_kb(chat_id),
    )


@router.callback_query(F.data.startswith("a330:"))
async def cb_a330(callback: CallbackQuery):
    block = callback.data.split(":", 1)[1]
    cycle_a330(callback.message.chat.id, block)
    a330 = get_a330(callback.message.chat.id)
    done = sum(1 for v in a330.values() if v == 2)
    await callback.message.edit_text(
        f"Вопросы А330 → ввод в КВС: {done}/{len(A330_BLOCKS)}\nНажми на блок, чтобы изменить статус.",
        reply_markup=a330_kb(callback.message.chat.id),
    )
    await callback.answer()


@router.message(F.text == "📅 Неделя")
async def show_week(message: Message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    week = get_week_xp(chat_id)
    dow = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
    lines = []
    for d, xp in week:
        weekday = date.fromisoformat(d).weekday()
        marker = "◀" if d == today_str() else ""
        lines.append(f"{dow[weekday]} {d}: {xp} XP {marker}")
    await message.answer("Неделя:\n" + "\n".join(lines))


@router.message(F.text == "💾 Экспорт")
async def export_cmd(message: Message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    data = export_data(chat_id)
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > 3500:
        # Telegram ограничивает длину сообщения — отправим файлом при необходимости
        await message.answer("Слишком много данных для сообщения, экспортирую файлом…")
        with open("/tmp/life_rpg_export.json", "w", encoding="utf-8") as f:
            f.write(text)
        from aiogram.types import FSInputFile
        await message.answer_document(FSInputFile("/tmp/life_rpg_export.json"))
    else:
        await message.answer(f"```\n{text}\n```", parse_mode="Markdown")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан TELEGRAM_BOT_TOKEN. Установи переменную окружения с токеном от @BotFather."
        )
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    log.info("Life-RPG бот запущен, начинаю polling…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
