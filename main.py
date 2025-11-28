import logging
import aiohttp
import json
import os
import re
import asyncio
from aiogram import Bot, Dispatcher, executor, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))
except ValueError:
    logging.error("ADMIN_ID или ALLOWED_CHAT_ID должны быть числами!")
    exit(1)

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

load_data()

async def ask_perplexity(question: str, is_school_task: bool = False) -> str:
    try:
        headers = {
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json"
        }
        
        base_system_prompt = (
            "Твое имя Улитка. "
            "Стиль общения: дружелюбный, простой, без лишних подробностей. "
            "Отвечай коротко и по делу, максимум 2048 символов для обычных вопросов. "
            "Без эмодзи, без звездочек, без нумерованных списков. "
            "СТРОГО ЗАПРЕЩЕНО использовать LaTeX, математические символы типа \\(x\\), \\[формула\\], $x$, $$формула$$. "
            "Формулы пиши обычными символами Unicode: используй ², ³ для степеней, √ для корня. "
            "Например: c² = a² + b², D = b² − 4ac, x = (−b ± √D) / 2a "
            "На простые вопросы типа привет отвечай кратко одним предложением. "
        )
        
        if is_school_task:
            system_prompt = base_system_prompt + (
                "Если попросят решить задачу по математике физике химии биологии "
                "найди в интернете аналогичную с решением проверь что сайт работает в РФ "
                "и дай прямую ссылку просто URL без скобок."
            )
        else:
            system_prompt = base_system_prompt
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ]
        
        logging.info(f"Sending request to Perplexity")
        logging.info(f"Question: {question[:100]}...")
        
        payload = {
            "model": "sonar",
            "messages": messages,
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 4000,
            "search_recency_filter": "month",
            "return_images": False,
            "return_related_questions": False,
            "stream": False,
            "presence_penalty": 0,
            "frequency_penalty": 1
        }
        
        for attempt in range(3):
            try:
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
                            
                            answer = re.sub(r'\[(\d+)\]', '', answer)
                            answer = re.sub(r'\\\[.*?\\\]', '', answer, flags=re.DOTALL)
                            answer = re.sub(r'\\\(.*?\\\)', '', answer, flags=re.DOTALL)
                            answer = re.sub(r'\$\$.*?\$\$', '', answer, flags=re.DOTALL)
                            answer = re.sub(r'\$[^\$]+\$', '', answer)
                            answer = re.sub(r'\*\*', '', answer)
                            answer = re.sub(r'^\s*[-•]\s*', '', answer, flags=re.MULTILINE)
                            answer = re.sub(r'^\s*\d+\.\s*', '', answer, flags=re.MULTILINE)
                            
                            if len(answer) > 3500:
                                answer = answer[:3497] + "..."
                            
                            return answer.strip()
                        elif resp.status == 429:
                            if attempt < 2:
                                await asyncio.sleep(3)
                                continue
                            return "Слишком много запросов. Попробуй через минуту."
                        else:
                            error_text = await resp.text()
                            logging.error(f"API error {resp.status}: {error_text}")
                            return f"API ошибка {resp.status}. Попробуй позже."
            except asyncio.TimeoutError:
                logging.warning(f"Timeout attempt {attempt + 1}/3")
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                return "Запрос занял слишком много времени. Попробуй упростить вопрос."
            except Exception as e:
                logging.error(f"Query error on attempt {attempt + 1}: {e}", exc_info=True)
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                return "Ошибка при обработке запроса."
        
        return "Не удалось получить ответ после 3 попыток."
        
    except Exception as e:
        logging.error(f"Perplexity query error: {e}", exc_info=True)
        return "Ошибка при обработке запроса."

async def on_startup(dp):
    await bot.delete_my_commands()

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
    
    text = (message.text or message.caption or "").strip()
    text_lower = text.lower()
    
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
    
    if text.startswith('/ask') or text_lower.startswith('улитка'):
        question = text
        if text.startswith('/ask'):
            question = text[4:].strip()
        elif text_lower.startswith('улитка'):
            question = text[6:].strip()
        
        if not question:
            return
        
        await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        
        school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
        is_school = any(keyword in question.lower() for keyword in school_keywords)
        
        answer = await ask_perplexity(question=question, is_school_task=is_school)
        
        if answer:
            await message.reply(answer, parse_mode=None)
        return

@dp.message_handler(chat_type=types.ChatType.PRIVATE)
async def private_handler(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        text = (message.text or message.caption or "").strip()
        text_lower = text.lower()
        
        if text.startswith('/ask') or text_lower.startswith('улитка'):
            question = text
            if text.startswith('/ask'):
                question = text[4:].strip()
            elif text_lower.startswith('улитка'):
                question = text[6:].strip()
            
            if not question:
                return
            
            await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
            
            school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
            is_school = any(keyword in question.lower() for keyword in school_keywords)
            
            answer = await ask_perplexity(question=question, is_school_task=is_school)
            
            if answer:
                await message.reply(answer, parse_mode=None)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
