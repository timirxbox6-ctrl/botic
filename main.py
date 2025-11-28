import logging
import aiohttp
import json
import os
import re
import base64
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

async def download_image_as_base64(file_url: str) -> tuple:
    try:
        logging.info(f"[IMG] Starting download from: {file_url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                logging.info(f"[IMG] Response status: {resp.status}")
                if resp.status == 200:
                    image_data = await resp.read()
                    
                    size_mb = len(image_data) / (1024 * 1024)
                    logging.info(f"[IMG] Downloaded size: {size_mb:.2f} MB ({len(image_data)} bytes)")
                    
                    content_type = resp.headers.get('Content-Type', '').lower()
                    logging.info(f"[IMG] Content-Type: {content_type}")
                    
                    if 'jpeg' in content_type or 'jpg' in content_type:
                        mime_type = 'image/jpeg'
                    elif 'png' in content_type:
                        mime_type = 'image/png'
                    elif 'webp' in content_type:
                        mime_type = 'image/webp'
                    else:
                        if file_url.lower().endswith(('.jpg', '.jpeg')):
                            mime_type = 'image/jpeg'
                        elif file_url.lower().endswith('.png'):
                            mime_type = 'image/png'
                        elif file_url.lower().endswith('.webp'):
                            mime_type = 'image/webp'
                        else:
                            logging.warning(f"[IMG] Unknown format: {content_type}, defaulting to jpeg")
                            mime_type = 'image/jpeg'
                    
                    base64_string = base64.b64encode(image_data).decode('utf-8')
                    
                    logging.info(f"[IMG] MIME type: {mime_type}")
                    logging.info(f"[IMG] Base64 length: {len(base64_string)}")
                    
                    return base64_string, mime_type
                else:
                    logging.error(f"[IMG] Failed to download: HTTP {resp.status}")
                    return None, None
    except Exception as e:
        logging.error(f"[IMG] Download error: {e}", exc_info=True)
        return None, None

async def ask_perplexity(question: str, image_base64: str = None, mime_type: str = None, is_school_task: bool = False) -> str:
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
            "На вопросы с фото сначала кратко опиши что на изображении затем выполняй указанные действия. "
        )
        
        if is_school_task:
            system_prompt = base_system_prompt + (
                "Если попросят решить задачу по математике физике химии биологии "
                "найди в интернете аналогичную с решением проверь что сайт работает в РФ "
                "и дай прямую ссылку просто URL без скобок."
            )
        else:
            system_prompt = base_system_prompt
        
        messages = [{"role": "system", "content": system_prompt}]
        
        if image_base64:
            if not mime_type:
                mime_type = 'image/jpeg'
            
            user_text = question if question else "Проанализируй это изображение детально. Если это задание или упражнение - реши его полностью."
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_base64}"
                    }
                },
                {
                    "type": "text",
                    "text": user_text
                }
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": question})
        
        model = "sonar-pro" if image_base64 else "sonar"
        
        logging.info(f"[API] Sending request to Perplexity")
        logging.info(f"[API] Model: {model}")
        logging.info(f"[API] Has image: {image_base64 is not None}")
        logging.info(f"[API] MIME type: {mime_type if image_base64 else 'N/A'}")
        if image_base64:
            logging.info(f"[API] Image data length: {len(image_base64)}")
        logging.info(f"[API] Question: {question[:100] if question else 'No question'}...")
        
        payload = {
            "model": model,
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
                                           timeout=aiohttp.ClientTimeout(total=120)) as resp:
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
                            logging.error(f"[API] Error {resp.status}: {error_text}")
                            return f"API ошибка {resp.status}. Попробуй позже."
            except asyncio.TimeoutError:
                logging.warning(f"[API] Timeout attempt {attempt + 1}/3")
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                return "Запрос занял слишком много времени. Попробуй упростить вопрос или попробуй позже."
            except Exception as e:
                logging.error(f"[API] Query error on attempt {attempt + 1}: {e}", exc_info=True)
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                return "Ошибка при обработке запроса."
        
        return "Не удалось получить ответ после 3 попыток."
        
    except Exception as e:
        logging.error(f"[API] Query error: {e}", exc_info=True)
        return "Ошибка при обработке запроса."

async def extract_image_url(text: str) -> str:
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text)
    
    for url in urls:
        url_lower = url.lower()
        if url_lower.endswith(('.jpg', '.jpeg', '.png', '.webp')):
            return url
    
    return None

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
        
        logging.info(f"[DEBUG] Question: {question}")
        logging.info(f"[DEBUG] Has photo: {message.photo is not None}")
        logging.info(f"[DEBUG] Has reply photo: {message.reply_to_message and message.reply_to_message.photo is not None if message.reply_to_message else False}")
        if message.photo:
            logging.info(f"[DEBUG] Photo count: {len(message.photo)}")
            logging.info(f"[DEBUG] Largest photo size: {message.photo[-1].file_size}")
        
        await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        
        image_base64 = None
        mime_type = None
        processing_msg = None
        
        if message.photo:
            try:
                processing_msg = await message.answer("Обрабатываю изображение...")
                
                photo = message.photo[-1]
                logging.info(f"[DEBUG] Getting file for photo_id: {photo.file_id}")
                
                file = await bot.get_file(photo.file_id)
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
                
                logging.info(f"[DEBUG] File URL: {file_url}")
                
                image_base64, mime_type = await download_image_as_base64(file_url)
                
                if not image_base64:
                    logging.error(f"[DEBUG] Image download failed!")
                    await processing_msg.edit_text("Ошибка обработки изображения. Попробуй ещё раз.")
                    return
                
                logging.info(f"[DEBUG] Image downloaded successfully, mime: {mime_type}")
                
                try:
                    await processing_msg.edit_text("бем бем бем...")
                except:
                    pass
                    
            except Exception as e:
                logging.error(f"[DEBUG] Image processing exception: {e}", exc_info=True)
                if processing_msg:
                    await processing_msg.edit_text("Не могу загрузить фото.")
                else:
                    await message.reply("Не могу загрузить фото.")
                return
        elif message.reply_to_message and message.reply_to_message.photo:
            try:
                processing_msg = await message.answer("Обрабатываю изображение...")
                
                photo = message.reply_to_message.photo[-1]
                logging.info(f"[DEBUG] Getting reply photo file_id: {photo.file_id}")
                
                file = await bot.get_file(photo.file_id)
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
                
                image_base64, mime_type = await download_image_as_base64(file_url)
                
                if not image_base64:
                    await processing_msg.edit_text("Ошибка обработки изображения. Попробуй ещё раз.")
                    return
                
                try:
                    await processing_msg.edit_text("бем бем бем...")
                except:
                    pass
                    
            except Exception as e:
                logging.error(f"[DEBUG] Reply image error: {e}", exc_info=True)
                if processing_msg:
                    await processing_msg.edit_text("Не могу загрузить фото.")
                else:
                    await message.reply("Не могу загрузить фото.")
                return
        
        if not image_base64:
            extracted_url = await extract_image_url(question)
            if extracted_url:
                try:
                    processing_msg = await message.answer("Загружаю изображение по ссылке...")
                    image_base64, mime_type = await download_image_as_base64(extracted_url)
                    
                    if not image_base64:
                        await processing_msg.edit_text("Не могу загрузить изображение по ссылке.")
                        return
                    
                    try:
                        await processing_msg.edit_text("Анализирую с помощью AI...")
                    except:
                        pass
                except Exception as e:
                    logging.error(f"[DEBUG] URL image error: {e}", exc_info=True)
                    await message.reply("Не могу загрузить изображение по ссылке.")
                    return
        
        if not question and not image_base64:
            logging.info(f"[DEBUG] No question and no image, returning")
            return
        
        school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
        is_school = any(keyword in question.lower() for keyword in school_keywords) if question else False
        
        logging.info(f"[DEBUG] Calling Perplexity - has_image: {image_base64 is not None}, is_school: {is_school}")
        
        answer = await ask_perplexity(question=question, image_base64=image_base64, mime_type=mime_type, is_school_task=is_school)
        
        if processing_msg:
            try:
                await processing_msg.delete()
            except:
                pass
        
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
            
            await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
            
            image_base64 = None
            mime_type = None
            processing_msg = None
            
            if message.photo:
                try:
                    processing_msg = await message.answer("Обрабатываю изображение...")
                    
                    photo = message.photo[-1]
                    file = await bot.get_file(photo.file_id)
                    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
                    
                    image_base64, mime_type = await download_image_as_base64(file_url)
                    
                    if not image_base64:
                        await processing_msg.edit_text("Ошибка обработки изображения. Попробуй ещё раз.")
                        return
                    
                    try:
                        await processing_msg.edit_text("бем бем бем...")
                    except:
                        pass
                        
                except Exception as e:
                    logging.error(f"Image download error: {e}")
                    if processing_msg:
                        await processing_msg.edit_text("Не могу загрузить фото.")
                    else:
                        await message.reply("Не могу загрузить фото.")
                    return
            
            if not image_base64:
                extracted_url = await extract_image_url(question)
                if extracted_url:
                    try:
                        processing_msg = await message.answer("Загружаю фото по ссылке...")
                        image_base64, mime_type = await download_image_as_base64(extracted_url)
                        
                        if not image_base64:
                            await processing_msg.edit_text("хуйня фото.")
                            return
                        
                        try:
                            await processing_msg.edit_text("бем бем бем...")
                        except:
                            pass
                    except Exception as e:
                        logging.error(f"URL image download error: {e}")
                        await message.reply("не могу по ссылке просмотреть.")
                        return
            
            if not question and not image_base64:
                return
            
            school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
            is_school = any(keyword in question.lower() for keyword in school_keywords) if question else False
            
            answer = await ask_perplexity(question=question, image_base64=image_base64, mime_type=mime_type, is_school_task=is_school)
            
            if processing_msg:
                try:
                    await processing_msg.delete()
                except:
                    pass
            
            if answer:
                await message.reply(answer, parse_mode=None)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
