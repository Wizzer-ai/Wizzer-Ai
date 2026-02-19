import asyncio
import aiohttp
import os
import logging
import random
from datetime import datetime, timedelta
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== ТОКЕНЫ ПРЯМО В КОДЕ (ДЛЯ БЕСПЛАТНОГО ТАРИФА) =====
BOT_TOKEN = "8501279587:AAE8d0RrVOqkT16zFagktXwHtxj_v-3lcB8"
OPENROUTER_API_KEY = "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"
ADMIN_ID = 7308065271

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== ФАЙЛЫ ДЛЯ ХРАНЕНИЯ ДАННЫХ =====
CHANNELS_FILE = "channels.json"
USERS_FILE = "users.json"
REFS_FILE = "refs.json"

def load_json(filename, default):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return default

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

required_channels = load_json(CHANNELS_FILE, [])
users_db = load_json(USERS_FILE, {})
refs_db = load_json(REFS_FILE, {})

# ===== ХАРАКТЕР БОТА =====
BOT_PERSONALITY = """
Ты Wizzer. Ты скромный, умный, серьёзный помощник. Отвечаешь по делу, чётко, без воды.
"""

# Хранилище данных
user_data = {}
user_histories = {}
user_settings = {}
user_subscription_cache = {}

# Доступные модели
FREE_MODELS = {
    "stepfun/step-3.5-flash:free": "⚡ Step 3.5 Flash",
}

PRO_MODELS = {
    "qwen/qwen2.5-7b-instruct:free": "🎯 Qwen 2.5",
    "google/gemma-3-12b-it:free": "🧠 Gemma 3 12B",
    "deepseek/deepseek-r1:free": "🔄 DeepSeek R1"
}

ALL_MODELS = {**FREE_MODELS, **PRO_MODELS}

# Запасные ответы
FALLBACK_RESPONSES = [
    "Хм, сейчас что-то с подключением. Попробуй через минуту.",
    "Техническая пауза, давай позже.",
    "Не отвечает, попробуй другую модель."
]

# ===== РЕФЕРАЛЬНАЯ СИСТЕМА =====

def get_user(user_id):
    user_id = str(user_id)
    if user_id not in users_db:
        users_db[user_id] = {
            "refs": 0,
            "pro": False,
            "pro_until": None,
            "joined": datetime.now().isoformat()
        }
        save_json(USERS_FILE, users_db)
    return users_db[user_id]

def add_ref(referrer_id, new_user_id):
    referrer_id = str(referrer_id)
    new_user_id = str(new_user_id)
    
    if referrer_id not in refs_db:
        refs_db[referrer_id] = []
    
    if new_user_id not in refs_db[referrer_id]:
        refs_db[referrer_id].append(new_user_id)
        save_json(REFS_FILE, refs_db)
        
        user = get_user(referrer_id)
        user["refs"] = len(refs_db[referrer_id])
        
        if user["refs"] >= 5 and not user["pro"]:
            user["pro"] = True
            user["pro_until"] = (datetime.now() + timedelta(days=30)).isoformat()
            save_json(USERS_FILE, users_db)
            return True
    return False

def is_pro(user_id):
    # Админ всегда PRO
    if int(user_id) == ADMIN_ID:
        return True
    
    user = get_user(user_id)
    if not user["pro"]:
        return False
    if user["pro_until"]:
        if datetime.fromisoformat(user["pro_until"]) < datetime.now():
            user["pro"] = False
            user["pro_until"] = None
            save_json(USERS_FILE, users_db)
            return False
    return True

# ===== ПРОВЕРКА ПОДПИСКИ =====

async def check_subscription(user_id: int) -> bool:
    if not required_channels or user_id == ADMIN_ID:
        return True
    
    if user_id in user_subscription_cache:
        return user_subscription_cache[user_id]
    
    try:
        for channel in required_channels:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                user_subscription_cache[user_id] = False
                return False
        user_subscription_cache[user_id] = True
        return True
    except:
        return False

def get_subscription_keyboard():
    builder = InlineKeyboardBuilder()
    for channel in required_channels:
        builder.row(InlineKeyboardButton(text=f"📢 {channel}", url=f"https://t.me/{channel.replace('@', '')}"))
    builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub"))
    return builder.as_markup()

# ===== АДМИН-ПАНЕЛЬ =====

def get_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Добавить канал", callback_data="admin_add_channel"))
    builder.row(InlineKeyboardButton(text="➖ Удалить канал", callback_data="admin_remove_channel"))
    builder.row(InlineKeyboardButton(text="📋 Список каналов", callback_data="admin_list_channels"))
    builder.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="👑 Дать PRO", callback_data="admin_give_pro"))
    builder.row(InlineKeyboardButton(text="◀️ Выход", callback_data="admin_exit"))
    return builder.as_markup()

# ===== КЛАВИАТУРЫ ПОЛЬЗОВАТЕЛЯ =====

def get_main_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💬 Спросить", callback_data="ask"),
        InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        InlineKeyboardButton(text="🤝 Рефералы", callback_data="ref"),
        width=2
    )
    builder.row(
        InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        InlineKeyboardButton(text="📢 Каналы", callback_data="channels"),
        width=2
    )
    return builder.as_markup()

def get_back_keyboard(callback: str = "menu"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=callback))
    return builder.as_markup()

def get_settings_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    if is_pro(user_id):
        builder.row(InlineKeyboardButton(text="🤖 Сменить модель", callback_data="change_model"))
    else:
        builder.row(InlineKeyboardButton(text="🔒 PRO модели", callback_data="pro_info"))
    builder.row(
        InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications"),
        InlineKeyboardButton(text="🌐 Язык", callback_data="language"),
        width=2
    )
    builder.row(InlineKeyboardButton(text="🧹 Очистить историю", callback_data="clear_history"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu"))
    return builder.as_markup()

def get_models_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    for model_id, model_name in FREE_MODELS.items():
        builder.row(InlineKeyboardButton(text=model_name, callback_data=f"setmodel_{model_id}"))
    if is_pro(user_id):
        for model_id, model_name in PRO_MODELS.items():
            builder.row(InlineKeyboardButton(text=f"⭐ {model_name}", callback_data=f"setmodel_{model_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="settings"))
    return builder.as_markup()

# ===== ОБРАБОТЧИКИ КОМАНД =====

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject = None):
    user_id = message.from_user.id
    get_user(user_id)
    
    # Обработка реферальной ссылки
    if command and command.args and user_id != ADMIN_ID:
        try:
            ref_id = int(command.args)
            if ref_id != user_id and str(user_id) not in refs_db.get(str(ref_id), []):
                if add_ref(ref_id, user_id):
                    try:
                        await bot.send_message(
                            ref_id,
                            "🎉 Поздравляю!\n\nТы пригласил 5 друзей и получил PRO доступ на 30 дней!"
                        )
                    except:
                        pass
        except:
            pass
    
    # Проверка подписки
    if required_channels:
        subscribed = await check_subscription(user_id)
        if not subscribed:
            await message.answer(
                "📢 Для использования бота нужно подписаться на каналы:",
                reply_markup=get_subscription_keyboard()
            )
            return
    
    pro_status = "⭐ PRO" if is_pro(user_id) else "⚪ FREE"
    
    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я Wizzer — твой помощник.\n"
        f"Твой статус: {pro_status}\n\n"
        f"👇 Выбирай кнопку"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user_id))

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У тебя нет прав администратора.")
        return
    
    stats_text = (
        f"👑 Админ-панель Wizzer\n\n"
        f"📊 Статистика:\n"
        f"• Пользователей: {len(users_db)}\n"
        f"• PRO пользователей: {sum(1 for u in users_db.values() if u.get('pro'))}\n"
        f"• Каналов в подписке: {len(required_channels)}\n\n"
        f"🔧 Управление:"
    )
    await message.answer(stats_text, reply_markup=get_admin_keyboard())

# ===== ОБРАБОТЧИКИ ПОДПИСКИ =====

@dp.callback_query(lambda c: c.data == "check_sub")
async def check_subscription_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    subscribed = await check_subscription(user_id)
    
    if subscribed:
        user_subscription_cache[user_id] = True
        await callback.message.delete()
        await cmd_start(callback.message)
    else:
        await callback.answer("❌ Ты подписался не на все каналы!", show_alert=True)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "channels")
async def show_channels(callback: CallbackQuery):
    if not required_channels:
        await callback.message.edit_text(
            "📢 Нет обязательных каналов для подписки.",
            reply_markup=get_back_keyboard()
        )
        await callback.answer()
        return
    
    channels_text = "📢 Обязательные каналы:\n\n"
    for ch in required_channels:
        channels_text += f"• {ch}\n"
    
    await callback.message.edit_text(
        channels_text,
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

# ===== ОБРАБОТЧИКИ РЕФЕРАЛОВ =====

@dp.callback_query(lambda c: c.data == "ref")
async def ref_system(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    ref_link = f"https://t.me/{(await bot.me()).username}?start={user_id}"
    ref_count = user["refs"]
    needed = max(0, 5 - ref_count)
    
    pro_status = "✅ PRO активен" if is_pro(user_id) else "❌ PRO не активен"
    if user.get("pro_until") and user_id != ADMIN_ID:
        pro_until = datetime.fromisoformat(user["pro_until"]).strftime("%d.%m.%Y")
        pro_status += f"\n⏳ Действует до: {pro_until}"
    
    ref_text = (
        f"🤝 Реферальная система\n\n"
        f"📊 Твоя статистика:\n"
        f"• Приглашено: {ref_count} / 5\n"
        f"• Осталось: {needed}\n\n"
        f"{pro_status}\n\n"
        f"🔗 Твоя ссылка:\n"
        f"`{ref_link}`\n\n"
        f"📌 За 5 приглашений → PRO на месяц"
    )
    
    await callback.message.edit_text(ref_text, reply_markup=get_back_keyboard())
    await callback.answer()

# ===== ОБРАБОТЧИКИ МЕНЮ =====

@dp.callback_query(lambda c: c.data == "menu")
async def back_to_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if required_channels:
        subscribed = await check_subscription(user_id)
        if not subscribed:
            await callback.message.edit_text(
                "📢 Нужно подписаться на каналы",
                reply_markup=get_subscription_keyboard()
            )
            return
    
    await callback.message.edit_text(
        "👋 Главное меню",
        reply_markup=get_main_keyboard(user_id)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "ask")
async def ask_question(callback: CallbackQuery):
    await callback.message.edit_text(
        "💬 Задай вопрос\n\nПросто напиши мне что-нибудь.",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "profile")
async def show_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    history_len = len(user_histories.get(user_id, []))
    pro_status = "⭐ PRO" if is_pro(user_id) else "⚪ FREE"
    
    profile_text = (
        f"👤 Твой профиль\n\n"
        f"📅 Присоединился: {user.get('joined', 'неизвестно')[:10]}\n"
        f"💬 Сообщений: {history_len}\n"
        f"⭐ Статус: {pro_status}\n"
        f"🤝 Рефералов: {user['refs']}\n"
        f"🆔 ID: `{user_id}`"
    )
    
    await callback.message.edit_text(profile_text, reply_markup=get_back_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "settings")
async def show_settings(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.message.edit_text(
        "⚙️ Настройки",
        reply_markup=get_settings_keyboard(user_id)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "change_model")
async def change_model(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.message.edit_text(
        "🤖 Выбери модель:",
        reply_markup=get_models_keyboard(user_id)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "pro_info")
async def pro_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔒 PRO модели\n\n"
        "Чтобы получить доступ:\n"
        "• Пригласи 5 друзей\n"
        "• Или обратись к администратору",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("setmodel_"))
async def set_model(callback: CallbackQuery):
    user_id = callback.from_user.id
    model_id = callback.data.replace("setmodel_", "")
    
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]["model"] = model_id
    
    model_name = ALL_MODELS.get(model_id, "модель")
    
    await callback.message.edit_text(
        f"✅ Модель изменена на {model_name}",
        reply_markup=get_settings_keyboard(user_id)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "clear_history")
async def clear_history_cmd(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_histories:
        user_histories[user_id] = []
    
    await callback.message.edit_text(
        "🧹 История диалога очищена",
        reply_markup=get_settings_keyboard(user_id)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help")
async def show_help(callback: CallbackQuery):
    help_text = (
        "❓ Помощь\n\n"
        "/start — главное меню\n"
        "/admin — админ-панель\n\n"
        "💬 В чатах: тегни @WizzerBot и задай вопрос"
    )
    await callback.message.edit_text(help_text, reply_markup=get_back_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "notifications")
async def toggle_notifications(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_settings:
        user_settings[user_id] = {}
    
    current = user_settings[user_id].get("notifications", True)
    user_settings[user_id]["notifications"] = not current
    
    status = "включены" if not current else "отключены"
    await callback.message.edit_text(
        f"🔔 Уведомления {status}",
        reply_markup=get_settings_keyboard(user_id)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "language")
async def language(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.message.edit_text(
        "🌐 Русский язык",
        reply_markup=get_settings_keyboard(user_id)
    )
    await callback.answer()

# ===== АДМИН ОБРАБОТЧИКИ =====

@dp.callback_query(lambda c: c.data == "admin_add_channel")
async def admin_add_channel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    await callback.message.edit_text(
        "➕ Добавление канала\n\n"
        "Отправь username канала в формате @channel",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_back")
        ).as_markup()
    )
    await callback.answer()

@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text and msg.text.startswith('@'))
async def handle_add_channel(message: Message):
    channel = message.text.strip()
    
    if channel not in required_channels:
        required_channels.append(channel)
        save_json(CHANNELS_FILE, required_channels)
        await message.answer(f"✅ Канал {channel} добавлен")
    else:
        await message.answer(f"⚠️ Канал {channel} уже есть")

@dp.callback_query(lambda c: c.data == "admin_remove_channel")
async def admin_remove_channel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    if not required_channels:
        await callback.message.edit_text(
            "📭 Список каналов пуст",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")
            ).as_markup()
        )
        await callback.answer()
        return
    
    builder = InlineKeyboardBuilder()
    for channel in required_channels:
        builder.row(InlineKeyboardButton(text=f"❌ {channel}", callback_data=f"delchannel_{channel}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    
    await callback.message.edit_text(
        "🗑 Удаление канала\n\nВыбери канал:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("delchannel_"))
async def delete_channel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    channel = callback.data.replace("delchannel_", "")
    if channel in required_channels:
        required_channels.remove(channel)
        save_json(CHANNELS_FILE, required_channels)
        await callback.message.edit_text(f"✅ Канал {channel} удалён")
        await asyncio.sleep(1)
        await admin_remove_channel(callback)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_list_channels")
async def admin_list_channels(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    if not required_channels:
        text = "📭 Список каналов пуст"
    else:
        channels_list = "\n".join([f"• {ch}" for ch in required_channels])
        text = f"📋 Список каналов:\n\n{channels_list}"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")
        ).as_markup()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    total_users = len(users_db)
    pro_users = sum(1 for u in users_db.values() if u.get('pro'))
    
    stats_text = (
        f"📊 Статистика\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"⭐ PRO: {pro_users}\n"
        f"📢 Каналов: {len(required_channels)}"
    )
    
    await callback.message.edit_text(
        stats_text,
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")
        ).as_markup()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    await callback.message.edit_text(
        "📢 Рассылка\n\n"
        "Отправь сообщение для рассылки",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_back")
        ).as_markup()
    )
    await callback.answer()

@dp.message(lambda msg: msg.from_user.id == ADMIN_ID)
async def handle_broadcast(message: Message):
    sent = 0
    failed = 0
    
    status_msg = await message.answer("📤 Начинаю рассылку...")
    
    for user_id in users_db.keys():
        try:
            await bot.copy_message(
                chat_id=int(user_id),
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await status_msg.edit_text(
        f"✅ Рассылка завершена\n📨 {sent} | ❌ {failed}"
    )

@dp.callback_query(lambda c: c.data == "admin_give_pro")
async def admin_give_pro(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    await callback.message.edit_text(
        "👑 Выдача PRO\n\n"
        "Отправь ID пользователя",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_back")
        ).as_markup()
    )
    await callback.answer()

@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text and msg.text.isdigit())
async def handle_give_pro(message: Message):
    user_id = message.text.strip()
    
    user = get_user(user_id)
    user["pro"] = True
    user["pro_until"] = (datetime.now() + timedelta(days=30)).isoformat()
    save_json(USERS_FILE, users_db)
    
    await message.answer(f"✅ PRO выдан пользователю {user_id}")
    
    try:
        await bot.send_message(
            int(user_id),
            "🎉 Вам выдан PRO доступ на 30 дней!"
        )
    except:
        pass

@dp.callback_query(lambda c: c.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет прав")
        return
    
    stats_text = (
        f"👑 Админ-панель Wizzer\n\n"
        f"📊 Статистика:\n"
        f"• Пользователей: {len(users_db)}\n"
        f"• PRO: {sum(1 for u in users_db.values() if u.get('pro'))}\n"
        f"• Каналов: {len(required_channels)}"
    )
    await callback.message.edit_text(stats_text, reply_markup=get_admin_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_exit")
async def admin_exit(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

# ===== ОСНОВНОЙ ОБРАБОТЧИК =====

@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    
    if message.text and message.text.startswith('/'):
        return
    
    # Проверка подписки
    if message.chat.type == "private" and required_channels and user_id != ADMIN_ID:
        subscribed = await check_subscription(user_id)
        if not subscribed:
            await message.answer(
                "📢 Нужно подписаться на каналы",
                reply_markup=get_subscription_keyboard()
            )
            return
    
    await bot.send_chat_action(message.chat.id, "typing")
    
    get_user(user_id)
    if user_id not in user_data:
        user_data[user_id] = {"model": "stepfun/step-3.5-flash:free"}
    if user_id not in user_histories:
        user_histories[user_id] = []
    
    if message.chat.type == "private":
        user_histories[user_id].append({"role": "user", "content": message.text})
        if len(user_histories[user_id]) > 5:
            user_histories[user_id] = user_histories[user_id][-5:]
    
    try:
        model = user_data[user_id].get("model", "stepfun/step-3.5-flash:free")
        
        if model in PRO_MODELS and not is_pro(user_id):
            model = "stepfun/step-3.5-flash:free"
            user_data[user_id]["model"] = model
        
        messages = [{"role": "system", "content": BOT_PERSONALITY}]
        
        if message.chat.type == "private":
            for msg in user_histories[user_id]:
                messages.append(msg)
        else:
            clean_text = message.text.replace(f"@{bot.me.username}", "").strip()
            messages.append({"role": "user", "content": clean_text})
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0.7
                },
                timeout=60
            ) as resp:
                
                if resp.status == 200:
                    data = await resp.json()
                    answer = data["choices"][0]["message"]["content"]
                    
                    if message.chat.type == "private":
                        user_histories[user_id].append({"role": "assistant", "content": answer})
                    
                    await message.reply(answer[:3000])
                else:
                    await message.reply(random.choice(FALLBACK_RESPONSES))
                    
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.reply(random.choice(FALLBACK_RESPONSES))

async def main():
    logger.info("🚀 Wizzer FULL запущен")
    logger.info(f"Админ ID: {ADMIN_ID} (PRO навсегда)")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())