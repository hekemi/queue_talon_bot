import asyncio
import logging
import os

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


class EditorStates(StatesGroup):
    waiting_change_talon = State()
    waiting_new_number = State()
    waiting_delete_talon = State()


def get_editor_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Изменить номер")],
            [KeyboardButton(text="🗑 Удалить из очереди")],
            [KeyboardButton(text="🔄 Обновить очередь")],
            [KeyboardButton(text="↩️ Назад")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_USERNAMES = {"hekemi0", "forlornbark113"}


def is_admin(message: Message) -> bool:
    username = message.from_user.username if message.from_user else None
    return username is not None and username.lower() in ADMIN_USERNAMES


def get_main_keyboard(message: Message) -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton(text="🎟 Взять талон"),
            KeyboardButton(text="❌ Освободить место"),
        ],
        [
            KeyboardButton(text="📋 Показать очередь"),
        ],
    ]

    if is_admin(message):
        keyboard.append([KeyboardButton(text="🛠 Редактор очереди")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )

queue = []  # список user_id в очереди
talon_numbers = {}  # user_id -> номер талона
usernames = {}  # user_id -> username / имя


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

    try:
        number = int(text.strip())
    except ValueError:
        await message.answer("Введите номер талона числом.")
        return

    await add_talon(message, number)
    await state.clear()


@dp.message(Command("dismiss_talon"))
async def dismiss_talon(message: Message):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    if user_id not in queue:
        await message.answer("У вас нет активного талона.")
        return

    number = talon_numbers.pop(user_id)
    queue.remove(user_id)

    await message.answer(f"Ваш талон №{number} отменён.")


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
    await state.set_state(TalonStates.waiting_number)
    await message.answer(
        "Введите желаемый номер талона.\n"
        "Введите 0, чтобы занять ближайшее свободное место."
    )


@dp.message(F.text == "❌ Освободить место")
async def dismiss_talon_button(message: Message):
    await dismiss_talon(message)


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
        old_number = int(message.text.strip())
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
        new_number = int(message.text.strip())
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
        number = int(message.text.strip())
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