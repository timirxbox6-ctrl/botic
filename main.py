import logging
import aiohttp
import json
import os
import re
import asyncio
import base64
import aiosqlite
import pytz
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram_calendar import simple_cal_callback, SimpleCalendar
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle

BOT_TOKEN = os.getenv("BOT_TOKEN")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    
    # Поддержка нескольких групп через запятую
    allowed_chats_str = os.getenv("ALLOWED_CHAT_ID", "0")
    ALLOWED_CHAT_IDS = [int(x.strip()) for x in allowed_chats_str.split(",") if x.strip()]
    
    if not ALLOWED_CHAT_IDS or ALLOWED_CHAT_IDS == [0]:
        logging.error("ALLOWED_CHAT_ID не настроен!")
        exit(1)
        
except ValueError:
    logging.error("ADMIN_ID или ALLOWED_CHAT_ID должны быть числами!")
    exit(1)

DB_FILE = "/data/users_db.json"
NICKNAMES_FILE = "/data/nicks.json"
EVENTS_DB = "/data/events.db"

if not BOT_TOKEN or not PERPLEXITY_API_KEY:
    logging.error("ОШИБКА: Не найдены BOT_TOKEN или PERPLEXITY_API_KEY в переменных окружения!")
    exit(1)

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)
known_users = set()
nicknames = {}

EKB_TZ = pytz.timezone('Asia/Yekaterinburg')
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Фильтр для проверки разрешенных чатов
def is_allowed_chat(message: types.Message) -> bool:
    return message.chat.id in ALLOWED_CHAT_IDS

# FSM States
class EventStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_description = State()

class MafiaStates(StatesGroup):
    registration = State()
    night = State()
    day = State()

# Игры Мафии
mafia_games = {}

class MafiaGame:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.players = []
        self.mafia = []
        self.detective = None
        self.doctor = None
        self.alive = []
        self.phase = "registration"
        self.day_num = 0

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

async def init_events_db():
    async with aiosqlite.connect(EVENTS_DB) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                title TEXT,
                description TEXT,
                event_date TEXT,
                event_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                google_event_id TEXT
            )
        ''')
        await db.commit()

async def save_event(user_id, chat_id, title, description, event_date, event_time, google_event_id=None):
    async with aiosqlite.connect(EVENTS_DB) as db:
        await db.execute('''
            INSERT INTO events (user_id, chat_id, title, description, event_date, event_time, google_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, chat_id, title, description, event_date, event_time, google_event_id))
        await db.commit()

async def get_upcoming_events(chat_id, limit=10):
    async with aiosqlite.connect(EVENTS_DB) as db:
        async with db.execute('''
            SELECT title, description, event_date, event_time
            FROM events
            WHERE chat_id = ? AND event_date >= date('now')
            ORDER BY event_date, event_time
            LIMIT ?
        ''', (chat_id, limit)) as cursor:
            return await cursor.fetchall()

def get_calendar_service():
    creds = None
    if os.path.exists('/data/token.pickle'):
        with open('/data/token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('/data/credentials.json'):
                return None
            flow = InstalledAppFlow.from_client_secrets_file(
                '/data/credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open('/data/token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    return build('calendar', 'v3', credentials=creds)

def create_google_event(title, description, start_datetime, duration_minutes=60):
    try:
        service = get_calendar_service()
        if not service:
            return None
        
        end_datetime = start_datetime + timedelta(minutes=duration_minutes)
        
        event = {
            'summary': title,
            'description': description,
            'start': {
                'dateTime': start_datetime.isoformat(),
                'timeZone': 'Asia/Yekaterinburg',
            },
            'end': {
                'dateTime': end_datetime.isoformat(),
                'timeZone': 'Asia/Yekaterinburg',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 30},
                ],
            },
        }
        
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return created_event.get('id')
    except Exception as e:
        logging.error(f"Google Calendar error: {e}")
        return None

load_data()

async def download_photo(file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    photo_bytes = await bot.download_file(file.file_path)
    return photo_bytes.read()

async def ask_perplexity(question: str, is_school_task: bool = False, photo_base64: str = None) -> str:
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
            "будь веселым"
        )
        
        if is_school_task:
            system_prompt = base_system_prompt + (
                "Если попросят решить задачу по математике физике химии биологии "
                "найди в интернете аналогичную с решением проверь что сайт работает в РФ "
                "и дай прямую ссылку просто URL без скобок."
            )
        else:
            system_prompt = base_system_prompt
        
        model = "sonar-pro" if photo_base64 else "sonar"
        
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        if photo_base64:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{photo_base64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": question
                    }
                ]
            })
        else:
            messages.append({"role": "user", "content": question})
        
        logging.info(f"Sending request to Perplexity with model: {model}")
        logging.info(f"Question: {question[:100]}...")
        
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
    await init_events_db()
    logging.info(f"Бот запущен для групп: {ALLOWED_CHAT_IDS}")

# === СОБЫТИЯ ===
@dp.message_handler(is_allowed_chat, commands=['event'], state='*')
async def cmd_add_event(message: types.Message):
    await message.reply("Введите название события:")
    await EventStates.waiting_for_title.set()

@dp.message_handler(state=EventStates.waiting_for_title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Выберите дату:", reply_markup=await SimpleCalendar().start_calendar())
    await EventStates.waiting_for_date.set()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('simple_calendar'), state=EventStates.waiting_for_date)
async def process_date(callback_query: types.CallbackQuery, state: FSMContext):
    selected, date = await SimpleCalendar().process_selection(callback_query, callback_query.data)
    
    if selected:
        await callback_query.answer()
        await state.update_data(event_date=date.strftime('%Y-%m-%d'))
        
        time_kb = types.InlineKeyboardMarkup(row_width=3)
        time_kb.add(
            types.InlineKeyboardButton("08:00", callback_data="time_08:00"),
            types.InlineKeyboardButton("09:00", callback_data="time_09:00"),
            types.InlineKeyboardButton("10:00", callback_data="time_10:00"),
            types.InlineKeyboardButton("12:00", callback_data="time_12:00"),
            types.InlineKeyboardButton("14:00", callback_data="time_14:00"),
            types.InlineKeyboardButton("16:00", callback_data="time_16:00"),
            types.InlineKeyboardButton("18:00", callback_data="time_18:00"),
            types.InlineKeyboardButton("20:00", callback_data="time_20:00"),
            types.InlineKeyboardButton("22:00", callback_data="time_22:00")
        )
        time_kb.add(types.InlineKeyboardButton("Свое время", callback_data="time_custom"))
        
        await callback_query.message.edit_text(
            f"Дата: {date.strftime('%d.%m.%Y')}\n\nВыберите время:",
            reply_markup=time_kb
        )
        await EventStates.waiting_for_time.set()

@dp.callback_query_handler(lambda c: c.data.startswith("time_"), state=EventStates.waiting_for_time)
async def process_time(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    time_str = callback.data.replace("time_", "")
    
    if time_str == "custom":
        await callback.message.answer("Введите время в формате ЧЧ:ММ (например 15:30):")
        return
    
    await state.update_data(event_time=time_str)
    await callback.message.answer("Введите описание события или /skip для пропуска:")
    await EventStates.waiting_for_description.set()

@dp.message_handler(state=EventStates.waiting_for_time)
async def process_custom_time(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, '%H:%M')
        await state.update_data(event_time=message.text)
        await message.answer("Введите описание события или /skip для пропуска:")
        await EventStates.waiting_for_description.set()
    except ValueError:
        await message.answer("Неверный формат. Введите время в формате ЧЧ:ММ:")

@dp.message_handler(state=EventStates.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    data = await state.get_data()
    description = "Нет описания" if message.text == "/skip" else message.text
    
    event_datetime = EKB_TZ.localize(
        datetime.strptime(f"{data['event_date']} {data['event_time']}", '%Y-%m-%d %H:%M')
    )
    
    google_event_id = create_google_event(
        title=data['title'],
        description=description,
        start_datetime=event_datetime
    )
    
    await save_event(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        title=data['title'],
        description=description,
        event_date=data['event_date'],
        event_time=data['event_time'],
        google_event_id=google_event_id
    )
    
    calendar_status = "Добавлено в эвенты" if google_event_id else "Сохранено только в боте"
    
    await message.answer(
        f"Событие создано\n\n"
        f"{data['title']}\n"
        f"{data['event_date']} в {data['event_time']}\n"
        f"{description}\n\n"
        f"{calendar_status}"
    )
    await state.finish()

@dp.message_handler(is_allowed_chat, commands=['events'])
async def cmd_events(message: types.Message):
    events = await get_upcoming_events(message.chat.id)
    
    if not events:
        await message.reply("Нет предстоящих событий")
        return
    
    text = "Предстоящие события:\n\n"
    for i, (title, desc, date, time) in enumerate(events, 1):
        text += f"{i}. {title}\n{date} в {time}\n{desc}\n\n"
    
    await message.reply(text)

# === МАФИЯ ===
@dp.message_handler(is_allowed_chat, commands=['mafia'])
async def cmd_mafia(message: types.Message):
    chat_id = message.chat.id
    
    if chat_id in mafia_games:
        await message.reply("Игра уже идет")
        return
    
    mafia_games[chat_id] = MafiaGame(chat_id)
    
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Войти в игру", callback_data="mafia_join"))
    kb.add(types.InlineKeyboardButton("Начать игру", callback_data="mafia_start"))
    
    await message.answer(
        "Игра МАФИЯ\n\n"
        "Роли:\n"
        "Мафия - убивает игроков ночью\n"
        "Мирные жители - голосуют днем\n"
        "Детектив - проверяет игроков ночью\n"
        "Доктор - спасает игроков ночью\n\n"
        "Минимум 4 игрока для старта",
        reply_markup=kb
    )

@dp.callback_query_handler(lambda c: c.data == "mafia_join")
async def mafia_join(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    
    if chat_id not in mafia_games:
        await callback.answer("Игра не начата")
        return
    
    game = mafia_games[chat_id]
    user = callback.from_user
    
    if user.id in [p['id'] for p in game.players]:
        await callback.answer("Вы уже в игре")
        return
    
    game.players.append({
        'id': user.id,
        'name': user.first_name,
        'role': None,
        'alive': True
    })
    
    await callback.answer("Вы в игре")
    await callback.message.answer(f"{user.first_name} присоединился. Всего игроков: {len(game.players)}")

@dp.callback_query_handler(lambda c: c.data == "mafia_start")
async def mafia_start(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    
    if chat_id not in mafia_games:
        await callback.answer("Игра не начата")
        return
    
    game = mafia_games[chat_id]
    
    if len(game.players) < 4:
        await callback.answer("Нужно минимум 4 игрока")
        return
    
    players = game.players.copy()
    random.shuffle(players)
    
    mafia_count = max(1, len(players) // 3)
    for i in range(mafia_count):
        players[i]['role'] = 'mafia'
        game.mafia.append(players[i]['id'])
    
    players[mafia_count]['role'] = 'detective'
    game.detective = players[mafia_count]['id']
    
    if len(players) > mafia_count + 1:
        players[mafia_count + 1]['role'] = 'doctor'
        game.doctor = players[mafia_count + 1]['id']
    
    for i in range(mafia_count + 2, len(players)):
        players[i]['role'] = 'citizen'
    
    game.alive = [p['id'] for p in players]
    game.phase = "night"
    
    role_text = {
        'mafia': 'Вы МАФИЯ. Убивайте мирных жителей.',
        'detective': 'Вы ДЕТЕКТИВ. Проверяйте подозрительных.',
        'doctor': 'Вы ДОКТОР. Спасайте игроков.',
        'citizen': 'Вы МИРНЫЙ ЖИТЕЛЬ. Ищите мафию.'
    }
    
    for player in players:
        try:
            await bot.send_message(player['id'], f"Ваша роль: {role_text[player['role']]}")
        except:
            pass
    
    await callback.message.answer(
        f"Игра началась. Участвует {len(players)} игроков.\n"
        f"Наступает ночь. Роли отправлены в личные сообщения."
    )

@dp.message_handler(is_allowed_chat, commands=['mafia_stop'])
async def cmd_mafia_stop(message: types.Message):
    chat_id = message.chat.id
    if chat_id in mafia_games:
        del mafia_games[chat_id]
        await message.reply("Игра остановлена")
    else:
        await message.reply("Игра не идет")

# === ОСТАЛЬНЫЕ ХЕНДЛЕРЫ ===
@dp.message_handler(is_allowed_chat, content_types=types.ContentTypes.NEW_CHAT_MEMBERS)
async def on_join(message: types.Message):
    for u in message.new_chat_members:
        if not u.is_bot:
            udata = (u.id, u.username, u.first_name)
            known_users.discard(next((x for x in known_users if x[0] == u.id), None))
            known_users.add(udata)
            save_users()

@dp.message_handler(is_allowed_chat, content_types=types.ContentTypes.ANY)
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
        
        photo_base64 = None
        if message.photo:
            photo = message.photo[-1]
            if photo.file_size > 20 * 1024 * 1024:
                await message.reply("Фото слишком большое. Максимум 20 МБ.")
                return
            
            try:
                photo_bytes = await download_photo(photo.file_id)
                photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
            except Exception as e:
                logging.error(f"Photo download error: {e}")
                await message.reply("Ошибка при загрузке фото.")
                return
        
        if not question and not photo_base64:
            return
        
        if photo_base64 and not question:
            question = "Реши эту задачу"
        
        await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
        
        school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
        is_school = any(keyword in question.lower() for keyword in school_keywords) or photo_base64
        
        answer = await ask_perplexity(question=question, is_school_task=is_school, photo_base64=photo_base64)
        
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
            
            photo_base64 = None
            if message.photo:
                photo = message.photo[-1]
                if photo.file_size > 20 * 1024 * 1024:
                    await message.reply("Фото слишком большое. Максимум 20 МБ.")
                    return
                
                try:
                    photo_bytes = await download_photo(photo.file_id)
                    photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
                except Exception as e:
                    logging.error(f"Photo download error: {e}")
                    await message.reply("Ошибка при загрузке фото.")
                    return
            
            if not question and not photo_base64:
                return
            
            if photo_base64 and not question:
                question = "Реши эту задачу"
            
            await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
            
            school_keywords = ["реши", "решить", "задач", "пример", "уравнение", "формул", "теорем"]
            is_school = any(keyword in question.lower() for keyword in school_keywords) or photo_base64
            
            answer = await ask_perplexity(question=question, is_school_task=is_school, photo_base64=photo_base64)
            
            if answer:
                await message.reply(answer, parse_mode=None)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
