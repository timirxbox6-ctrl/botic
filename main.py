import logging
import aiohttp
import json
import os
import re
import base64
from aiogram import Bot, Dispatcher, executor, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))
except ValueError:
    logging.error("ADMIN_ID или ALLOWED_CHAT_ID должны быть числами!")
    exit(1)

AI_MODEL = "sonar"
DB_FILE = "/data/users_db.json"
NICKNAMES_FILE = "/data/nicks.json"

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

async def ask_perplexity(question: str, image_base64: str = None, is_school_task: bool = False) -> str:
    try:
        headers = {
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json"
        }
        
        base_system_prompt = (
            "Твое имя Улитка. "
            "Стиль: прямой, без слов-паразитов. "
            "Отвечай кратко, максимум 524 символа. "
            "На простые вопросы отвечай коротко без подробностей. "
            "Можешь с юмором если уместно. "
            "Отвечай ТОЛЬКО чистым текстом без markdown/html. "
            "Ссылки пиши прямо без скобок (https://...). "
            "Не используй нумерованные списки 1, 2, 3. "
            "Не используй звездочки для выделения. "
            "Не начинай предложения с тире или дефиса. "
            "Пиши обычным текстом как в Telegram переписке."
        )
        
        if is_school_task:
            system_prompt = base_system_prompt + (
                " Если попросят решить задачу по математике, физике, химии, биологии - "
                "найди в интернете аналогичную с решением, проверь что сайт работает в РФ "
                "и дай прямую ссылку."
            )
        else:
            system_prompt = base_system_prompt
        
        messages = [{"role": "system", "content": system_prompt}]
        
        if image_base64:
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                {"type": "text", "text": question if question else "Что на фото? Опиши кратко."}
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": question})
        
        payload = {
            "model": AI_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "top_p": 0.9,
            "max_tokens": 4000,
            "search_recency_filter": "month",
            "return_images": False,
            "return_related_questions": False,
            "stream": False,
            "presence_penalty": 0,
            "frequency_penalty": 1
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.perplexity.ai/chat/completions", 
                                   headers=headers, 
                                   json=payload, 
                                   timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    answer = result['choices'][0]['message']['content']
                    if not answer:
                        return "Не смог сформулировать ответ. Попробуй переформулировать."
                    
                    answer = re.sub(r'\*\*.*?\*\*', '', answer)
                    answer = re.sub(r'\*.*?\*', '', answer)
                    
                    if len(answer) > 524:
                        answer = answer[:521] + "..."
                    
                    return answer.strip()
                elif resp.status == 429:
                    return "Слишком много запросов. Попробуй через минуту."
                else:
                    return f"API ошибка {resp.status}. Попробуй позже."
    except Exception as e:
        logging.error(f"Perplexity query error: {e}", exc_info=True)
        return "Ошибка при обработке запроса."

async def set_bot_commands():
    commands = [
        types.BotCommand(command="ask", description="Задать вопрос боту"),
        types.BotCommand(command="tip", description="Установить никнейм пользователю"),
        types.BotCommand(command="all", description="Упомянуть всех участников")
    ]
    await bot.set_my_commands(commands)

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
    
    text = message.text or message.caption or ""
    
    if text.startswith('/tip'):
        args = re.findall(r'"([^"]*)"', text)
        if len(args) == 2:
            nickname = args[0]
            target_username = args[1].replace('@', '').strip()
            if target_username:
                nicknames[target_username.lower()] = nickname
                save_nicks()
                await message.reply(f"Запомнил, @{target_username} теперь {nickname}.")
        return
    
    if text.startswith('/all') or text.startswith('/tagall'):
        if not known_users:
            await message.reply("Пусто.")
            return
        mentions = []
        for uid, uname, fname in known_users:
            if uname:
                mentions.append(f"@{uname}")
            else:
                mentions.append(f"{fname}")
        chunk_size = 30
        chunks = [mentions[i:i+chunk_size] for i in range(0, len(mentions), chunk_size)]
        await message.answer("Общий сбор:")
        for chunk in chunks:
            await message.answer(" ".join(chunk), parse_mode="HTML")
        return
    
    if text.startswith('/ask '):
        question = text[5:].strip()
        await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        
        has_photo = message.photo or (message.reply_to_message and message.reply_to_message.photo)
        image_base64 = None
        
        if has_photo:
            try:
                if message.photo:
                    photo = message.photo[-1]
                else:
                    photo = message.reply_to_message.photo[-1]
                
                file = await bot.get_file(photo.file_id)
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            image_base64 = base64.b64encode(image_data).decode('utf-8')
            except Exception as e:
                logging.error(f"Image download error: {e}")
                await message.reply("Не могу загрузить фото.")
                return
        
        school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
        is_school = any(keyword in question.lower() for keyword in school_keywords) if question else False
        
        answer = await ask_perplexity(question=question, image_base64=image_base64, is_school_task=is_school)
        
        if answer:
            await message.reply(answer, parse_mode=None)
        return

@dp.message_handler(chat_type=types.ChatType.PRIVATE)
async def private_handler(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        text = message.text or message.caption or ""
        if text.startswith('/ask '):
            question = text[5:].strip()
            await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
            
            has_photo = message.photo
            image_base64 = None
            
            if has_photo:
                try:
                    photo = message.photo[-1]
                    file = await bot.get_file(photo.file_id)
                    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status == 200:
                                image_data = await resp.read()
                                image_base64 = base64.b64encode(image_data).decode('utf-8')
                except Exception as e:
                    logging.error(f"Image download error: {e}")
                    await message.reply("Не могу загрузить фото.")
                    return
            
            school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
            is_school = any(keyword in question.lower() for keyword in school_keywords) if question else False
            
            answer = await ask_perplexity(question=question, image_base64=image_base64, is_school_task=is_school)
            
            if answer:
                await message.reply(answer, parse_mode=None)

async def on_startup(dp):
    await set_bot_commands()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
