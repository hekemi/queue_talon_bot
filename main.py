import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.exceptions import TelegramUnauthorizedError, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv

load_dotenv()

dp = Dispatcher(storage=MemoryStorage())


class TalonStates(StatesGroup):
    waiting_number = State()
    waiting_dismiss_confirmation = State()
    waiting_group_name = State()
    waiting_swap_target = State()


class EditorStates(StatesGroup):
    waiting_change_talon = State()
    waiting_new_number = State()
    waiting_delete_talon = State()
    waiting_swap_first = State()
    waiting_swap_second = State()
    waiting_schedule_type = State()
    waiting_schedule_action = State()
    waiting_schedule_timer = State()
    waiting_schedule_date = State()
    waiting_new_group_name = State()
    waiting_max_talon_limit = State()


OPEN_QUEUE_BUTTON = "🟢 Открыть очередь"
CLOSE_QUEUE_BUTTON = "🔴 Закрыть очередь"
CLEAR_QUEUE_BUTTON = "🧹 Очистить очередь"
MAX_TALON_LIMIT_BUTTON = "⚙️ Лимит мест"
SCHEDULE_QUEUE_BUTTON = "⏰ Планировщик"
CANCEL_SCHEDULE_BUTTON = "❌ Отменить расписание"
SCHEDULE_TIMER_BUTTON = "⏱ Таймер"
SCHEDULE_DATE_BUTTON = "📅 По дате и времени"
SCHEDULE_OPEN_BUTTON = "🟢 Открыть очередь"
SCHEDULE_CLOSE_BUTTON = "🔴 Закрыть очередь"
SCHEDULE_BACK_BUTTON = "↩️ Назад к редактору"
CONFIRM_BUTTON = "✅ Да"
CANCEL_BUTTON = "❌ Нет"
CHANGE_TALONNUM_BUTTON = "✏️ Изменить номер"
SWITCH_PLACES_BUTTON = "🔁 Поменять местами"
REMOVE_FROM_QUEUE_BUTTON = "🗑 Удалить из очереди"
UPDATE_QUEUE_BUTTON = "🔄 Обновить очередь"
QUEUE_REDACTOR_BUTTON = "🛠 Редактор очереди"
TAKE_TALON_BUTTON = "🎟 Взять талон"
RELEASE_PLACE_BUTTON = "❌ Освободить место"
SHOW_QUEUE_BUTTON = "📋 Показать очередь"
CHANGE_GROUP_BUTTON = "🔄 Сменить группу"
GROUPS_BUTTON = "👥 Управление группами"
ADD_GROUP_BUTTON = "➕ Добавить группу"
DELETE_GROUP_BUTTON = "🗑 Удалить группу"
BACK_TO_GROUPS_BUTTON = "↩️ К группам"
SWITCH_PLACES_USER_BUTTON = "🔁 Поменяться местами"
BACK_USER_BUTTON = "↩️ Назад"
DEFAULT_MAX_TALON_NUMBER = 50

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/queue.db"))


def get_db() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database() -> None:
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_groups (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE
            );
            """
        )


def get_groups() -> list[sqlite3.Row]:
    with get_db() as connection:
        return connection.execute(
            "SELECT id, name FROM groups ORDER BY name COLLATE NOCASE"
        ).fetchall()


def get_user_group_id(user_id: int) -> int | None:
    with get_db() as connection:
        row = connection.execute(
            "SELECT group_id FROM user_groups WHERE user_id = ?", (user_id,)
        ).fetchone()
    return int(row["group_id"]) if row else None


def get_group_name(group_id: int) -> str:
    with get_db() as connection:
        row = connection.execute(
            "SELECT name FROM groups WHERE id = ?", (group_id,)
        ).fetchone()
    return str(row["name"]) if row else "Неизвестная группа"


def assign_user_id_to_group(user_id: int, username: str, group_id: int) -> None:
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO user_groups (user_id, username, group_id)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                group_id = excluded.group_id
            """,
            (user_id, username, group_id),
        )


def assign_user_to_group(message: Message, group_id: int) -> None:
    if message.from_user is None:
        return
    user = message.from_user
    assign_user_id_to_group(
        user.id,
        user.username or user.full_name or str(user.id),
        group_id,
    )


def release_user_from_group_queue(user_id: int, group_id: int) -> None:
    group = group_queues.get(group_id)
    if group is None or user_id not in group.queue:
        return

    number = group.talon_numbers.pop(user_id)
    group.queue.remove(user_id)
    group.usernames.pop(user_id, None)
    normalize_after_delete(group, number)

    target_id = swap_requesters.pop(user_id, None)
    if target_id is not None:
        swap_requests.pop(target_id, None)
    request = swap_requests.pop(user_id, None)
    if request is not None:
        swap_requesters.pop(request.requester_id, None)


def switch_user_group(user_id: int, username: str, group_id: int) -> None:
    current_group_id = get_user_group_id(user_id)
    if current_group_id is not None:
        release_user_from_group_queue(user_id, current_group_id)
    assign_user_id_to_group(user_id, username, group_id)


def create_group(name: str) -> int | None:
    normalized_name = " ".join(name.split())
    if not normalized_name:
        return None
    try:
        with get_db() as connection:
            cursor = connection.execute(
                "INSERT INTO groups (name) VALUES (?)", (normalized_name,)
            )
            return int(cursor.lastrowid) if cursor.lastrowid is not None else None
    except sqlite3.IntegrityError:
        return None


def delete_group(group_id: int) -> bool:
    with get_db() as connection:
        cursor = connection.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    clear_group_swap_requests(group_id)
    group_queues.pop(group_id, None)
    return cursor.rowcount > 0


def get_group_selection_keyboard(
    include_new: bool = True,
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"👥 {row['name']}", callback_data=f"group:{row['id']}")]
        for row in get_groups()
    ]
    if include_new:
        buttons.append(
            [InlineKeyboardButton(text=ADD_GROUP_BUTTON, callback_data="group:new")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)



def get_editor_keyboard(message: Message) -> ReplyKeyboardMarkup:
    group_id = get_message_group_id(message)
    queue_state = get_group_queue(group_id) if group_id is not None else GroupQueue()
    queue_control_button = CLOSE_QUEUE_BUTTON if queue_state.is_open else OPEN_QUEUE_BUTTON
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=queue_control_button),
                KeyboardButton(text=UPDATE_QUEUE_BUTTON),
            ],
            [
                KeyboardButton(text=CHANGE_TALONNUM_BUTTON),
                KeyboardButton(text=SWITCH_PLACES_BUTTON),
            ],
            [
                KeyboardButton(text=REMOVE_FROM_QUEUE_BUTTON),
                KeyboardButton(text=CLEAR_QUEUE_BUTTON),
            ],
            [
                KeyboardButton(text=MAX_TALON_LIMIT_BUTTON),
                KeyboardButton(text=SCHEDULE_QUEUE_BUTTON),
            ],
            [
                KeyboardButton(text=CANCEL_SCHEDULE_BUTTON),
                KeyboardButton(text=BACK_USER_BUTTON),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_schedule_type_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SCHEDULE_TIMER_BUTTON)],
            [KeyboardButton(text=SCHEDULE_DATE_BUTTON)],
            [KeyboardButton(text=SCHEDULE_BACK_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_schedule_action_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SCHEDULE_OPEN_BUTTON)],
            [KeyboardButton(text=SCHEDULE_CLOSE_BUTTON)],
            [KeyboardButton(text=SCHEDULE_BACK_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_USERNAMES = {
    username.strip().lstrip("@").lower()
    for username in os.getenv("ADMIN_USERNAMES", "").split(",")
    if username.strip()
}


def is_admin(message: Message) -> bool:
    username = message.from_user.username if message.from_user else None
    return username is not None and username.lower() in ADMIN_USERNAMES


def is_admin_user(username: str | None) -> bool:
    return username is not None and username.lower() in ADMIN_USERNAMES


def get_main_keyboard(
    message: Message | None = None,
    admin: bool | None = None,
) -> ReplyKeyboardMarkup:
    is_user_admin = (
        is_admin(message)
        if admin is None and message is not None
        else bool(admin)
    )
    keyboard = [
        [
            KeyboardButton(text=TAKE_TALON_BUTTON),
            KeyboardButton(text=RELEASE_PLACE_BUTTON),
        ],
        [
            KeyboardButton(text=SWITCH_PLACES_USER_BUTTON),
        ],
        [
            KeyboardButton(text=SHOW_QUEUE_BUTTON),
            KeyboardButton(text=CHANGE_GROUP_BUTTON),
        ],
    ]

    if is_user_admin:
        keyboard.append(
            [
                KeyboardButton(text=QUEUE_REDACTOR_BUTTON),
                KeyboardButton(text=GROUPS_BUTTON),
            ]
        )

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_dismiss_confirmation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=CONFIRM_BUTTON),
                KeyboardButton(text=CANCEL_BUTTON),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_swap_targets_keyboard(user_id: int, group_id: int) -> ReplyKeyboardMarkup:
    group = get_group_queue(group_id)
    buttons = [
        KeyboardButton(text=str(group.talon_numbers[target_user_id]))
        for target_user_id in sorted(
            group.queue,
            key=lambda participant_id: group.talon_numbers[participant_id],
        )
        if target_user_id != user_id
    ]
    rows = [[button] for button in buttons]
    rows.append([KeyboardButton(text=BACK_USER_BUTTON)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_swap_confirmation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=CONFIRM_BUTTON),
                KeyboardButton(text=CANCEL_BUTTON),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@dataclass
class GroupQueue:
    queue: list[int] = field(default_factory=list)
    talon_numbers: dict[int, int] = field(default_factory=dict)
    usernames: dict[int, str] = field(default_factory=dict)
    is_open: bool = True
    max_talon_number: int = DEFAULT_MAX_TALON_NUMBER
    schedule_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    schedule_labels: dict[str, str] = field(default_factory=dict)


group_queues: dict[int, GroupQueue] = {}


@dataclass(frozen=True)
class SwapRequest:
    group_id: int
    requester_id: int
    requester_number: int
    target_id: int
    target_number: int


swap_requests: dict[int, SwapRequest] = {}
swap_requesters: dict[int, int] = {}


def get_group_queue(group_id: int) -> GroupQueue:
    return group_queues.setdefault(group_id, GroupQueue())


def get_message_group_id(message: Message) -> int | None:
    return (
        get_user_group_id(message.from_user.id)
        if message.from_user is not None
        else None
    )


async def require_group(message: Message) -> int | None:
    group_id = get_message_group_id(message)
    if group_id is None:
        await message.answer(
            "Сначала выберите группу:",
            reply_markup=get_group_selection_keyboard(),
        )
    return group_id


def get_display_name(message: Message) -> str | None:
    if message.from_user is None:
        return None

    user = message.from_user
    if user.username:
        return f"@{user.username}"
    if user.full_name:
        return user.full_name
    return str(user.id)


def clear_swap_request(requester_id: int, target_id: int) -> None:
    swap_requests.pop(target_id, None)
    swap_requesters.pop(requester_id, None)


def clear_group_swap_requests(group_id: int) -> None:
    for target_id, request in list(swap_requests.items()):
        if request.group_id == group_id:
            clear_swap_request(request.requester_id, target_id)


async def add_talon(message: Message, number: int):
    if message.from_user is None:
        return

    user_id = message.from_user.id
    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)

    if not group.is_open and not is_admin(message):
        await message.answer("Очередь закрыта для новых участников.")
        return

    if user_id in group.talon_numbers:
        await message.answer(
            f"Вы уже в очереди. Ваш талон №{group.talon_numbers[user_id]}"
        )
        return

    occupied_numbers = set(group.talon_numbers.values())

    if number == 0:
        number = get_next_available_talon_number(group)
        if number == 0:
            await message.answer(
                f"Все места до лимита ({group.max_talon_number}) заняты."
            )
            return
    elif number < 1:
        await message.answer("Введите положительный номер или 0.")
        return
    elif not check_talon_limit(group, number):
        await message.answer(
            f"Номер талона превышает лимит очереди ({group.max_talon_number})."
        )
        return
    elif number in occupied_numbers:
        await message.answer(f"Талон №{number} уже занят.")
        return

    # Если пользователь ввёл 0, мы берём ближайшее доступное место,
    # но только внутри лимита очереди. Это сохраняет корректную структуру
    # данных и не даёт "размножаться" длинным слотам.
    group.talon_numbers[user_id] = number
    group.queue.append(user_id)

    display_name = get_display_name(message)
    if display_name is not None:
        group.usernames[user_id] = display_name

    await message.answer(f"Вы заняли место. Ваш талон №{number}")


# Это обработчик команды /take_talon. Нужен для работы с inline
@dp.message(Command("take_talon"))
async def take_talon(message: Message, command: CommandObject):
    if not command.args:
        await add_talon(message, 0)
        return

    try:
        number = int(command.args.strip())
    except ValueError:
        await message.answer("Введите номер талона числом.")
        return

    await add_talon(message, number)


@dp.message(TalonStates.waiting_number, F.text)
async def process_talon_number(message: Message, state: FSMContext):
    text = message.text
    if text is None:
        await message.answer("Введите номер талона числом.")
        return

    if text == RELEASE_PLACE_BUTTON:
        await request_dismiss_talon(message, state)
        return

    try:
        number = int(text.strip())
    except ValueError:
        await message.answer("Введите номер талона числом.")
        return

    await add_talon(message, number)
    await state.clear()


async def request_dismiss_talon(message: Message, state: FSMContext):
    if message.from_user is None:
        return

    user_id = message.from_user.id
    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)

    if user_id not in group.queue:
        await state.clear()
        await message.answer(
            "У вас нет активного талона.",
            reply_markup=get_main_keyboard(message),
        )
        return

    await state.clear()
    await state.set_state(TalonStates.waiting_dismiss_confirmation)
    await message.answer(
        "Вы действительно хотите выйти из очереди?",
        reply_markup=get_dismiss_confirmation_keyboard(),
    )


async def remove_talon(message: Message, state: FSMContext):
    if message.from_user is None:
        return

    user_id = message.from_user.id
    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)

    if user_id not in group.queue:
        await state.clear()
        await message.answer(
            "У вас нет активного талона.",
            reply_markup=get_main_keyboard(message),
        )
        return

    number = group.talon_numbers[user_id]
    release_user_from_group_queue(user_id, group_id)

    await state.clear()
    await message.answer(
        f"Ваш талон №{number} отменён.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(Command("dismiss_talon"))
async def dismiss_talon_command(message: Message, state: FSMContext):
    await request_dismiss_talon(message, state)


async def set_queue_status(message: Message, is_open: bool):
    if not is_admin(message):
        return
    group_id = await require_group(message)
    if group_id is None:
        return

    apply_queue_status(group_id, is_open)
    status = "открыта" if is_open else "закрыта"
    await message.answer(
        f"Очередь {status} для новых участников.",
        reply_markup=get_editor_keyboard(message),
    )


def apply_queue_status(group_id: int, is_open: bool) -> None:
    get_group_queue(group_id).is_open = is_open


def cancel_queue_schedule(group_id: int, action: str) -> bool:
    group = get_group_queue(group_id)
    task = group.schedule_tasks.pop(action, None)
    group.schedule_labels.pop(action, None)
    if task is None:
        return False
    task.cancel()
    return True


def schedule_queue_action(group_id: int, action: str, delay: float, label: str) -> None:
    group = get_group_queue(group_id)
    cancel_queue_schedule(group_id, action)

    async def run_scheduled_action() -> None:
        try:
            await asyncio.sleep(delay)
            apply_queue_status(group_id, action == "open")
            group.schedule_tasks.pop(action, None)
            group.schedule_labels.pop(action, None)
            logging.info("Очередь автоматически %s.", "открыта" if action == "open" else "закрыта")
        except asyncio.CancelledError:
            raise

    group.schedule_labels[action] = label
    group.schedule_tasks[action] = asyncio.create_task(run_scheduled_action())


def parse_schedule_request(text: str) -> tuple[str, float, str] | None:
    parts = text.strip().split(maxsplit=3)
    if len(parts) < 3:
        return None

    schedule_type, action = parts[0].lower(), parts[1].lower()
    action_aliases = {"открыть": "open", "закрыть": "close", "open": "open", "close": "close"}
    action = action_aliases.get(action, "")
    if schedule_type in {"таймер", "timer"}:
        if action not in {"open", "close"}:
            return None
        try:
            minutes = float(parts[2].replace(",", "."))
        except ValueError:
            return None
        if minutes <= 0:
            return None
        return action, minutes * 60, f"через {minutes:g} мин."

    if schedule_type not in {"дата", "date"} or action not in {"open", "close"} or len(parts) < 4:
        return None

    try:
        scheduled_at = datetime.strptime(
            f"{parts[2]} {parts[3]}",
            "%d.%m.%Y %H:%M",
        ).astimezone()
    except ValueError:
        try:
            scheduled_at = datetime.strptime(
                f"{parts[2]} {parts[3]}",
                "%Y-%m-%d %H:%M",
            ).astimezone()
        except ValueError:
            return None

    delay = (scheduled_at - datetime.now().astimezone()).total_seconds()
    if delay <= 0:
        return None
    return action, delay, scheduled_at.strftime("%d.%m.%Y %H:%M %Z")


async def finish_schedule(
    message: Message,
    state: FSMContext,
    action: str,
    delay: float,
    label: str,
) -> None:
    group_id = await require_group(message)
    if group_id is None:
        return
    schedule_queue_action(group_id, action, delay, label)
    status = "открытие" if action == "open" else "закрытие"
    await state.clear()
    await message.answer(
        f"Запланировано {status} очереди {label}.",
        reply_markup=get_editor_keyboard(message),
    )


def get_queue_schedule_text(group_id: int) -> str:
    current_schedule_labels = get_group_queue(group_id).schedule_labels
    if not current_schedule_labels:
        return "Активных расписаний нет."
    lines = [
        f"{'Открытие' if action == 'open' else 'Закрытие'}: {label}"
        for action, label in current_schedule_labels.items()
    ]
    return "Активные расписания:\n" + "\n".join(lines)


@dp.message(Command("schedule_queue"))
async def schedule_queue_command(
    message: Message,
    command: CommandObject,
    state: FSMContext,
):
    if not command.args:
        await message.answer(
            "Используйте:\n"
            "/schedule_queue timer open 30\n"
            "/schedule_queue timer close 15\n"
            "/schedule_queue date open 03.11.2026 19:00\n"
            "/schedule_queue date close 03.11.2026 20:00"
        )
        return
    if not is_admin(message):
        return
    parsed = parse_schedule_request(command.args)
    if parsed is None:
        await message.answer("Не удалось распознать расписание. Проверьте формат команды.")
        return
    action, delay, label = parsed
    await finish_schedule(message, state, action, delay, label)


@dp.message(Command("cancel_schedule"))
async def cancel_schedule_command(message: Message):
    if not is_admin(message):
        return
    group_id = await require_group(message)
    if group_id is None:
        return
    cancelled = sum(
        cancel_queue_schedule(group_id, action) for action in ("open", "close")
    )
    text = "Расписания отменены." if cancelled else "Активных расписаний нет."
    await message.answer(text, reply_markup=get_editor_keyboard(message))


@dp.message(F.text == SCHEDULE_QUEUE_BUTTON)
async def schedule_queue_button(message: Message, state: FSMContext):
    if not is_admin(message):
        return
    await state.set_state(EditorStates.waiting_schedule_type)
    await message.answer(
        "Как запланировать изменение очереди?",
        reply_markup=get_schedule_type_keyboard(),
    )


@dp.message(EditorStates.waiting_schedule_type, F.text == SCHEDULE_TIMER_BUTTON)
async def choose_schedule_timer(message: Message, state: FSMContext):
    await state.update_data(schedule_type="timer")
    await state.set_state(EditorStates.waiting_schedule_action)
    await message.answer("Что сделать по таймеру?", reply_markup=get_schedule_action_keyboard())


@dp.message(EditorStates.waiting_schedule_type, F.text == SCHEDULE_DATE_BUTTON)
async def choose_schedule_date(message: Message, state: FSMContext):
    await state.update_data(schedule_type="date")
    await state.set_state(EditorStates.waiting_schedule_action)
    await message.answer("Что сделать по дате?", reply_markup=get_schedule_action_keyboard())


@dp.message(EditorStates.waiting_schedule_action, F.text.in_({SCHEDULE_OPEN_BUTTON, SCHEDULE_CLOSE_BUTTON}))
async def choose_schedule_action(message: Message, state: FSMContext):
    action = "open" if message.text == SCHEDULE_OPEN_BUTTON else "close"
    data = await state.get_data()
    await state.update_data(action=action)
    if data.get("schedule_type") == "timer":
        await state.set_state(EditorStates.waiting_schedule_timer)
        await message.answer("Через сколько минут изменить очередь? Введите положительное число.")
    else:
        await state.set_state(EditorStates.waiting_schedule_date)
        await message.answer(
            "Введите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ\n"
            "Например: 03.11.2026 19:00"
        )


@dp.message(EditorStates.waiting_schedule_timer, F.text)
async def process_schedule_timer(message: Message, state: FSMContext):
    if message.text is None:
        return
    try:
        minutes = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Введите количество минут числом.")
        return
    if minutes <= 0:
        await message.answer("Количество минут должно быть больше нуля.")
        return
    data = await state.get_data()
    await finish_schedule(
        message,
        state,
        data["action"],
        minutes * 60,
        f"через {minutes:g} мин.",
    )


@dp.message(EditorStates.waiting_schedule_date, F.text)
async def process_schedule_date(message: Message, state: FSMContext):
    if message.text is None:
        return
    data = await state.get_data()
    parsed = parse_schedule_request(f"дата {data['action']} {message.text}")
    if parsed is None:
        await message.answer(
            "Не удалось распознать дату. Используйте формат ДД.ММ.ГГГГ ЧЧ:ММ "
            "и укажите дату в будущем."
        )
        return
    action, delay, label = parsed
    await finish_schedule(message, state, action, delay, label)


@dp.message(F.text == SCHEDULE_BACK_BUTTON)
async def back_from_scheduler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Редактор очереди.", reply_markup=get_editor_keyboard(message))


@dp.message(F.text == CANCEL_SCHEDULE_BUTTON)
async def cancel_schedule_button(message: Message):
    await cancel_schedule_command(message)


@dp.message(Command("open_queue"))
async def open_queue_command(message: Message):
    await set_queue_status(message, True)


@dp.message(Command("close_queue"))
async def close_queue_command(message: Message):
    await set_queue_status(message, False)


@dp.message(Command("clear_queue"))
async def clear_queue_command(message: Message):
    if not is_admin(message):
        return

    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)
    if not group.queue:
        await message.answer("Очередь уже пустая.", reply_markup=get_editor_keyboard(message))
        return

    group.queue.clear()
    group.talon_numbers.clear()
    group.usernames.clear()
    clear_group_swap_requests(group_id)

    await message.answer(
        "Очередь успешно очищена.",
        reply_markup=get_editor_keyboard(message),
    )


@dp.message(Command("queue"))
async def show_queue(message: Message):
    group_id = await require_group(message)
    if group_id is not None:
        await message.answer(get_queue_text(group_id))


@dp.callback_query((F.data == "group:new") | F.data.regexp(r"^group:\d+$"))
async def group_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    data = callback.data or ""
    if data == "group:new":
        target_state = (
            EditorStates.waiting_new_group_name
            if is_admin_user(callback.from_user.username)
            else TalonStates.waiting_group_name
        )
        await state.set_state(target_state)
        await callback.message.answer("Введите название новой группы.")
        await callback.answer()
        return

    try:
        group_id = int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректная группа.", show_alert=True)
        return
    if not any(row["id"] == group_id for row in get_groups()):
        await callback.answer("Группа больше не существует.", show_alert=True)
        return

    switch_user_group(
        callback.from_user.id,
        callback.from_user.username
        or callback.from_user.full_name
        or str(callback.from_user.id),
        group_id,
    )
    await state.clear()
    await callback.message.answer(
        f"Вы выбрали группу «{get_group_name(group_id)}».",
        reply_markup=get_main_keyboard(
            admin=is_admin_user(callback.from_user.username),
        ),
    )
    await callback.answer()


@dp.message(TalonStates.waiting_group_name, F.text)
async def process_group_name(message: Message, state: FSMContext):
    if message.text is None:
        return
    group_id = create_group(message.text)
    if group_id is None:
        await message.answer("Группа с таким названием уже существует или имя пустое.")
        return
    if message.from_user is not None:
        switch_user_group(
            message.from_user.id,
            message.from_user.username
            or message.from_user.full_name
            or str(message.from_user.id),
            group_id,
        )
    await state.clear()
    await message.answer(
        f"Группа «{get_group_name(group_id)}» создана.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(F.text == CHANGE_GROUP_BUTTON)
async def change_group_button(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выберите новую группу:",
        reply_markup=get_group_selection_keyboard(),
    )


def get_group_admin_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"🗑 {row['name']}",
                callback_data=f"group:delete:{row['id']}",
            )
        ]
        for row in get_groups()
    ]
    buttons.append(
        [InlineKeyboardButton(text=ADD_GROUP_BUTTON, callback_data="group:admin_new")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(F.text == GROUPS_BUTTON)
async def groups_button(message: Message):
    if not is_admin(message):
        return
    await message.answer(
        "Управление группами:",
        reply_markup=get_group_admin_keyboard(),
    )


@dp.callback_query(F.data.startswith("group:delete:"))
async def delete_group_callback(callback: CallbackQuery):
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return

    if not is_admin_user(callback.from_user.username):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    # Теперь Pylance знает, что message — обычный Message
    await callback.message.edit_reply_markup(
        reply_markup=get_group_admin_keyboard()
    )
    await callback.answer("Группа удалена.")


@dp.callback_query(F.data == "group:admin_new")
async def admin_new_group_callback(callback: CallbackQuery, state: FSMContext):
    if callback.message is None or not is_admin_user(callback.from_user.username):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.set_state(EditorStates.waiting_new_group_name)
    await callback.message.answer("Введите название новой группы.")
    await callback.answer()


@dp.message(EditorStates.waiting_new_group_name, F.text)
async def process_admin_group_name(message: Message, state: FSMContext):
    if not is_admin(message) or message.text is None:
        return
    group_id = create_group(message.text)
    if group_id is None:
        await message.answer("Группа с таким названием уже существует или имя пустое.")
        return
    assign_user_to_group(message, group_id)
    await state.clear()
    await message.answer(
        f"Группа «{get_group_name(group_id)}» создана.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(Command("start"))
async def start_handler(message: Message):
    if get_message_group_id(message) is None:
        await message.answer(
            "Выберите группу для работы с очередью:",
            reply_markup=get_group_selection_keyboard(),
        )
        return
    await message.answer(
        "Выберите действие:",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(F.text == TAKE_TALON_BUTTON)
async def take_talon_button(message: Message, state: FSMContext):
    if await require_group(message) is None:
        return
    await show_queue(message)
    await state.set_state(TalonStates.waiting_number)
    await message.answer(
        f"Введите желаемый номер талона.\n"
        "Введите 0, чтобы занять ближайшее свободное место."
    )


@dp.message(F.text == SWITCH_PLACES_USER_BUTTON)
async def start_user_swap(message: Message, state: FSMContext):
    if message.from_user is None:
        return

    user_id = message.from_user.id
    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)
    if user_id not in group.talon_numbers:
        await message.answer(
            "Сначала займите место в очереди.",
            reply_markup=get_main_keyboard(message),
        )
        return

    available_targets = [
        participant_id for participant_id in group.queue if participant_id != user_id
    ]
    if not available_targets:
        await message.answer("Сейчас не с кем меняться местами.")
        return

    await state.set_state(TalonStates.waiting_swap_target)
    await message.answer(
        f"{get_queue_text(group_id)}\n\n"
        "Выберите номер, с владельцем которого хотите поменяться.",
        reply_markup=get_swap_targets_keyboard(user_id, group_id),
    )


@dp.message(TalonStates.waiting_swap_target, F.text == BACK_USER_BUTTON)
async def cancel_user_swap(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Обмен отменён.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(TalonStates.waiting_swap_target, F.text)
async def process_user_swap_target(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    bot = message.bot
    if bot is None:
        logging.error("Не удалось отправить запрос обмена: бот недоступен.")
        return

    requester_id = message.from_user.id
    group_id = await require_group(message)
    if group_id is None:
        await state.clear()
        return
    group = get_group_queue(group_id)
    try:
        target_number = int(message.text.strip())
    except ValueError:
        await message.answer("Выберите номер кнопкой ниже.")
        return

    requester_number = group.talon_numbers.get(requester_id)
    target_id = find_user_by_talon(group_id, target_number)
    if requester_number is None:
        await state.clear()
        await message.answer(
            "У вас больше нет места в очереди.",
            reply_markup=get_main_keyboard(message),
        )
        return
    if target_id is None or target_id == requester_id:
        await message.answer(
            "Это место уже недоступно. Выберите другой номер.",
            reply_markup=get_swap_targets_keyboard(requester_id, group_id),
        )
        return
    if target_id in swap_requests:
        await message.answer(
            "Владелец этого места уже рассматривает другой запрос. "
            "Выберите другой номер.",
            reply_markup=get_swap_targets_keyboard(requester_id, group_id),
        )
        return
    if requester_id in swap_requesters:
        await state.clear()
        await message.answer("У вас уже есть активный запрос на обмен.")
        return

    swap_requests[target_id] = SwapRequest(
        group_id=group_id,
        requester_id=requester_id,
        requester_number=requester_number,
        target_id=target_id,
        target_number=target_number,
    )
    swap_requesters[requester_id] = target_id
    await state.clear()

    requester_name = group.usernames.get(requester_id, f"id:{requester_id}")
    await bot.send_message(
        target_id,
        f"Пользователь {requester_name} предлагает поменяться местами: "
        f"№{target_number} ↔ №{requester_number}.\n"
        "Согласиться на обмен?",
        reply_markup=get_swap_confirmation_keyboard(),
    )
    await message.answer(
        f"Запрос отправлен владельцу места №{target_number}.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(F.text.in_({CONFIRM_BUTTON, CANCEL_BUTTON}))
async def process_user_swap_response(message: Message):
    if message.from_user is None:
        return
    bot = message.bot
    if bot is None:
        logging.error("Не удалось отправить ответ обмена: бот недоступен.")
        return

    target_id = message.from_user.id
    request = swap_requests.get(target_id)
    if request is None:
        await message.answer("Этот запрос на обмен уже неактуален.")
        return

    group = get_group_queue(request.group_id)
    requester_id = request.requester_id
    requester_number = request.requester_number
    target_number = request.target_number
    clear_swap_request(requester_id, target_id)

    if message.text == CANCEL_BUTTON:
        await message.answer(
            "Вы отказались от обмена.",
            reply_markup=get_main_keyboard(message),
        )
        await bot.send_message(
            requester_id,
            f"Владелец места №{target_number} отказался от обмена.",
        )
        return

    if (
        get_user_group_id(requester_id) != request.group_id
        or get_user_group_id(target_id) != request.group_id
        or group.talon_numbers.get(requester_id) != requester_number
        or group.talon_numbers.get(target_id) != target_number
    ):
        await message.answer(
            "Обмен невозможен: одно из мест уже изменилось.",
            reply_markup=get_main_keyboard(message),
        )
        await bot.send_message(
            requester_id,
            "Обмен не выполнен: одно из мест уже изменилось.",
        )
        return

    group.talon_numbers[requester_id], group.talon_numbers[target_id] = (
        target_number,
        requester_number,
    )
    await message.answer(
        f"Обмен выполнен. Теперь ваше место №{requester_number}.",
        reply_markup=get_main_keyboard(message),
    )
    await bot.send_message(
        requester_id,
        f"Обмен подтверждён. Теперь ваше место №{target_number}.",
    )


@dp.message(F.text == RELEASE_PLACE_BUTTON)
async def dismiss_talon_button(message: Message, state: FSMContext):
    await request_dismiss_talon(message, state)


@dp.message(TalonStates.waiting_dismiss_confirmation, F.text == CONFIRM_BUTTON)
async def confirm_dismiss_talon(message: Message, state: FSMContext):
    await remove_talon(message, state)


@dp.message(TalonStates.waiting_dismiss_confirmation, F.text == CANCEL_BUTTON)
async def cancel_dismiss_talon(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Вы остались в очереди.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(F.text == SHOW_QUEUE_BUTTON)
async def queue_button(message: Message):
    await show_queue(message)


@dp.message(F.text == QUEUE_REDACTOR_BUTTON)
async def queue_editor_button(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    await state.clear()
    group_id = await require_group(message)
    if group_id is None:
        return
    await message.answer(
        get_queue_text(group_id),
        reply_markup=get_editor_keyboard(message),
    )


@dp.message(F.text == UPDATE_QUEUE_BUTTON)
async def refresh_editor_queue(message: Message):
    if is_admin(message):
        group_id = await require_group(message)
        if group_id is None:
            return
        await message.answer(
            get_queue_text(group_id),
            reply_markup=get_editor_keyboard(message),
        )


@dp.message(F.text == OPEN_QUEUE_BUTTON)
async def open_queue_button(message: Message):
    await set_queue_status(message, True)


@dp.message(F.text == CLOSE_QUEUE_BUTTON)
async def close_queue_button(message: Message):
    await set_queue_status(message, False)


@dp.message(F.text == CLEAR_QUEUE_BUTTON)
async def clear_queue_button(message: Message):
    await clear_queue_command(message)


@dp.message(F.text == CHANGE_TALONNUM_BUTTON)
async def start_change_talon(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    await state.set_state(EditorStates.waiting_change_talon)
    await message.answer(
        "Введите текущий номер талона человека, которому нужно изменить номер."
    )


@dp.message(EditorStates.waiting_change_talon, F.text)
async def process_change_talon(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    try:
        old_number = int(message.text.strip()) # type: ignore
    except ValueError:
        await message.answer("Введите номер талона числом.")
        return

    group_id = await require_group(message)
    if group_id is None:
        return
    user_id = find_user_by_talon(group_id, old_number)

    if user_id is None:
        await message.answer("Человек с таким номером не найден.")
        return

    await state.update_data(user_id=user_id)
    await state.set_state(EditorStates.waiting_new_number)
    await message.answer("Введите новый номер талона.")


@dp.message(EditorStates.waiting_new_number, F.text)
async def process_new_talon_number(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    try:
        new_number = int(message.text.strip()) # type: ignore
    except ValueError:
        await message.answer("Введите номер талона числом.")
        return

    if new_number < 1:
        await message.answer("Номер должен быть положительным.")
        return

    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)
    if new_number in group.talon_numbers.values():
        await message.answer("Этот номер уже занят.")
        return

    data = await state.get_data()
    user_id = data["user_id"]
    old_number = group.talon_numbers[user_id]
    group.talon_numbers[user_id] = new_number

    await state.clear()
    await message.answer(
        f"Номер изменён: {old_number} → {new_number}\n\n{get_queue_text(group_id)}",
        reply_markup=get_editor_keyboard(message),
    )


@dp.message(F.text == REMOVE_FROM_QUEUE_BUTTON)
async def start_delete_talon(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    await state.set_state(EditorStates.waiting_delete_talon)
    await message.answer("Введите номер талона, которого нужно удалить.")


@dp.message(EditorStates.waiting_delete_talon, F.text)
async def process_delete_talon(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    try:
        number = int(message.text.strip()) # type: ignore
    except ValueError:
        await message.answer("Введите номер талона числом.")
        return

    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)
    user_id = find_user_by_talon(group_id, number)

    if user_id is None:
        await message.answer("Человек с таким номером не найден.")
        return

    release_user_from_group_queue(user_id, group_id)

    await state.clear()
    await message.answer(
        f"Талон №{number} удалён из очереди.\n\n{get_queue_text(group_id)}",
        reply_markup=get_editor_keyboard(message),
    )


@dp.message(F.text == BACK_USER_BUTTON)
async def back_from_editor(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выберите действие:",
        reply_markup=get_main_keyboard(message),
    )

@dp.message(F.text == SWITCH_PLACES_BUTTON)
async def start_swap_talons(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    await state.set_state(EditorStates.waiting_swap_first)
    await message.answer("Введите номер первого человека.")


@dp.message(EditorStates.waiting_swap_first, F.text)
async def process_swap_first(message: Message, state: FSMContext):
    if not is_admin(message) or message.text is None:
        return

    try:
        first_number = int(message.text.strip())
    except ValueError:
        await message.answer("Введите номер числом.")
        return

    group_id = await require_group(message)
    if group_id is None:
        return
    if find_user_by_talon(group_id, first_number) is None:
        await message.answer("Человек с таким номером не найден.")
        return

    await state.update_data(first_number=first_number)
    await state.set_state(EditorStates.waiting_swap_second)
    await message.answer("Введите номер второго человека.")


@dp.message(EditorStates.waiting_swap_second, F.text)
async def process_swap_second(message: Message, state: FSMContext):
    if not is_admin(message) or message.text is None:
        return

    try:
        second_number = int(message.text.strip())
    except ValueError:
        await message.answer("Введите номер числом.")
        return

    data = await state.get_data()
    first_number = data["first_number"]

    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)
    first_user_id = find_user_by_talon(group_id, first_number)
    second_user_id = find_user_by_talon(group_id, second_number)

    if first_user_id is None or second_user_id is None:
        await message.answer("Один из людей не найден.")
        return

    group.talon_numbers[first_user_id], group.talon_numbers[second_user_id] = (
        group.talon_numbers[second_user_id],
        group.talon_numbers[first_user_id],
    )

    await state.clear()
    await message.answer(
        f"Люди с номерами {first_number} и {second_number} поменялись местами.\n\n"
        f"{get_queue_text(group_id)}",
        reply_markup=get_editor_keyboard(message),
    )


@dp.message(F.text == MAX_TALON_LIMIT_BUTTON)
async def start_change_max_talon_limit(message: Message, state: FSMContext):
    """Запускает ввод нового лимита максимального номера талона.

    Администратор задаёт число, после чего в обработчике проходит проверка
    валидности: число должно быть целым и положительным. Это отдельный
    диалог, чтобы не смешивать логику редактирования очереди и настройки.
    """
    if not is_admin(message):
        return

    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)
    await state.set_state(EditorStates.waiting_max_talon_limit)
    await message.answer(
        f"Введите новый лимит мест в очереди. Текущее значение: {group.max_talon_number}."
    )


@dp.message(EditorStates.waiting_max_talon_limit, F.text)
async def process_max_talon_limit(message: Message, state: FSMContext):
    """Сохраняет лимит мест после валидации.

    После ввода администратор получает актуальный лимит в текущей группе.
    При этом ограничение гарантирует, что новые участники не смогут занять
    номер за пределами допустимого диапазона.
    """
    if not is_admin(message) or message.text is None:
        return

    try:
        limit = int(message.text.strip())
    except ValueError:
        await message.answer("Введите лимит числом, например: 50.")
        return

    if limit < 1:
        await message.answer("Лимит должен быть положительным числом.")
        return

    group_id = await require_group(message)
    if group_id is None:
        return
    group = get_group_queue(group_id)
    group.max_talon_number = limit

    await state.clear()
    await message.answer(
        f"Лимит очереди обновлён: {limit} мест.\n\n{get_queue_text(group_id)}",
        reply_markup=get_editor_keyboard(message),
    )


def get_queue_text(group_id: int) -> str:
    group = get_group_queue(group_id)
    if not group.queue:
        return "Очередь пустая."

    sorted_queue = sorted(group.queue, key=lambda user_id: group.talon_numbers[user_id])
    lines = []

    for user_id in sorted_queue:
        number = group.talon_numbers[user_id]
        name = group.usernames.get(user_id, f"id:{user_id}")
        lines.append(f"{number}. {name}")

    return "Текущая очередь:\n" + "\n".join(lines)

def normalize_after_delete(group: GroupQueue, deleted_number: int) -> None:
    """Убирает только блок пустот перед ближайшим следующим занятым номером.

    Примеры:
    - 1,2,4 после удаления 1 -> 1,3
    - 1,3,4 после удаления 1 -> 1,2
    """
    next_occupied = min(
        (number for number in group.talon_numbers.values() if number > deleted_number),
        default=None,
    )

    if next_occupied is None:
        return

    shift = next_occupied - deleted_number

    for user_id, number in list(group.talon_numbers.items()):
        if number >= next_occupied:
            group.talon_numbers[user_id] = number - shift

def find_user_by_talon(group_id: int, number: int) -> int | None:
    group = get_group_queue(group_id)
    for user_id in group.queue:
        if group.talon_numbers.get(user_id) == number:
            return user_id
    return None


def get_next_available_talon_number(group: GroupQueue, start_from: int = 1) -> int:
    """Возвращает ближайший свободный номер в пределах лимита.

    Если пользователь выбирает 0, бот пытается занять первый свободный слот,
    но не выходит за пределы max_talon_number. Это важно, чтобы очередь
    не росла бесконечно и не создавалась "пустая" длинная последовательность.
    """
    occupied_numbers = set(group.talon_numbers.values())
    candidate = start_from
    while candidate <= group.max_talon_number:
        if candidate not in occupied_numbers:
            return candidate
        candidate += 1
    return 0


def check_talon_limit(group: GroupQueue, number: int) -> bool:
    """Проверяет, что номер талона не выходит за лимит группы.

    Если номер выше максимального допустимого, новый участник не сможет
    занять место. Это защита от "длинных" номеров, которые портят очередь.
    """
    return 1 <= number <= group.max_talon_number


async def main():
    init_database()
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN")

    bot = Bot(token=TOKEN)

    try:
        me = await bot.get_me()
        print(f"Подключение к Telegram API успешно: @{me.username}")
    except TelegramUnauthorizedError:
        print("Ошибка: неверный BOT_TOKEN")
        await bot.session.close()
        return
    except TelegramNetworkError as e:
        print(f"Ошибка сети при подключении к Telegram API: {e}")
        await bot.session.close()
        return
    except Exception as e:
        print(f"Не удалось подключиться к Telegram API: {e}")
        await bot.session.close()
        return
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("Старые сообщения удалены. Бот запущен.")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())