import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()
queue = []  # список user_id в очереди


@dp.message(Command("take_talon"))
async def take_talon(message: Message):
    if message.from_user is None:
        return

    user_id = message.from_user.id

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


async def main():
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN")

    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())