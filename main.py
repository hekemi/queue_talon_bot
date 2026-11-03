import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup
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


OPEN_QUEUE_BUTTON = "🟢 Открыть очередь"
CLOSE_QUEUE_BUTTON = "🔴 Закрыть очередь"
CLEAR_QUEUE_BUTTON = "🧹 Очистить очередь"
SCHEDULE_QUEUE_BUTTON = "⏰ Планировщик"
CANCEL_SCHEDULE_BUTTON = "❌ Отменить расписание"
SCHEDULE_TIMER_BUTTON = "⏱ Таймер"
SCHEDULE_DATE_BUTTON = "📅 По дате и времени"
SCHEDULE_OPEN_BUTTON = "🟢 Открыть очередь"
SCHEDULE_CLOSE_BUTTON = "🔴 Закрыть очередь"
SCHEDULE_BACK_BUTTON = "↩️ Назад к редактору"
CONFIRM_DISMISS_BUTTON = "✅ Да, выйти"
CANCEL_DISMISS_BUTTON = "↩️ Нет, остаться"
CHANGE_TALONNUM_BUTTON = "✏️ Изменить номер"
SWITCH_PLACES_BUTTON = "🔁 Поменять местами"
REMOVE_FROM_QUEUE_BUTTON = "🗑 Удалить из очереди"
UPDATE_QUEUE_BUTTON = "🔄 Обновить очередь"
QUEUE_REDACTOR_BUTTON = "🛠 Редактор очереди"
TAKE_TALON_BUTTON = "🎟 Взять талон"
RELEASE_PLACE_BUTTON = "❌ Освободить место"
SHOW_QUEUE_BUTTON = "📋 Показать очередь"



def get_editor_keyboard() -> ReplyKeyboardMarkup:
    queue_control_button = (
        CLOSE_QUEUE_BUTTON if queue_is_open else OPEN_QUEUE_BUTTON
    )
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
                KeyboardButton(text=SCHEDULE_QUEUE_BUTTON),
                KeyboardButton(text=CANCEL_SCHEDULE_BUTTON),
            ],
            [KeyboardButton(text="↩️ Назад")],
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


def get_main_keyboard(message: Message) -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton(text=TAKE_TALON_BUTTON),
            KeyboardButton(text=RELEASE_PLACE_BUTTON),
        ],
        [
            KeyboardButton(text=SHOW_QUEUE_BUTTON),
        ],
    ]

    if is_admin(message):
        keyboard.append([KeyboardButton(text=QUEUE_REDACTOR_BUTTON)])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_dismiss_confirmation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=CONFIRM_DISMISS_BUTTON),
                KeyboardButton(text=CANCEL_DISMISS_BUTTON),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


queue = []  # список user_id в очереди
talon_numbers = {}  # user_id -> номер талона
usernames = {}  # user_id -> username / имя
queue_is_open = True
queue_schedule_tasks: dict[str, asyncio.Task] = {}
queue_schedule_labels: dict[str, str] = {}


def get_display_name(message: Message) -> str | None:
    if message.from_user is None:
        return None

    user = message.from_user
    if user.username:
        return f"@{user.username}"
    if user.full_name:
        return user.full_name
    return str(user.id)


async def add_talon(message: Message, number: int):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    if not queue_is_open and not is_admin(message):
        await message.answer("Очередь закрыта для новых участников.")
        return

    if user_id in talon_numbers:
        await message.answer(
            f"Вы уже в очереди. Ваш талон №{talon_numbers[user_id]}"
        )
        return

    occupied_numbers = set(talon_numbers.values())

    if number == 0:
        number = 1
        while number in occupied_numbers:
            number += 1
    elif number < 1:
        await message.answer("Введите положительный номер или 0.")
        return
    elif number in occupied_numbers:
        await message.answer(f"Талон №{number} уже занят.")
        return

    talon_numbers[user_id] = number
    queue.append(user_id)

    display_name = get_display_name(message)
    if display_name is not None:
        usernames[user_id] = display_name

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
        await message.answer("Введите номер или 0.")
        return

    await add_talon(message, number)


@dp.message(TalonStates.waiting_number, F.text)
async def process_talon_number(message: Message, state: FSMContext):
    text = message.text
    if text is None:
        await message.answer("Введите номер талона числом.")
        return

    if text == "❌ Освободить место":
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

    if user_id not in queue:
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

    if user_id not in queue:
        await state.clear()
        await message.answer(
            "У вас нет активного талона.",
            reply_markup=get_main_keyboard(message),
        )
        return

    number = talon_numbers.pop(user_id)
    queue.remove(user_id)

    normalize_queue_numbers()

    await state.clear()
    await message.answer(
        f"Ваш талон №{number} отменён.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(Command("dismiss_talon"))
async def dismiss_talon_command(message: Message, state: FSMContext):
    await request_dismiss_talon(message, state)


async def set_queue_status(message: Message, is_open: bool):
    global queue_is_open

    if not is_admin(message):
        return

    apply_queue_status(is_open)
    status = "открыта" if is_open else "закрыта"
    await message.answer(
        f"Очередь {status} для новых участников.",
        reply_markup=get_editor_keyboard(),
    )


def apply_queue_status(is_open: bool) -> None:
    global queue_is_open
    queue_is_open = is_open


def cancel_queue_schedule(action: str) -> bool:
    task = queue_schedule_tasks.pop(action, None)
    queue_schedule_labels.pop(action, None)
    if task is None:
        return False
    task.cancel()
    return True


def schedule_queue_action(action: str, delay: float, label: str) -> None:
    cancel_queue_schedule(action)

    async def run_scheduled_action() -> None:
        try:
            await asyncio.sleep(delay)
            apply_queue_status(action == "open")
            queue_schedule_tasks.pop(action, None)
            queue_schedule_labels.pop(action, None)
            logging.info("Очередь автоматически %s.", "открыта" if action == "open" else "закрыта")
        except asyncio.CancelledError:
            raise

    queue_schedule_labels[action] = label
    queue_schedule_tasks[action] = asyncio.create_task(run_scheduled_action())


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
    schedule_queue_action(action, delay, label)
    status = "открытие" if action == "open" else "закрытие"
    await state.clear()
    await message.answer(
        f"Запланировано {status} очереди {label}.",
        reply_markup=get_editor_keyboard(),
    )


def get_queue_schedule_text() -> str:
    if not queue_schedule_labels:
        return "Активных расписаний нет."
    lines = [
        f"{'Открытие' if action == 'open' else 'Закрытие'}: {label}"
        for action, label in queue_schedule_labels.items()
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
    cancelled = sum(
        cancel_queue_schedule(action) for action in ("open", "close")
    )
    text = "Расписания отменены." if cancelled else "Активных расписаний нет."
    await message.answer(text, reply_markup=get_editor_keyboard())


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
    await message.answer("Редактор очереди.", reply_markup=get_editor_keyboard())


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

    if not queue:
        await message.answer("Очередь уже пустая.", reply_markup=get_editor_keyboard())
        return

    queue.clear()
    talon_numbers.clear()
    usernames.clear()

    await message.answer(
        "Очередь успешно очищена.",
        reply_markup=get_editor_keyboard(),
    )


@dp.message(Command("queue"))
async def show_queue(message: Message):
    await message.answer(get_queue_text())


@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "Выберите действие:",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(F.text == "🎟 Взять талон")
async def take_talon_button(message: Message, state: FSMContext):
    await show_queue(message)
    await state.set_state(TalonStates.waiting_number)
    await message.answer(
        f"Введите желаемый номер талона.\n"
        "Введите 0, чтобы занять ближайшее свободное место."
    )


@dp.message(F.text == "❌ Освободить место")
async def dismiss_talon_button(message: Message, state: FSMContext):
    await request_dismiss_talon(message, state)


@dp.message(TalonStates.waiting_dismiss_confirmation, F.text == CONFIRM_DISMISS_BUTTON)
async def confirm_dismiss_talon(message: Message, state: FSMContext):
    await remove_talon(message, state)


@dp.message(TalonStates.waiting_dismiss_confirmation, F.text == CANCEL_DISMISS_BUTTON)
async def cancel_dismiss_talon(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Вы остались в очереди.",
        reply_markup=get_main_keyboard(message),
    )


@dp.message(F.text == "📋 Показать очередь")
async def queue_button(message: Message):
    await show_queue(message)


@dp.message(F.text == "🛠 Редактор очереди")
async def queue_editor_button(message: Message, state: FSMContext):
    if not is_admin(message):
        return

    await state.clear()
    await message.answer(
        get_queue_text(),
        reply_markup=get_editor_keyboard(),
    )


@dp.message(F.text == "🔄 Обновить очередь")
async def refresh_editor_queue(message: Message):
    if is_admin(message):
        await message.answer(
            get_queue_text(),
            reply_markup=get_editor_keyboard(),
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


@dp.message(F.text == "✏️ Изменить номер")
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

    user_id = find_user_by_talon(old_number)

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

    if new_number in talon_numbers.values():
        await message.answer("Этот номер уже занят.")
        return

    data = await state.get_data()
    user_id = data["user_id"]
    old_number = talon_numbers[user_id]
    talon_numbers[user_id] = new_number

    await state.clear()
    await message.answer(
        f"Номер изменён: {old_number} → {new_number}\n\n{get_queue_text()}",
        reply_markup=get_editor_keyboard(),
    )


@dp.message(F.text == "🗑 Удалить из очереди")
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

    user_id = find_user_by_talon(number)

    if user_id is None:
        await message.answer("Человек с таким номером не найден.")
        return

    talon_numbers.pop(user_id, None)
    usernames.pop(user_id, None)
    queue.remove(user_id)

    normalize_queue_numbers()

    await state.clear()
    await message.answer(
        f"Талон №{number} удалён из очереди.\n\n{get_queue_text()}",
        reply_markup=get_editor_keyboard(),
    )


@dp.message(F.text == "↩️ Назад")
async def back_from_editor(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выберите действие:",
        reply_markup=get_main_keyboard(message),
    )

@dp.message(F.text == "🔁 Поменять местами")
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

    if find_user_by_talon(first_number) is None:
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

    first_user_id = find_user_by_talon(first_number)
    second_user_id = find_user_by_talon(second_number)

    if first_user_id is None or second_user_id is None:
        await message.answer("Один из людей не найден.")
        return

    talon_numbers[first_user_id], talon_numbers[second_user_id] = (
        talon_numbers[second_user_id],
        talon_numbers[first_user_id],
    )

    await state.clear()
    await message.answer(
        f"Люди с номерами {first_number} и {second_number} поменялись местами.\n\n"
        f"{get_queue_text()}",
        reply_markup=get_editor_keyboard(),
    )

def get_queue_text() -> str:
    if not queue:
        return "Очередь пустая."

    sorted_queue = sorted(queue, key=lambda user_id: talon_numbers[user_id])
    lines = []

    for user_id in sorted_queue:
        number = talon_numbers[user_id]
        name = usernames.get(user_id, f"id:{user_id}")
        lines.append(f"{number}. {name}")

    return "Текущая очередь:\n" + "\n".join(lines)

def normalize_queue_numbers() -> None:
    """Перенумеровывает очередь с первого номера без пропусков."""
    ordered_users = sorted(
        queue,
        key=lambda user_id: talon_numbers.get(user_id, 0),
    )

    for number, user_id in enumerate(ordered_users, start=1):
        talon_numbers[user_id] = number

def find_user_by_talon(number: int) -> int | None:
    for user_id in queue:
        if talon_numbers.get(user_id) == number:
            return user_id
    return None


async def main():
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