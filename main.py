import logging
import aiohttp
import json
import os
import re
import base64
import asyncio
from aiogram import Bot, Dispatcher, executor, types

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

# Загрузка ID администратора и разрешенного чата
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))
except ValueError:
    logging.error("ADMIN_ID или ALLOWED_CHAT_ID должны быть числами!")
    exit(1)

AI_MODEL = "sonar-pro"
DB_FILE = "users_db.json"  # Можно поменять путь, например на /data/users_db.json
NICKNAMES_FILE = "nicks.json" # Можно поменять путь на /data/nicks.json

if not BOT_TOKEN or not PERPLEXITY_API_KEY:
    logging.error("ОШИБКА: Не найдены BOT_TOKEN или PERPLEXITY_API_KEY!")
    exit(1)

# --- ИНИЦИАЛИЗАЦИЯ ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

known_users = set()
nicknames = {}

# --- РАБОТА С ДАННЫМИ ---
def load_data():
    global known_users, nicknames
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                known_users = set(tuple(x) for x in data)
        except Exception as e:
            logging.error(f"Ошибка загрузки пользователей: {e}")
            known_users = set()
    
    if os.path.exists(NICKNAMES_FILE):
        try:
            with open(NICKNAMES_FILE, "r", encoding="utf-8") as f:
                nicknames = json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки ников: {e}")
            nicknames = {}

def save_users():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(list(known_users), f, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения пользователей: {e}")

def save_nicks():
    try:
        with open(NICKNAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(nicknames, f, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения ников: {e}")

load_data()

# --- PERPLEXITY API ---
async def ask_perplexity(question: str, image_base64: str = None, is_school_task: bool = False) -> str:
    try:
        headers = {
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Базовый промпт
        base_system_prompt = (
            "Твое имя Улитка. "
            "Отвечай обычным текстом, как в мессенджере. "
            "СТРОГО ЗАПРЕЩЕНО использовать Markdown (жирный, курсив, заголовки). "
            "СТРОГО ЗАПРЕЩЕНО использовать LaTeX и мат. символы (\\(, \\[, $). "
            "Пиши формулы текстом: a^2 + b^2 = c^2. "
            "Никаких списков с точками или цифрами, используй простые переносы строк. "
            "Тон: прямой, немного грубый, если вопрос грубый. "
            "Лимит: 524 символа (не обрывай предложение на полуслове). "
            "Если есть фото: сначала опиши что видишь, потом отвечай."
        )
        
        # Дополнение для школьных задач
        if is_school_task:
            system_prompt = base_system_prompt + (
                " ВАЖНО: Это школьная задача. Найди в интернете аналогичную задачу с решением. "
                "Приоритет сайтам, доступным в РФ (znanija, gdz, решуегэ). "
                "В тексте дай краткий ответ, а полную ссылку на решение вставь в конец ответа."
            )
        else:
            system_prompt = base_system_prompt
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Формирование сообщения пользователя
        if image_base64:
            user_text = question if question else "Что на фото? Опиши кратко и реши, если это задача."
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                {"type": "text", "text": user_text}
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": question})
        
        payload = {
            "model": AI_MODEL,
            "messages": messages,
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 600,
            "search_recency_filter": "month",
            "return_images": False,
            "return_related_questions": False,
            "return_citations": True, # Важно: просим вернуть ссылки отдельно
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
                    content = result['choices'][0]['message']['content']
                    citations = result.get('citations', [])
                    
                    if not content:
                        return "Не смог сформулировать ответ."
                    
                    # --- ОЧИСТКА ОТ ФОРМАТИРОВАНИЯ (CLEANER) ---
                    # Убираем LaTeX \[...\] и \(...\)
                    content = re.sub(r'\\\[.*?\\\]', '', content, flags=re.DOTALL)
                    content = re.sub(r'\\\(.*?\\\)', '', content, flags=re.DOTALL)
                    # Убираем $...$ и $$...$$
                    content = re.sub(r'\$\$.*?\$\$', '', content, flags=re.DOTALL)
                    content = re.sub(r'\$.*?\$', '', content)
                    # Убираем Markdown жирный/курсив (**text**, *text*)
                    content = re.sub(r'\*\*([^*]+)\*\*', r'\1', content)
                    content = re.sub(r'\*([^*]+)\*', r'\1', content)
                    content = re.sub(r'__([^_]+)__', r'\1', content)
                    # Убираем Markdown заголовки (## Text)
                    content = re.sub(r'^\s*#{1,6}\s*', '', content, flags=re.MULTILINE)
                    # Убираем Markdown ссылки [text](url) -> оставляем text
                    content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
                    # Убираем сноски вида [1], [2]
                    content = re.sub(r'\[\d+\]', '', content)
                    
                    content = content.strip()
                    
                    # Обрезка текста если слишком длинный
                    if len(content) > 500:
                        content = content[:497] + "..."
                    
                    # --- ДОБАВЛЕНИЕ ССЫЛОК ---
                    # Добавляем уникальные ссылки в конец сообщения
                    if citations:
                        # Берем до 3 уникальных ссылок
                        unique_links = []
                        seen = set()
                        for link in citations:
                            if link not in seen:
                                unique_links.append(link)
                                seen.add(link)
                            if len(unique_links) >= 3:
                                break
                        
                        links_text = "\n\n" + "\n".join(unique_links)
                        content += links_text

                    return content
                
                elif resp.status == 429:
                    return "Слишком много запросов. Подожди минуту."
                else:
                    return f"Ошибка API {resp.status}. Попробуй позже."
                    
    except asyncio.TimeoutError:
        return "Таймаут запроса. Повтори попытку."
    except Exception as e:
        logging.error(f"Perplexity query error: {e}", exc_info=True)
        return "Произошла внутренняя ошибка."

# --- ОБРАБОТЧИКИ BOT ---

async def on_startup(dp):
    # Удаляем вебхуки и старые команды при старте
    await bot.delete_webhook()
    await bot.delete_my_commands()
    logging.info("Бот запущен!")

@dp.message_handler(content_types=types.ContentTypes.NEW_CHAT_MEMBERS, chat_id=ALLOWED_CHAT_ID)
async def on_join(message: types.Message):
    for u in message.new_chat_members:
        if not u.is_bot:
            udata = (u.id, u.username, u.first_name)
            # Удаляем старую запись если есть, добавляем новую
            known_users.discard(next((x for x in known_users if x[0] == u.id), None))
            known_users.add(udata)
            save_users()

# Хендлер для группового чата
@dp.message_handler(content_types=types.ContentTypes.ANY, chat_id=ALLOWED_CHAT_ID)
async def main_handler(message: types.Message):
    # Сохраняем пользователя при любом сообщении
    if not message.from_user.is_bot:
        u = message.from_user
        udata = (u.id, u.username, u.first_name)
        if udata not in known_users:
            known_users.discard(next((x for x in known_users if x[0] == u.id), None))
            known_users.add(udata)
            save_users()
    
    text = message.text or message.caption or ""
    
    # Команда /tip "ник" "@юзер"
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
    
    # Команда /all или /tagall
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
    
    # Команда /ask
    if text.startswith('/ask'):
        # Убираем команду из текста
        question = text[4:].strip()
        
        # Проверка на наличие картинки
        image_base64 = None
        
        # 1. Если картинка прикреплена к сообщению с /ask
        if message.photo:
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

        # 2. Если /ask написано реплаем на сообщение с картинкой
        elif message.reply_to_message and message.reply_to_message.photo:
            try:
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

        # Если нет ни текста вопроса, ни картинки
        if not question and not image_base64:
            return

        await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)

        # Проверка на школьный запрос
        school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем", "физик", "матеш", "алгебр"]
        is_school = any(keyword in question.lower() for keyword in school_keywords) if question else False
        
        answer = await ask_perplexity(question=question, image_base64=image_base64, is_school_task=is_school)
        
        if answer:
            # parse_mode=None чтобы телеграм не пытался парсить оставшиеся символы
            await message.reply(answer, parse_mode=None)
        return

# Хендлер для ЛС (только админ)
@dp.message_handler(chat_type=types.ChatType.PRIVATE)
async def private_handler(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        text = message.text or message.caption or ""
        
        # В ЛС можно писать с /ask или без, если есть картинка
        is_command = text.startswith('/ask')
        question = text[4:].strip() if is_command else text.strip()
        
        image_base64 = None
        if message.photo:
            # Логика загрузки фото такая же
            try:
                photo = message.photo[-1]
                file = await bot.get_file(photo.file_id)
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            image_base64 = base64.b64encode(data).decode('utf-8')
            except: pass
        
        if not question and not image_base64:
            return
            
        await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        
        school_keywords = ["реши", "решить", "задач", "пример"]
        is_school = any(keyword in question.lower() for keyword in school_keywords) if question else False
        
        answer = await ask_perplexity(question=question, image_base64=image_base64, is_school_task=is_school)
        
        if answer:
            await message.reply(answer, parse_mode=None)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
