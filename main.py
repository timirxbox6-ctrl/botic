import os
import json
import re
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
import aiohttp
from pathlib import Path

BOT_TOKEN = os.getenv("BOT_TOKEN")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID"))

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
USERS_DB = DATA_DIR / "users_db.json"
NICKS_DB = DATA_DIR / "nicks.json"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def load_json(path):
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_user(user_id, username, first_name):
    users = load_json(USERS_DB)
    users[str(user_id)] = {
        "id": user_id,
        "username": username,
        "first_name": first_name
    }
    save_json(USERS_DB, users)

def get_nickname(username):
    nicks = load_json(NICKS_DB)
    return nicks.get(username, f"@{username}")

def set_nickname(username, nickname):
    nicks = load_json(NICKS_DB)
    nicks[username] = nickname
    save_json(NICKS_DB, nicks)

async def download_photo(photo):
    file = await bot.get_file(photo.file_id)
    file_path = file.file_path
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return await resp.read()

async def ask_perplexity(question, image_data=None):
    url = "https://api.perplexity.ai/chat/completions"
    
    system_prompt = """Ты Улитка. Отвечай коротко и по делу, максимум 300 символов. Пиши как человек обычным языком. НИКОГДА не используй LaTeX, формулы, скобки с символами, знаки доллара. Используй только обычные Unicode символы: ², ³, √, ×, ÷, ≈, ≤, ≥. Не используй списки, цифры в начале строк, звездочки, тире для перечисления. Пиши всё одним текстом. Если в вопросе мат - отвечай в таком же тоне коротко. Для школьных задач давай прямые ссылки на решения без скобок."""
    
    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    if image_data:
        import base64
        b64_image = base64.b64encode(image_data).decode('utf-8')
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
            ]
        })
    else:
        messages.append({"role": "user", "content": question})
    
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "sonar-pro",
        "messages": messages,
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 800,
        "search_recency_filter": "month",
        "return_images": False,
        "return_related_questions": False,
        "stream": False
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            result = await resp.json()
            return result["choices"][0]["message"]["content"]

def clean_response(text):
    text = re.sub(r'\\\[.*?\\\]', '', text)
    text = re.sub(r'\\\(.*?\\\)', '', text)
    text = re.sub(r'\$\$.*?\$\$', '', text)
    text = re.sub(r'\$.*?\$', '', text)
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'^[\d\-\*•]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    if len(text) > 300:
        text = text[:297] + "..."
    
    return text

def is_school_task(text):
    keywords = ["реши", "решить", "задача", "пример", "уравнение", "формула", "теорема"]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)

@dp.message(F.new_chat_members)
async def new_member(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    
    for user in message.new_chat_members:
        save_user(user.id, user.username, user.first_name)

@dp.message(Command("tip"))
async def set_tip(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID and message.from_user.id != ADMIN_ID:
        return
    
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    text = message.text
    match = re.search(r'/tip\s+"([^"]+)"\s+"@([^"]+)"', text)
    
    if not match:
        await message.reply("Формат: /tip \"никнейм\" \"@username\"")
        return
    
    nickname = match.group(1)
    username = match.group(2)
    
    set_nickname(username, nickname)
    await message.reply(f"Никнейм установлен: {nickname} для @{username}")

@dp.message(Command("all", "tagall"))
async def tag_all(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID and message.from_user.id != ADMIN_ID:
        return
    
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    users = load_json(USERS_DB)
    mentions = []
    
    for user_data in users.values():
        username = user_data.get("username")
        if username:
            mentions.append(get_nickname(username))
    
    chunks = [mentions[i:i+30] for i in range(0, len(mentions), 30)]
    
    for chunk in chunks:
        await message.reply(" ".join(chunk))

@dp.message(Command("ask"))
async def ask_command(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID and message.from_user.id != ADMIN_ID:
        return
    
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    question = message.text.replace("/ask", "").strip()
    
    if not question:
        await message.reply("Задай вопрос после команды")
        return
    
    image_data = None
    
    if message.photo:
        photo = message.photo[-1]
        image_data = await download_photo(photo)
        
        if len(question) < 10:
            question = "Реши задачу на фото"
    
    if message.reply_to_message and message.reply_to_message.photo and not image_data:
        photo = message.reply_to_message.photo[-1]
        image_data = await download_photo(photo)
        
        if len(question) < 10:
            question = "Реши задачу на фото"
    
    if is_school_task(question):
        question = f"{question}. Дай ссылку на решение."
    
    try:
        answer = await ask_perplexity(question, image_data)
        clean_answer = clean_response(answer)
        await message.reply(clean_answer)
    except Exception as e:
        await message.reply("Ошибка при обработке запроса")

@dp.message(F.text.startswith("улитка"))
async def snail_ask(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID and message.from_user.id != ADMIN_ID:
        return
    
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    question = message.text.replace("улитка", "").strip()
    
    if not question:
        await message.reply("Задай вопрос")
        return
    
    image_data = None
    
    if message.photo:
        photo = message.photo[-1]
        image_data = await download_photo(photo)
        
        if len(question) < 10:
            question = "Реши задачу на фото"
    
    if message.reply_to_message and message.reply_to_message.photo and not image_data:
        photo = message.reply_to_message.photo[-1]
        image_data = await download_photo(photo)
        
        if len(question) < 10:
            question = "Реши задачу на фото"
    
    if is_school_task(question):
        question = f"{question}. Дай ссылку на решение."
    
    try:
        answer = await ask_perplexity(question, image_data)
        clean_answer = clean_response(answer)
        await message.reply(clean_answer)
    except Exception as e:
        await message.reply("Ошибка при обработке запроса")

@dp.message()
async def save_users(message: Message):
    if message.chat.id == ALLOWED_CHAT_ID:
        save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

async def main():
    await bot.delete_my_commands()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
