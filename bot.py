"""
Life-RPG Telegram-бот.
Та же система квестов и XP, что и в веб-панели, но с хранением
в PostgreSQL от Railway — не зависит ни от облачного хранилища
Claude-артефактов, ни от локальных файлов/volume.
"""

import asyncio
import io
import json
import logging
import os
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import psycopg2
import psycopg2.extras

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
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
DATABASE_URL = os.environ.get("DATABASE_URL")

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

A330_BLOCKS = [
    "Limitations", "Memory Items", "SOP", "Performance", "Supplementary Procedures",
    "Правила полётов", "РПП А", "ECAM", "Аэродинамика", "QRH",
    "Abnormal Procedures", "MEL", "Заправка ВС топливом",
    "Противообледенительная обработка", "Оформление тех. документации", "Loadsheet & Trim Sheet",
]

QUESTIONS_SEED = [
("Limitations", "Weight limitations (max taxi/takeoff/landing weight)"),
("Limitations", "Environment envelope: min/max OAT для TO&LDG, max crosswind/tailwind TO/LDG, max OAT в Cruise"),
("Limitations", "Speed limitations: VMO/MMO, скорость выпуска/уборки шасси"),
("Limitations", "Autoflight: мин. высота для автопилота, max ветер для CAT2/CAT3"),
("Limitations", "Max operating altitude с выпущенными флап/слэт"),
("Limitations", "Max operating altitude для шасси"),
("Limitations", "Max brake temperature перед взлётом"),
("Limitations", "APU limitations: starter duty cycles, battery start limit, max altitude для bleed"),
("Limitations", "Engine: max EGT для запуска, TIME LIMIT для TOGA/FLEX, мин. температура масла, реверс, cooling/warm-up time"),

("SOP", "Captain is always PF"),
("SOP", "Внутрикабинные переговоры и ограничения доступа в кабину для кабинного экипажа"),
("SOP", "AIRBUS GOLDEN RULES — перечислить и объяснить своими словами"),
("SOP", "Работа с контрольными листами (Normal checklist)"),
("SOP", "Использование автоматических режимов"),
("SOP", "Условия стабилизированного захода на посадку"),
("SOP", "Предполётная подготовка, время начала, действия при неявке ЧЛЭ"),
("SOP", "За сколько минут до вылета прибыть на ВС"),
("SOP", "180 TURN ON THE RW"),
("SOP", "NPA — стратегии захода (Final app; NAV/FPA; NAV/VS, TRK-FPA)"),
("SOP", "Не позднее какого удаления от FAF начинать выпуск механизации"),
("SOP", "На каких этапах полёта экипаж на местах и ведёт радиосвязь"),
("SOP", "Распределение обязанностей PF/PNF"),
("SOP", "Контроль пространственного положения на конечном этапе захода"),
("SOP", "CRUISE процедура PF"),
("SOP", "DESCENT MONITORING — расчёт и контроль снижения"),
("SOP", "Когда рекомендуется EARLY STABILIZED APPROACH"),
("SOP", "APPROACH USING FINAL APPROACH GUIDANCE"),
("SOP", "CIRCLE-TO-LAND"),
("SOP", "G/S INTERCEPTION FROM ABOVE"),
("SOP", "GO AROUND"),
("SOP", "Std callouts отклонений на заходе"),
("SOP", "Electronic Flight Bag — проверка актуальности базы данных"),

("РПП А", "Обязанности членов лётного экипажа"),
("РПП А", "Инспекторская проверка SAFA на перроне"),
("РПП А", "Требования к формированию лётных экипажей"),
("РПП А", "Виды квалификационных проверок"),
("РПП А", "Классификация маршрутов и аэродромов, допуск к полётам"),
("РПП А", "Полётная смена, полётное время, продолжительность"),
("РПП А", "Рабочее время, время отдыха"),
("РПП А", "Категории сложности аэродромов, сроки действия подготовок"),
("РПП А", "Температурная поправка в зависимости от типа захода"),
("РПП А", "Снижение ниже минимальной безопасной высоты при заходе по ППП"),
("РПП А", "Снижение ниже MDA(H)"),
("РПП А", "Варианты принятия решения на вылет"),
("РПП А", "Требуемые условия на аэродроме назначения для вылета"),
("РПП А", "Требования к метеоусловиям на запасном для вылета"),
("РПП А", "Учёт метеоусловий при полёте менее 2 часов"),
("РПП А", "Ceiling — что не учитывается при решении на вылет"),
("РПП А", "Условия выбора запасного аэродрома"),
("РПП А", "Когда требуется запасной для взлёта"),
("РПП А", "Вылет без запасного аэродрома назначения"),
("РПП А", "Независимые рабочие ВПП"),
("РПП А", "Вылет при погоде ниже минимума на аэродроме назначения"),
("РПП А", "Требования к запасным на маршруте"),
("РПП А", "Эксплуатационный минимум аэродрома"),
("РПП А", "Минимум командира ВС"),
("РПП А", "Расчёт потребного топлива"),
("РПП А", "Сообщение MINIMUM FUEL"),
("РПП А", "MAYDAY FUEL — когда объявляется"),
("РПП А", "Что подтверждает КВС, подписывая рабочий план полёта"),
("РПП А", "Ответственность за судовую/полётную документацию"),
("РПП А", "Максимальные изменения взлётной массы перед вылетом"),
("РПП А", "Опасные предметы как груз/почта/багаж"),
("РПП А", "Действия при разливе топлива на стоянке"),
("РПП А", "Посадка/высадка пассажиров при заправке"),
("РПП А", "Противообледенительные жидкости — нижний предел по tнв"),
("РПП А", "Обязанности персонала по ПОЖ-обработке"),
("РПП А", "Более высокая концентрация ПОЖ по требованию КВС"),
("РПП А", "Недостаточное время защитного действия ПОЖ для выруливания"),
("РПП А", "Несогласованный отказ КВС от ПОЖ ВС"),
("РПП А", "Действия после вскрытия папки судовой документации"),
("РПП А", "Когда ВС считается принятым лётным экипажем"),
("РПП А", "Проверка судовой документации во внебазовом аэропорту"),
("РПП А", "Когда запрещается начинать/продолжать руление"),
("РПП А", "Скорость руления"),
("РПП А", "Применение автомобиля сопровождения на рулении"),
("РПП А", "Действия КВС перед взлётом"),
("РПП А", "Действия КВС при прекращении взлёта"),
("РПП А", "Случаи, когда методика уменьшения шума не требуется"),
("РПП А", "Взлёт с кратковременной остановкой на ВПП"),
("РПП А", "Временные интервалы от разрешения на взлёт до начала разбега"),
("РПП А", "Ограничения по вертикальной скорости за 1000ft до эшелона"),
("РПП А", "Покидание кабины одним из пилотов"),
("РПП А", "Максимальные вертикальные скорости на снижении"),
("РПП А", "Видимость/RVR ниже минимума в процессе снижения"),
("РПП А", "Значения RVR на полосе"),
("РПП А", "Ориентиры для продолжения захода ниже DA/H или MDA/H"),
("РПП А", "Прекращение снижения и уход на 2-й круг"),
("РПП А", "Условия стабилизации ВС"),
("РПП А", "Горизонтальный полёт на MDA(H) при CDFA"),
("РПП А", "Порядок действий при уходе на запасной"),
("РПП А", "Принципы автоматизации, политика компании"),
("РПП А", "Автоматическая посадка по категориям ILS"),
("РПП А", "Удаление обхода Cb/TCU по РПП А"),
("РПП А", "Правила «стерильной кабины»"),
("РПП А", "Запрет размещения пассажиров на доп. креслах"),
("РПП А", "Действия при ранении/ухудшении здоровья экипажа"),
("РПП А", "Высота ухода на 2-й круг при CDFA"),
("РПП А", "Радиусы зоны визуального маневрирования"),
("РПП А", "Порядок перевозки оружия на борту"),
("РПП А", "Защита кабины пилотов, режимы доступа"),
("РПП А", "Действия с недисциплинированными пассажирами"),
("РПП А", "Правила визуального захода на посадку"),
("РПП А", "Полёты в RVSM"),
("РПП А", "Потеря радиосвязи"),
("РПП А", "Сигналы ИТП при отсутствии радиосвязи"),
("РПП А", "На основании каких документов разработано РПП А"),

("MEL", "На основе каких документов создаётся MEL"),
("MEL", "Структура MEL"),
("MEL", "Правило продления действия MEL"),
("MEL", "Как определяется срок действия MEL"),
("MEL", "Оформление допуска ВС к полёту по MEL"),
("MEL", "Обязанность КВС при нескольких отказах"),
("MEL", "Кто принимает окончательное решение о вылете по MEL"),
("MEL", "Оформление отказа КВС от полёта с неисправностью"),
("MEL", "Вылет по MEL из промежуточного аэропорта без участия ИТС"),
("MEL", "Вылет по MEL из промежуточного аэропорта с участием ИТС"),
("MEL", "Этапы действия MEL"),
("MEL", "Быстрый поиск информации в MEL на рулении"),
("MEL", "Документ CDL — что это и где находится"),
("MEL", "Принятие решения по CDL (практика)"),
("MEL", "Определение нужного компьютера для перезапуска"),

("Memory Items", "Знание наизусть Memory Items, включая мелкий шрифт + понимание работы систем ВС при их выполнении"),

("Аэродинамика", "Зависимость Cy от угла атаки (график + обледенение, механизация, скольжение, PFD scale)"),
("Аэродинамика", "Влияние механизации на взлётные/посадочные характеристики"),
("Аэродинамика", "Скорости набора высоты (макс. градиент, макс. скороподъёмность)"),
("Аэродинамика", "Методы гашения избытка высоты, расчёт траектории снижения"),
("Аэродинамика", "Полёт с OEI: b-target, side-slip, балансировка"),
("Аэродинамика", "NORMAL LAW, DIR LAW, ALTN LAW, MECH BACK UP"),
("Аэродинамика", "Максимальная высота полёта (теор./практ. потолок, FMGS REC ALT)"),
("Аэродинамика", "Гидроглиссирование, посадка на contaminated RW"),
("Аэродинамика", "Взлёт и посадка с боковым ветром"),
("Аэродинамика", "GROUND SPEED mini"),
("Аэродинамика", "Работа SRS mode"),
("Аэродинамика", "Факторы, влияющие на дистанцию прерванного взлёта"),
("Аэродинамика", "Полёт в турбулентности"),
("Аэродинамика", "PROTECTIONS при воздействии пилота на органы управления"),
("Аэродинамика", "PROTECTIONS без воздействия пилота"),
("Аэродинамика", "MODE REVERSIONS"),
("Аэродинамика", "Characteristic and protection speeds"),

("Loadsheet & Trim Sheet", "Практическое упражнение по подготовке Loadsheet & Trim Sheet"),

("Supplementary Procedures", "Airframe deicing/anti-icing procedure on ground"),
("Supplementary Procedures", "Ground operations in cold weather"),
("Supplementary Procedures", "Ground operations in heavy rain"),
("Supplementary Procedures", "Minimum speed with ice accretion"),
("Supplementary Procedures", "Definition of icing conditions"),
("Supplementary Procedures", "Ice shedding"),
("Supplementary Procedures", "Water system draining"),
("Supplementary Procedures", "Manual engine start"),
("Supplementary Procedures", "Engine start with external pneumatic power"),
("Supplementary Procedures", "Crossbleed engine start"),
("Supplementary Procedures", "Engine start valve manual operation"),
("Supplementary Procedures", "Engine ventilation (dry cranking)"),
("Supplementary Procedures", "Refueling"),
("Supplementary Procedures", "Operation with nosewheel steering offset"),
("Supplementary Procedures", "Pushback with power push unit via main landing gear"),
("Supplementary Procedures", "Operations at QNH above 1050 hPa"),
("Supplementary Procedures", "Reduced vertical separation minimum"),

("Performance", "MAX TAKE OFF WEIGHT — чем ограничен"),
("Performance", "Учёт порывов ветра при расчёте взлётных/посадочных характеристик"),
("Performance", "Какие расчёты обязателен экипаж перед вылетом и для каких аэродромов"),
("Performance", "Действия при несоответствии RLD/ALD(FLD) и LDA"),
("Performance", "Коэффициент безопасности для посадочных характеристик — когда не обязателен"),
("Performance", "Алгоритм расчёта посадочных характеристик по Landing chart"),
("Performance", "Выбор конфигурации при заходе на посадку"),
("Performance", "Стандартная практика использования реверса на посадке"),
("Performance", "Выбор конфигурации для взлёта"),
("Performance", "Алгоритм расчёта взлётных характеристик по RTOW/EFB charts"),
("Performance", "Использование FUEL PENALTY FACTOR TABLES"),

("Противообледенительная обработка", "Правила обработки, типы ПОЖ"),
("Противообледенительная обработка", "Этапы обработки, выбор % концентрации"),
("Противообледенительная обработка", "Время действия ПОЖ, таблица holdover time"),
("Противообледенительная обработка", "Допускается ли СЛО на крыле и фюзеляже"),
("Противообледенительная обработка", "Правила принятия решения на обработку"),

("Правила полётов", "Документация на борту ВС (Воздушный кодекс)"),
("Правила полётов", "Горный аэродром (ФАП)"),
("Правила полётов", "Горная, холмистая, равнинная местность (ФАП)"),
("Правила полётов", "Расчётное время прибытия (ФАП)"),
("Правила полётов", "Безопасная высота полёта в районе аэродрома (ФАП)"),
("Правила полётов", "Нижний (безопасный) эшелон полёта (ФАП)"),
("Правила полётов", "Перевод шкал барометрических высотомеров после взлёта и при заходе (ФАП)"),
("Правила полётов", "Действия при опасных метеоявлениях без возможности доклада (ФАП)"),
("Правила полётов", "Обход Cb/TCU по РЛС (ФАП)"),
("Правила полётов", "Обход Cb/TCU визуально (ФАП)"),
("Правила полётов", "Посадка ниже установленного минимума — когда возможна (ФАП)"),
("Правила полётов", "Потерянная радиосвязь — критерии и действия после взлёта (ФАП)"),
("Правила полётов", "Задержка, при которой план полёта должен быть изменён (ФАП)"),
("Правила полётов", "Классификация ВС по сертифицированной взлётной массе (ФАП)"),
("Правила полётов", "Визуальные метеоусловия (DOC 4444/8168/Annex2)"),
("Правила полётов", "Визуальный заход на посадку"),
("Правила полётов", "Запасной аэродром — определение и виды"),
("Правила полётов", "Заход на посадку по приборам — методы, боковое/вертикальное наведение"),
("Правила полётов", "Конечный этап захода (Final approach)"),
("Правила полётов", "Схема захода по приборам — классификация"),
("Правила полётов", "Сообщения УВД, которые всегда повторяются экипажем"),
("Правила полётов", "Элементы первоначального вызова УВД при смене канала"),
("Правила полётов", "MAYDAY & PANPAN call"),
("Правила полётов", "Действия при аварийном снижении"),
("Правила полётов", "Определение MSA и MEA"),
("Правила полётов", "Заход по прямой vs по кругу (Circling)"),
("Правила полётов", "Определение ОСА/Н для схем захода"),
("Правила полётов", "Участки схемы захода на посадку (начальный/промежуточный/конечный/уход на 2-й круг)"),
("Правила полётов", "Потеря радиосвязи в зоне радиолокационного УВД"),
("Правила полётов", "Радиолокационное наведение при вылете — когда допускается"),
("Правила полётов", "Скорости в зонах ожидания по секторам захода"),
("Правила полётов", "Максимальные скорости circling, радиус зоны, уход на 2-й круг"),
("Правила полётов", "Зона ожидания — скорости, крен, время, сектора входа"),
("Правила полётов", "NADP1"),
("Правила полётов", "NADP2"),

("Заправка ВС топливом", "Разлив топлива при заправке под самолётом"),
("Заправка ВС топливом", "Ограничения по системам самолёта во время заправки"),
("Заправка ВС топливом", "Заправка в автоматическом режиме"),
("Заправка ВС топливом", "Заправка в ручном режиме"),
("Заправка ВС топливом", "Заправка при отсутствии электропитания"),
("Заправка ВС топливом", "Процедура слива топлива"),
("Заправка ВС топливом", "Неравномерная заправка — превышение дисбаланса по бакам"),

("QRH", "Предназначение QRH, какие процедуры содержит"),
("QRH", "Abnormal and Emergency task sharing"),
("QRH", "Memory Items"),
("QRH", "Initiation of procedures"),
("QRH", "QRH procedure layout (title, black square, black dot, indentation)"),
("QRH", "Use of Summaries"),
("QRH", "Vapp determination (normal conditions)"),
("QRH", "Практический пример abn/emerg checklist"),
("QRH", "Таблицы и графики IN FLIGHT PERFORMANCE"),
("QRH", "ECAM advisory table"),
("QRH", "Computer reset"),
("QRH", "Vapp determination (abnormal/emergency)"),
("QRH", "Выбор механизации при abnormal/emergency"),
("QRH", "Применение OEB (red, ECAM entry, Status entry, reminder)"),
("QRH", "Увеличение расхода топлива при отказах"),

("Оформление тех. документации", "Ведение ATLB"),
("Оформление тех. документации", "Приём-передача ВС от ИТС экипажу и обратно, смена на эстафете"),
("Оформление тех. документации", "Оформление дефекта по CDL в ATLB"),
("Оформление тех. документации", "Оформление приёма-передачи ВС в ATLB"),
("Оформление тех. документации", "Заполнение технического акта на задержку рейса"),
("Оформление тех. документации", "Оформление документации при проверке SAFA"),
("Оформление тех. документации", "Предполётная и послеполётная проверка комплекта документации"),
("Оформление тех. документации", "Содержание папки сертификатов, действия при нарушении пломбировки"),

("ECAM", "Color coding"),
("ECAM", "Warning/Caution classification — описание, звук, индикация"),
("ECAM", "Зачем нажимать MASTER WARNING/CAUTION"),
("ECAM", "Если сообщение с порядком действий пропало с ECAM"),
("ECAM", "Когда обращаться к QRH при ADVISORY"),
("ECAM", "T.O. INHIBIT и LDG INHIBIT — фазы полёта"),
("ECAM", "ECAM philosophy"),

("Abnormal Procedures", "Memory Items"),
("Abnormal Procedures", "Abnormal, которые наизусть (RTO и др.)"),
("Abnormal Procedures", "OEI: Engine failure at low speed"),
("Abnormal Procedures", "OEI: Engine failure after V1"),
("Abnormal Procedures", "OEI: Engine failure during cruise"),
("Abnormal Procedures", "The standard strategy"),
("Abnormal Procedures", "The obstacle strategy"),
("Abnormal Procedures", "The fixed speed strategy"),
]

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
# База данных (PostgreSQL)
# ---------------------------------------------------------------------------

def get_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS quests (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            text TEXT NOT NULL,
            sphere TEXT NOT NULL,
            type TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sphere_xp (
            chat_id BIGINT NOT NULL,
            sphere TEXT NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, sphere)
        );
        CREATE TABLE IF NOT EXISTS daily_log (
            chat_id BIGINT NOT NULL,
            day TEXT NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, day)
        );
        CREATE TABLE IF NOT EXISTS a330 (
            chat_id BIGINT NOT NULL,
            block TEXT NOT NULL,
            status INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, block)
        );
        CREATE TABLE IF NOT EXISTS a330_questions (
            id SERIAL PRIMARY KEY,
            block TEXT NOT NULL,
            text TEXT NOT NULL,
            UNIQUE (block, text)
        );
        CREATE TABLE IF NOT EXISTS a330_question_stats (
            chat_id BIGINT NOT NULL,
            question_id INTEGER NOT NULL REFERENCES a330_questions(id) ON DELETE CASCADE,
            last_result TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            last_reviewed TIMESTAMP,
            PRIMARY KEY (chat_id, question_id)
        );
        """
    )
    cur.close()
    conn.close()
    seed_questions()


def seed_questions():
    conn = get_conn()
    cur = conn.cursor()
    for block, text in QUESTIONS_SEED:
        cur.execute(
            """INSERT INTO a330_questions (block, text) VALUES (%s, %s)
               ON CONFLICT (block, text) DO NOTHING""",
            (block, text),
        )
    cur.close()
    conn.close()


def ensure_user(chat_id: int):
    conn = get_conn()
    cur = conn.cursor()
    for sid, _, _ in SPHERES:
        cur.execute(
            "INSERT INTO sphere_xp (chat_id, sphere, xp) VALUES (%s, %s, 0) "
            "ON CONFLICT (chat_id, sphere) DO NOTHING",
            (chat_id, sid),
        )
    for block in A330_BLOCKS:
        cur.execute(
            "INSERT INTO a330 (chat_id, block, status) VALUES (%s, %s, 0) "
            "ON CONFLICT (chat_id, block) DO NOTHING",
            (chat_id, block),
        )
    cur.close()
    conn.close()


def get_total_xp(chat_id: int) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(xp), 0) AS s FROM sphere_xp WHERE chat_id = %s", (chat_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["s"]


def get_sphere_xp(chat_id: int) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sphere, xp FROM sphere_xp WHERE chat_id = %s", (chat_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["sphere"]: r["xp"] for r in rows}


def bump_sphere_xp(chat_id: int, sphere: str, delta: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO sphere_xp (chat_id, sphere, xp) VALUES (%s, %s, %s)
           ON CONFLICT (chat_id, sphere) DO UPDATE SET xp = GREATEST(0, sphere_xp.xp + EXCLUDED.xp)""",
        (chat_id, sphere, delta),
    )
    cur.close()
    conn.close()


def bump_daily_log(chat_id: int, delta: int):
    conn = get_conn()
    cur = conn.cursor()
    day = today_str()
    cur.execute(
        """INSERT INTO daily_log (chat_id, day, xp) VALUES (%s, %s, %s)
           ON CONFLICT (chat_id, day) DO UPDATE SET xp = GREATEST(0, daily_log.xp + EXCLUDED.xp)""",
        (chat_id, day, delta),
    )
    cur.close()
    conn.close()


def add_quest(chat_id: int, text: str, sphere: str, qtype: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO quests (chat_id, text, sphere, type, done) VALUES (%s, %s, %s, %s, 0)",
        (chat_id, text, sphere, qtype),
    )
    cur.close()
    conn.close()


def get_quests(chat_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM quests WHERE chat_id = %s ORDER BY id", (chat_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_quest(chat_id: int, quest_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM quests WHERE chat_id = %s AND id = %s", (chat_id, quest_id))
    row = cur.fetchone()
    cur.close()
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
    cur = conn.cursor()
    cur.execute("UPDATE quests SET done = %s WHERE id = %s", (will_be_done, quest_id))
    cur.close()
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
    cur = conn.cursor()
    cur.execute("DELETE FROM quests WHERE id = %s", (quest_id,))
    cur.close()
    conn.close()


def new_day(chat_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM quests WHERE chat_id = %s AND done = 1", (chat_id,))
    cur.close()
    conn.close()


def get_a330(chat_id: int) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT block, status FROM a330 WHERE chat_id = %s", (chat_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["block"]: r["status"] for r in rows}


def cycle_a330(chat_id: int, block: str):
    a330 = get_a330(chat_id)
    cur_status = a330.get(block, 0)
    nxt = (cur_status + 1) % 3
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE a330 SET status = %s WHERE chat_id = %s AND block = %s",
        (nxt, chat_id, block),
    )
    cur.close()
    conn.close()


def get_next_quiz_question(chat_id: int, block: str = None):
    """Возвращает следующий вопрос для квиза: сперва те, что ещё не отмечены
    как "знаю", затем случайно среди уже пройденных — для повторения."""
    conn = get_conn()
    cur = conn.cursor()
    if block:
        cur.execute(
            """
            SELECT q.id, q.block, q.text, s.last_result
            FROM a330_questions q
            LEFT JOIN a330_question_stats s
                ON s.question_id = q.id AND s.chat_id = %s
            WHERE q.block = %s
            ORDER BY (s.last_result IS NOT DISTINCT FROM 'correct') ASC, RANDOM()
            LIMIT 1
            """,
            (chat_id, block),
        )
    else:
        cur.execute(
            """
            SELECT q.id, q.block, q.text, s.last_result
            FROM a330_questions q
            LEFT JOIN a330_question_stats s
                ON s.question_id = q.id AND s.chat_id = %s
            ORDER BY (s.last_result IS NOT DISTINCT FROM 'correct') ASC, RANDOM()
            LIMIT 1
            """,
            (chat_id,),
        )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def record_quiz_answer(chat_id: int, question_id: int, result: str):
    conn = get_conn()
    cur = conn.cursor()
    correct_incr = 1 if result == "correct" else 0
    cur.execute(
        """INSERT INTO a330_question_stats (chat_id, question_id, last_result, attempts, correct_count, last_reviewed)
           VALUES (%s, %s, %s, 1, %s, NOW())
           ON CONFLICT (chat_id, question_id) DO UPDATE SET
               last_result = EXCLUDED.last_result,
               attempts = a330_question_stats.attempts + 1,
               correct_count = a330_question_stats.correct_count + %s,
               last_reviewed = NOW()""",
        (chat_id, question_id, result, correct_incr, correct_incr),
    )
    cur.close()
    conn.close()


def get_quiz_stats(chat_id: int):
    """Возвращает по блокам: всего вопросов и сколько сейчас в статусе correct."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT q.block,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE s.last_result = 'correct') AS known
        FROM a330_questions q
        LEFT JOIN a330_question_stats s
            ON s.question_id = q.id AND s.chat_id = %s
        GROUP BY q.block
        """,
        (chat_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["block"]: (r["total"], r["known"]) for r in rows}


def get_week_xp(chat_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT day, xp FROM daily_log WHERE chat_id = %s", (chat_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    by_day = {r["day"]: r["xp"] for r in rows}
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    return [(d, by_day.get(d, 0)) for d in days]


def get_streak(chat_id: int) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT day, xp FROM daily_log WHERE chat_id = %s", (chat_id,))
    rows = cur.fetchall()
    cur.close()
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
        [KeyboardButton(text="🎯 Квиз А330"), KeyboardButton(text="📈 График")],
        [KeyboardButton(text="📅 Неделя"), KeyboardButton(text="🔁 Новый день")],
        [KeyboardButton(text="💾 Экспорт")],
    ],
    resize_keyboard=True,
)


def sphere_kb() -> InlineKeyboardMarkup:
    rows = []
    for sid, label, icon in SPHERES:
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"sphere:{sid}")])
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


def a330_kb(chat_id: int) -> InlineKeyboardMarkup:
    a330 = get_a330(chat_id)
    rows = []
    for block in A330_BLOCKS:
        status = a330.get(block, 0)
        icon = ["⬜", "🟡", "✅"][status]
        rows.append([InlineKeyboardButton(text=f"{icon} {block}", callback_data=f"a330:{block}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Короткие коды блоков для callback_data (Telegram ограничивает 64 байтами)
BLOCK_CODE = {str(i): block for i, block in enumerate(A330_BLOCKS)}
BLOCK_CODE_REV = {block: str(i) for i, block in enumerate(A330_BLOCKS)}


def quiz_block_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🎲 Все блоки", callback_data="qzsel:A")]]
    for block in A330_BLOCKS:
        rows.append([InlineKeyboardButton(text=block, callback_data=f"qzsel:{BLOCK_CODE_REV[block]}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quiz_answer_kb(question_id: int, block_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅", callback_data=f"qz:c:{question_id}:{block_code}"),
                InlineKeyboardButton(text="🤔", callback_data=f"qz:p:{question_id}:{block_code}"),
                InlineKeyboardButton(text="❌", callback_data=f"qz:w:{question_id}:{block_code}"),
            ],
            [InlineKeyboardButton(text="🔚 Стоп", callback_data="qzstop")],
        ]
    )


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


def format_quiz_question(block: str, text: str) -> str:
    import html
    return f"🎯 <b>{html.escape(block)}</b>\n\n{html.escape(text)}\n\nОтветь сам себе, потом оцени:"


@router.message(F.text == "🎯 Квиз А330")
async def quiz_start(message: Message):
    ensure_user(message.chat.id)
    await message.answer("Какой блок тренируем?", reply_markup=quiz_block_kb())


@router.callback_query(F.data.startswith("qzsel:"))
async def cb_quiz_select(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    block = None if code == "A" else BLOCK_CODE.get(code)
    row = get_next_quiz_question(callback.message.chat.id, block)
    if not row:
        await callback.message.edit_text("В этом блоке пока нет вопросов.")
        await callback.answer()
        return
    await callback.message.edit_text(
        format_quiz_question(row["block"], row["text"]),
        reply_markup=quiz_answer_kb(row["id"], code),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("qz:"))
async def cb_quiz_answer(callback: CallbackQuery):
    _, result_code, qid_str, block_code = callback.data.split(":")
    result_map = {"c": "correct", "p": "partial", "w": "wrong"}
    result = result_map[result_code]
    record_quiz_answer(callback.message.chat.id, int(qid_str), result)

    block = None if block_code == "A" else BLOCK_CODE.get(block_code)
    row = get_next_quiz_question(callback.message.chat.id, block)
    mark = {"correct": "✅", "partial": "🤔", "wrong": "❌"}[result]
    if not row:
        await callback.message.edit_text(f"{mark} Записал.\n\nВопросы в этом блоке закончились — отличная работа! 🎉")
        await callback.answer()
        return
    await callback.message.edit_text(
        f"{mark} Записал.\n\n" + format_quiz_question(row["block"], row["text"]),
        reply_markup=quiz_answer_kb(row["id"], block_code),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "qzstop")
async def cb_quiz_stop(callback: CallbackQuery):
    await callback.message.edit_text("Квиз остановлен. Возвращайся, когда будешь готов — 🎯 Квиз А330.")
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


BG = "#0C1524"
PANEL = "#121D30"
LINE = "#2C3E5E"
AMBER = "#FFA51F"
TEAL = "#1FE0C9"
TEXT = "#E8EDF4"


def render_chart(chat_id: int) -> bytes:
    week = get_week_xp(chat_id)
    sphere_xp = get_sphere_xp(chat_id)
    dow = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]

    week_labels = [dow[date.fromisoformat(d).weekday()] for d, _ in week]
    week_values = [xp for _, xp in week]

    sphere_labels = [f"{icon} {label}" for _, label, icon in SPHERES]
    sphere_values = [sphere_xp.get(sid, 0) for sid, _, _ in SPHERES]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5))
    fig.patch.set_facecolor(BG)

    ax1.bar(week_labels, week_values, color=AMBER)
    ax1.set_title("XP за неделю", color=TEXT, fontsize=13)

    ax2.barh(sphere_labels[::-1], sphere_values[::-1], color=TEAL)
    ax2.set_title("XP по сферам", color=TEXT, fontsize=13)

    for ax in (ax1, ax2):
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=TEXT, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(LINE)
        ax.grid(axis="x" if ax is ax2 else "y", color=LINE, alpha=0.4)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


@router.message(F.text == "📈 График")
async def show_chart(message: Message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    image_bytes = render_chart(chat_id)
    await message.answer_photo(
        BufferedInputFile(image_bytes, filename="chart.png"),
        caption="XP за неделю и по сферам",
    )


@router.message(F.text == "💾 Экспорт")
async def export_cmd(message: Message):
    chat_id = message.chat.id
    ensure_user(chat_id)
    data = export_data(chat_id)
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > 3500:
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
    if not DATABASE_URL:
        raise RuntimeError(
            "Не задан DATABASE_URL. Добавь PostgreSQL к проекту в Railway и подключи переменную."
        )
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    log.info("Life-RPG бот запущен, начинаю polling…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
