import logging
import aiohttp
import json
import os
import re
from aiogram import Bot, Dispatcher, executor, types

# ================= НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ =================
# Если переменной нет, скрипт упадет или будет использовать дефолт (если указан)
BOT_TOKEN = os.getenv("BOT_TOKEN")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

# ID можно передавать строкой, но в коде они нужны как int
# Используем int() для преобразования
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))
except ValueError:
    logging.error("ADMIN_ID или ALLOWED_CHAT_ID должны быть числами!")
    exit(1)

AI_MODEL = "sonar"

# Пути к файлам. Если подключишь Volume на Railway в папку /data,
# измени эти пути на "/data/users_db.json" и т.д.
DB_FILE = "users_db.json"
NICKNAMES_FILE = "nicks.json"
# =====================================================================

# Проверка, что ключи загрузились
if not BOT_TOKEN or not PERPLEXITY_API_KEY:
    logging.error("ОШИБКА: Не найдены BOT_TOKEN или PERPLEXITY_API_KEY в переменных окружения!")
    exit(1)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

known_users = set()
nicknames = {}

def load_data():
    global known_users, nicknames
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                known_users = set(tuple(x) for x in data)
        except:
            known_users = set()
    if os.path.exists(NICKNAMES_FILE):
        try:
            with open(NICKNAMES_FILE, "r", encoding="utf-8") as f:
                nicknames = json.load(f)
        except:
            nicknames = {}

def save_users():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(list(known_users), f, ensure_ascii=False)
    except: pass

def save_nicks():
    try:
        with open(NICKNAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(nicknames, f, ensure_ascii=False)
    except: pass

def get_display_name(user_obj=None, username=None, uid=None, first_name=None):
    uname = username
    if user_obj:
        uname = user_obj.username
    if uname:
        uname_lower = uname.lower()
        if uname_lower in nicknames:
            return nicknames[uname_lower]
        return f"@{uname}"
    if first_name:
        return first_name
    return "Человек"

load_data()

async def ask_perplexity(text, context=""):
    url = "https://api.perplexity.ai/chat/completions"
    system_prompt = (
        f"Твое имя Улитка. {context}\n"
        "Твой стиль: много слов-паразитов (типа, лол, короче, походу, ну). "
        "1. Отвечай ТОЛЬКО чистым текстом. НЕ используй markdown или html. "
        "2. Ссылки пиши как есть (https://...). "
        "3. Если грубят - отвечай агрессивно матом. "
        "4. Если извиняются - прощай."
    )
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "temperature": 0.7
    }
    headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    return res['choices'][0]['message']['content']
                return None
    except: return None

@dp.message_handler(content_types=types.ContentTypes.NEW_CHAT_MEMBERS, chat_id=ALLOWED_CHAT_ID)
async def on_join(message: types.Message):
    for u in message.new_chat_members:
        if not u.is_bot:
            udata = (u.id, u.username, u.first_name)
            known_users.discard(next((x for x in known_users if x[0] == u.id), None))
            known_users.add(udata)
            save_users()

@dp.message_handler(content_types=types.ContentTypes.ANY, chat_id=ALLOWED_CHAT_ID)
async def main_handler(message: types.Message):
    if not message.from_user.is_bot:
        u = message.from_user
        udata = (u.id, u.username, u.first_name)
        if udata not in known_users:
            known_users.discard(next((x for x in known_users if x[0] == u.id), None))
            known_users.add(udata)
            save_users()

    text = message.text or ""
    
    if text.startswith('/tip'):
        args = re.findall(r'"([^"]*)"', text)
        if len(args) == 2:
            nickname = args[0]
            target_username = args[1].replace('@', '').strip()
            if target_username:
                nicknames[target_username.lower()] = nickname
                save_nicks()
                await message.reply(f"Типа запомнил, @{target_username} теперь {nickname}, лол.")
        return

    if text.startswith('/all') or text.startswith('/tagall'):
        if not known_users:
            await message.reply("Пусто, лол.")
            return
        
        mentions = []
        for uid, uname, fname in known_users:
            if uname:
                mentions.append(f"@{uname}")
            else:
                mentions.append(f"<a href='tg://user?id={uid}'>{fname}</a>")

        chunk_size = 30
        chunks = [mentions[i:i+chunk_size] for i in range(0, len(mentions), chunk_size)]
        
        await message.answer("Короче, общий сбор типа...")
        for chunk in chunks:
            await message.answer(" ".join(chunk), parse_mode="HTML")
        return

    if text.startswith('/ask '):
        question = text[5:].strip()
        if question:
            ans = await ask_perplexity(question)
            if ans: 
                await message.reply(ans, parse_mode=None)
        return

    if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
        sender_name = get_display_name(user_obj=message.from_user)
        context = f"Ты говоришь с пользователем {sender_name}."
        ans = await ask_perplexity(text, context=context)
        if ans:
            final_text = f"{sender_name}, {ans}"
            await message.reply(final_text, parse_mode=None)

@dp.message_handler(chat_type=types.ChatType.PRIVATE)
async def private_handler(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        ans = await ask_perplexity(message.text or "")
        if ans: await message.reply(ans)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
