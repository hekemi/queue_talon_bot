import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramUnauthorizedError, TelegramNetworkError

TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()
queue = []  # список user_id в очереди
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
async def take_talon(message: Message):
    if message.from_user is None:
        return

    user_id = message.from_user.id
    display_name = get_display_name(message)
    if display_name is not None:
        usernames[user_id] = display_name

    if user_id in queue:
        position = queue.index(user_id) + 1
        await message.answer(f"Вы уже в очереди. Ваш номер: {position}")
        return

    queue.append(user_id)
    await message.answer(f"Вы заняли место в очереди. Ваш номер: {len(queue)}")


@dp.message(Command("dismiss_talon"))
async def dismiss_talon(message: Message):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    if user_id not in queue:
        await message.answer("У вас нет активного талона.")
        return

    position = queue.index(user_id) + 1
    queue.remove(user_id)
    await message.answer(f"Вы вышли из очереди. Ваш талон №{position} отменён.")


@dp.message(Command("queue"))
async def show_queue(message: Message):
    if not queue:
        await message.answer("Очередь пустая.")
        return

    lines = []
    for i, user_id in enumerate(queue, start=1):
        name = usernames.get(user_id, f"id:{user_id}")
        lines.append(f"{i}. {name}")

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