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

TOKEN = os.getenv("BOT_TOKEN")

class TalonStates(StatesGroup):
    waiting_number = State()

dp = Dispatcher(storage=MemoryStorage())

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🎟 Взять талон"),
            KeyboardButton(text="❌ Освободить место"),
        ],
        [
            KeyboardButton(text="📋 Показать очередь"),
        ],
    ],
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
        await message.answer("Введите целое число: 5 или 0.")
        return

    try:
        number = int(text.strip())
    except ValueError:
        await message.answer("Введите целое число. Например: 5 или 0.")
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
    if not queue:
        await message.answer("Очередь пустая.")
        return

    sorted_queue = sorted(queue, key=lambda user_id: talon_numbers[user_id])
    lines = []

    for user_id in sorted_queue:
        number = talon_numbers[user_id]
        name = usernames.get(user_id, f"id:{user_id}")
        lines.append(f"{number}. {name}")

    await message.answer("Текущая очередь:\n" + "\n".join(lines))


@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "Выберите действие:",
        reply_markup=main_keyboard,
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