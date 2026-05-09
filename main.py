import asyncio
import datetime
import json
import os
import sys

import aiohttp
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print("Ошибка: BOT_TOKEN и CHAT_ID должны быть установлены в .env файле.")
    sys.exit(1)

SUBSCRIBERS_FILE = "subscribers.json"

subscribers = {}
last_notification_sent = False
just_switched_to_monitor = False

def load_data() -> None:
    """Загрузить подписчиков из JSON-файла, если он существует."""
    global subscribers
    if not os.path.exists(SUBSCRIBERS_FILE):
        print(f"Файл {SUBSCRIBERS_FILE} не найден, начинаем с пустого списка подписчиков.")
        subscribers = {}
        return

    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as file:
            subscribers = json.load(file)
        print(f"Данные загружены из {SUBSCRIBERS_FILE}. Всего подписчиков: {len(subscribers)}")
    except (json.JSONDecodeError, OSError) as error:
        print(f"Ошибка при загрузке данных: {error}")
        subscribers = {}


def save_data() -> None:
    """Сохранить текущих подписчиков в JSON-файл атомарно."""
    temp_file = SUBSCRIBERS_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(subscribers, file, ensure_ascii=False, indent=4)
        os.replace(temp_file, SUBSCRIBERS_FILE)
        print(f"Данные успешно сохранены в {SUBSCRIBERS_FILE}.")
    except OSError as error:
        print(f"Не удалось сохранить данные: {error}")
        # Попытка удалить временный файл, если он существует
        try:
            os.remove(temp_file)
        except OSError:
            pass


def format_date(date_obj: datetime.date) -> str:
    """Преобразовать объект даты в строку формата YYYY-MM-DD."""
    return date_obj.isoformat()


def compute_expire_date(days: int) -> str:
    """Вычислить дату окончания, добавив дни к текущей дате."""
    today = datetime.date.today()
    expire_date = today + datetime.timedelta(days=days)
    return format_date(expire_date)


def parse_subscription_line(line: str) -> tuple[str, int]:
    """Разобрать строку ввода пользователя на username и количество дней."""
    parts = line.split()
    if len(parts) != 2:
        raise ValueError("Неправильный формат. Ожидается: @username 7")

    username = parts[0].strip()
    days_str = parts[1].strip()

    if not username or not username.startswith("@"):  # username должен начинаться с @
        raise ValueError("Неправильный username. Должен начинаться с @ и не быть пустым.")

    if username == "@":
        raise ValueError("Неправильный username. Укажите имя после @.")

    try:
        days = int(days_str)
    except ValueError:
        raise ValueError("Неправильное количество дней. Укажите целое положительное число.")

    if days <= 0:
        raise ValueError("Количество дней должно быть положительным.")

    return username, days


def add_or_update_subscriber(username: str, days: int) -> None:
    """Добавить нового подписчика или обновить дату окончания существующей подписки."""
    expire_date = compute_expire_date(days)
    existing = subscribers.get(username)
    subscribers[username] = {
        "expire_date": expire_date,
        "notified": False,
    }

    if existing:
        print(f"Обновлен подписчик {username}: новая дата окончания {expire_date}.")
    else:
        print(f"Добавлен подписчик {username} с датой окончания {expire_date}.")

    save_data()


async def send_telegram_message(message: str) -> bool:
    """Отправить сообщение в Telegram через Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                data = await response.json()
                if not data.get("ok"):
                    print(f"Ошибка Telegram: {data}")
                    return False
    except Exception as error:
        print(f"Ошибка отправки Telegram: {error}")
        return False

    print("Telegram уведомление отправлено.")
    return True


async def notify_one_day_left() -> None:
    """Найти подписчиков с 1 днём до окончания и отправить одно уведомление."""
    global last_notification_sent
    today = datetime.date.today()
    users_to_notify = []

    for username, data in subscribers.items():
        if data.get("notified"):
            continue

        expire_date = datetime.date.fromisoformat(data["expire_date"])
        remaining = (expire_date - today).days
        if remaining == 1:
            users_to_notify.append(username)

    if not users_to_notify:
        return

    message = "У следующих пользователей остался 1 день подписки:\n\n" + "\n".join(users_to_notify)
    if await send_telegram_message(message):
        for username in users_to_notify:
            if username in subscribers:
                subscribers[username]["notified"] = True
        save_data()
        print(f"Уведомление Telegram отправлено для: {', '.join(users_to_notify)}")
        global last_notification_sent
        last_notification_sent = True


async def remove_expired_subscribers() -> None:
    """Удалить подписчиков с истекшими подписками."""
    today = datetime.date.today()
    to_remove = []

    for username, data in subscribers.items():
        expire_date = datetime.date.fromisoformat(data["expire_date"])
        remaining = (expire_date - today).days
        if remaining <= 0:
            to_remove.append(username)

    if not to_remove:
        return

    # Лог в консоль
    for username in to_remove:
        print(f"Удален подписчик {username}, срок подписки истек.")

    # Удаление из памяти
    for username in to_remove:
        subscribers.pop(username, None)

    # Сохранение
    save_data()


async def monitor_loop() -> None:
    """Фоновый цикл мониторинга, который периодически проверяет подписки."""
    while True:
        await notify_one_day_left()
        await remove_expired_subscribers()
        await asyncio.sleep(3600)


async def console_loop() -> None:
    """Основной цикл для взаимодействия через консоль и ввода подписок."""
    global last_notification_sent, just_switched_to_monitor
    mode = "input"
    print("Введите юзернейм и длительность подписки")
    print("Для сохранения данных введите \"end\"")

    while True:
        if mode == "monitor":
            if last_notification_sent:
                print("Данные успешно сохранены. Введите start для добавления новых подписчиков.")
                last_notification_sent = False
            if not just_switched_to_monitor:
                print("start - добавление/обновление подписчиков, end - сохранение данных")
            just_switched_to_monitor = False

        try:
            line = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print("\nЗавершение работы по сигналу пользователя.")
            break

        if not line:
            continue

        line = line.strip()
        if not line:
            continue

        if mode == "input":
            if line.lower() == "end":
                save_data()
                print("Введите start для добавления новых подписчиков.")
                mode = "monitor"
                just_switched_to_monitor = True
                continue

            try:
                username, days = parse_subscription_line(line)
                add_or_update_subscriber(username, days)
            except ValueError as error:
                print(error)
                continue

        else:
            if line.lower() == "start":
                mode = "input"
                print("Возврат в режим добавления подписчиков.")
                print("Введите юзернейм и длительность подписки")
                print("Для сохранения данных введите \"end\"")
                continue

            if line.lower() == "end":
                save_data()
                print("Данные успешно сохранены.")
                continue

            print('Введите "start" для добавления новых подписчиков или "end" для сохранения данных.')



async def main() -> None:
    load_data()
    monitor_task = asyncio.create_task(monitor_loop())

    try:
        await console_loop()
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
