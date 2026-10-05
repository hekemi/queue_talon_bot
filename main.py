import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.exceptions import TelegramUnauthorizedError, TelegramNetworkError

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()
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


@dp.message(Command("take_talon"))
async def take_talon(message: Message, command: CommandObject):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    if user_id in queue:
        number = talon_numbers[user_id]
        await message.answer(f"Вы уже в очереди. Ваш талон №{number}")
        return

    last_number = max(talon_numbers.values(), default=0)

    if command.args:
        try:
            number = int(command.args.strip())
        except ValueError:
            await message.answer("Используйте номер: /take_talon 5")
            return

        if number <= last_number:
            await message.answer(
                f"Номер должен быть больше последнего талона: {last_number}"
            )
            return
    else:
        number = last_number + 1

    queue.append(user_id)
    talon_numbers[user_id] = number

    display_name = get_display_name(message)
    if display_name is not None:
        usernames[user_id] = display_name

    await message.answer(f"Вы заняли место. Ваш талон №{number}")


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
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())