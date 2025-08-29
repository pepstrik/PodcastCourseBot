import json, os, sys, time
import threading
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)
from fpdf import FPDF
from telegram import InputFile
import logging

from config import TOKEN, ADMIN_CHAT_ID

# --- Files and persistent storage ---
COURSE_FILE = 'full_course_data.json'
PROGRESS_FILE = 'progress.json'
SAVE_INTERVAL = 60  # секунд между автосохранениями

# Кэш в памяти
_progress_cache: dict = {}
_progress_lock = threading.Lock()
user_final_passed = {}
user_states = {}
user_data = {}  # Словарь: {uid: {lang: "ru" или "en"}}
progress: dict  # Словарь: {uid: {step: номер, финальный тест пройден или нет}}
user_final: dict[str, dict[str,int]] = {}

# логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def load_progress() -> dict:
    """Однократно при старте загружает прогресс из файла в кэш."""
    global _progress_cache
    if os.path.exists(PROGRESS_FILE):
        with _progress_lock:
            with open(PROGRESS_FILE, encoding='utf-8') as f:
                _progress_cache = json.load(f)
    else:
        _progress_cache = {}
    return _progress_cache

def save_progress() -> None:
    """Сохраняет текущий кэш на диск."""
    with _progress_lock:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_progress_cache, f, ensure_ascii=False, indent=2)

def auto_save_loop():
    while True:
        try:
            time.sleep(SAVE_INTERVAL)
            save_progress()
        except Exception as e:
            logger.exception("Ошибка в auto_save_loop")
            # Немного подождать, чтобы не спамить логом
            time.sleep(5)

def check_course_file(path):
    if not os.path.exists(path):
        logger.error(f"❗ Error: course file {path} not found.")
        sys.exit(1)
    size_kb = os.path.getsize(path) / 1024
    logger.info(f"📦 Course file size: {size_kb:.1f} KB")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            course = json.load(f)
    except Exception as e:
        logger.error(f"❗ JSON load error: {e}")
        sys.exit(1)
    logger.info(f"✅ Course loaded, top-level keys: {list(course.keys())}")
    return course

# Загрузка
COURSE = check_course_file(COURSE_FILE)
    
# --- Helpers ---
def t(key, lang):
    return COURSE['texts'][key][lang]


# Тексты для сертификата
CERT_TEXT = {
    'ru': {
        'title': "Сертификат о прохождении курса",
        'subtitle': "Настоящим подтверждается, что",
        'subsubtitle': "успешно прошёл(-а) мини-курс «Подкаст за 7 шагов»",
        'footer': "Теперь вы не просто человек, а человек-голос.\nСлушайте себя. Говорите уверенно.",
        'date_label': "Дата прохождения:"
    },
    'en': {
        'title': "Certificate of Completion",
        'subtitle': "This is to certify that",
        'subsubtitle': "has successfully completed the mini-course Podcast in 7 Steps",
        'footer': "Now you are not just a person, you are a voice.\nSpeak confidently and share your story!",
        'date_label': "Date of completion:"
    }
}
def generate_certificate_fpdf(name: str, lang: str, date_str: str, output_path: str = 'certificate.pdf') -> str:
    pdf = FPDF('L', 'mm', 'A4')
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    base      = os.path.dirname(__file__)
    icon_path = os.path.join(base, 'assets', 'mic.png')
    icon_w    = 30  # ширина иконки в мм
    fonts_dir = os.path.join(base, 'fonts')
    regular   = os.path.join(fonts_dir, 'DejaVuSans.ttf')
    bold      = os.path.join(fonts_dir, 'DejaVuSans-Bold.ttf')
    # шрифты для кириллицы
    pdf.add_font('DejaVu','', regular, uni=True)
    pdf.add_font('DejaVu','B', bold,    uni=True)
    
    # --- фон и рамка (оставляем как было) ---
    pdf.set_fill_color(111, 78, 55)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')
    pdf.set_draw_color(245, 245, 220)
    pdf.set_line_width(4)
    pdf.rect(10, 10, pdf.w-20, pdf.h-20)

    # --- вставляем PNG-иконку ---
    image_y = pdf.h * 0.20
    pdf.image(icon_path,
              x=(pdf.w - icon_w) / 2,
              y=image_y,
              w=icon_w)

    # вот тут — спускаем текст ниже иконки + 5 мм отступ
    pdf.set_y(image_y + icon_w + 5)
    
    # заголовок
    pdf.add_font('DejaVu','B', os.path.join(base,'fonts','DejaVuSans-Bold.ttf'), uni=True)
    pdf.set_font('DejaVu','B',36)
    pdf.set_text_color(245, 245, 220)
    pdf.cell(0, 15, CERT_TEXT[lang]['title'], ln=1, align='C')
    
    # подзаголовок
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('DejaVu', '', 24)
    pdf.cell(0, 12, CERT_TEXT[lang]['subtitle'], ln=1, align='C')
    pdf.ln(5)

    # имя
    pdf.set_text_color(245, 245, 220)
    pdf.set_font('DejaVu', 'B', 32)
    pdf.cell(0, 15, name, ln=1, align='C')
    pdf.ln(8)

    # подзаголовок
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('DejaVu', '', 24)
    pdf.cell(0, 12, CERT_TEXT[lang]['subsubtitle'], ln=1, align='C')
    pdf.ln(8)

    # дата прохождения
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('DejaVu', '', 18)
    pdf.cell(
        0, 10,
        f"{CERT_TEXT[lang]['date_label']} {date_str}",
        ln=1, align='C'
    )
    pdf.ln(8)

    # футер
    pdf.set_text_color(245, 245, 220)
    pdf.set_font('DejaVu', '', 18)
    pdf.multi_cell(0, 10, CERT_TEXT[lang]['footer'], align='C')
    pdf.ln(10)

    pdf.output(output_path)
    return output_path


async def name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка имени для сертификата.
    """
    if not context.user_data.get('awaiting_name'):
        return False  # <--- ВАЖНО: False, чтобы другие хендлеры могли обработать сообщение

    lang = context.user_data.get('lang', 'ru')
    name = update.message.text.strip()
    uid = str(update.message.from_user.id)

    context.user_data['awaiting_name'] = False

    # Берём сохранённую дату или используем текущую
    date_str = progress.get(uid, {}).get('completion_date') \
               or date.today().strftime('%d.%m.%Y')

    try:
        output_path = generate_certificate_fpdf(
            name, lang, date_str,
            output_path=f"certificate_{uid}.pdf"
        )
        with open(output_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"certificate_{name}.pdf",
                caption=f"🎓 {t('cert', lang)} — {name}"
            )
        
        # --- Затем отправляем кнопку "🏠 Главное меню"
        kb = [
            [InlineKeyboardButton(t('back_main', lang), callback_data='back_main')]
        ]
        await update.message.reply_text(
            text="✅ " + t('cert_ready', lang),  # Можно красивый текст типа "Сертификат готов!"
            reply_markup=InlineKeyboardMarkup(kb)
        )

    except Exception:
        logger.exception("Ошибка отправки сертификата")
        await update.message.reply_text("❗ Сертификат сгенерирован, но не удалось отправить его.")

    return True  # <--- ВАЖНО: True, чтобы остановить дальнейшую обработку


    # -- НЕ УДАЛЯЕМ файл, он останется на диске для дальнейшего использования --
    # try:
    #     os.remove(cert_path)
    # except OSError:
    #     pass


# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Стартовое сообщение с выбором языка."""
    # Сохраняем аргумент команды (например, 'final_ru')
    if context.args:
        context.user_data['start_param'] = context.args[0]

    kb = [[
        InlineKeyboardButton('🇷🇺 Русский', callback_data='lang:ru'),
        InlineKeyboardButton('🇬🇧 English', callback_data='lang:en')
    ]]
    prompt = "<b>Пожалуйста, выберите язык | Please choose your language:</b>"
    await update.message.reply_text(
        prompt,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='HTML'
    )


async def finaltest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    lang = context.user_data.get('lang', 'ru')

    # Если язык ещё не выбран, задаём по умолчанию
    if not lang:
        lang = 'ru'
        context.user_data['lang'] = lang

    # Обнуляем финальный тест
    user_final[uid] = {'q': 0, 'score': 0}
    await send_final_question(update, context)

async def lang_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    chosen = query.data.split(':', 1)[1]
    context.user_data['lang'] = chosen

    # Обновим прогресс
    progress.setdefault(uid, {'step': 1, 'final_passed': False})
    save_progress()

    welcome = COURSE['texts']['welcome'][chosen]
    overview = COURSE['texts']['overview'][chosen]

    # Проверим, передавался ли start_param
    start_param = context.user_data.pop('start_param', None)

    # Стартовое сообщение
    await query.message.reply_text(
        f"{welcome}\n\n{overview}",
        parse_mode='HTML'
    )

    # И если был deep-linking
    if start_param and start_param.startswith('final'):
        user_final[uid] = {'q': 0, 'score': 0}
        return await send_final_question(update, context)

    # Иначе — главное меню
    return await query.message.reply_text(
        t('main_menu_title', chosen),
        reply_markup=build_main_menu(uid, chosen)
    )

def get_user_language(uid: str) -> str:
    # если вы храните языки в user_data, можно сделать так:
    return user_data.get(uid, {}).get('lang', 'ru')

def build_main_menu(uid: int, lang: str) -> InlineKeyboardMarkup:
    st = progress.get(str(uid), {})
    final_passed = st.get('final_passed', False)
    kb = []

    # 📋 Показать шаги
    kb.append([
        InlineKeyboardButton(
            t('menu_show_course', lang),
            callback_data='menu_show_course'
        )
    ])

    # 🌟 Финальный тест
    kb.append([
        InlineKeyboardButton(
            t('menu_final', lang),
            callback_data='menu_final'
        )
    ])
    
    # 🎓 Сертификат # 💖 Поддержка и 📚 Бонусы — только после финального теста
    if final_passed:
        kb.append([
            InlineKeyboardButton(t('menu_certificate', lang), callback_data='menu_certificate')
        ])
        kb.append([
            InlineKeyboardButton(t('menu_bonus', lang), callback_data='menu_bonus')
        ])
        
        kb.append([
        InlineKeyboardButton(t('menu_support', lang), callback_data='menu_support')
        ])

    # ✉ Обратная связь
    kb.append([
        InlineKeyboardButton(t('menu_feedback', lang), callback_data='menu_feedback')
    ])

    # ❓ Задать вопрос
    kb.append([
        InlineKeyboardButton(t('menu_ask', lang), callback_data='menu_ask')
    ])

    return InlineKeyboardMarkup(kb)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid  = str(query.from_user.id)
    lang = context.user_data.get('lang', 'ru')

    try:
        # 0) Начать курс (новый пользователь: step == 0)
        if data == 'menu_start_course':
            return await show_course(update, context)
        
        # 1) Смена языка
        if data.startswith('lang:'):
            return await lang_chosen(update, context)

        # 2) 📋 Посмотреть шаги
        if data == 'menu_show_course':
            return await show_course(update, context)
        
        if data == 'show_course':  
            return await show_course(update, context)
        
        # 3) Выбор шага
        if data.startswith('select_step:'):
            return await select_step(update, context)

        # 4) Мини-тест внутри шага
        if data.startswith('test_step:'):
            return await take_test(update, context)

        # 5) Вход в финальный тест
        if data == 'menu_final':
            user_final[uid] = {'q': 0, 'score': 0}
            return await send_final_question(update, context)

        # 6) Ответ на финальный тест
        if data.startswith('test_final:'):
            return await handle_final_answer(update, context)


        # 7) Запрос имени для сертификата
        # --- Получить сертификат
        if data == 'menu_certificate':
            if not user_final_passed.get(uid):
                await query.message.reply_text(
                    t('certificate_locked', lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('back_main', lang), callback_data='back_main')]])
                )
                return
            
            # Запросить имя для сертификата
            text = COURSE['texts']['certificate_message'][lang]
            kb = [
                [InlineKeyboardButton(t('enter_name_button', lang), callback_data='enter_name')],
                [InlineKeyboardButton(t('back_main',       lang), callback_data='back_main')]
            ]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
            return

        if data == 'enter_name':
            # Устанавливаем флаг, что теперь ждём имя
            context.user_data['awaiting_name'] = True
            await query.message.reply_text(
                t('enter_name_prompt', lang)
            )
            return
 
        # 8) Бонусы
        if data == 'menu_bonus':
            url = COURSE['bonus']['links'][lang]
            bonus_message = COURSE['texts']['bonus_title'][lang] + "\n\n" + COURSE['texts']['bonus_text'][lang]

            kb = [
                [InlineKeyboardButton(COURSE['texts']['btn_bonus'][lang], url=url)],
                [InlineKeyboardButton(t('back_main', lang), callback_data='back_main')]
            ]
            await query.message.reply_text(
                bonus_message,
                reply_markup=InlineKeyboardMarkup(kb),
                disable_web_page_preview=False
            )
            return

        # 9) Поддержать курс
        if data == 'menu_support':
            if not user_final_passed.get(uid):
                await query.message.reply_text(
                    t('bonus_locked', lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t('back_main', lang), callback_data='back_main')]])
                )
                return
                 
            url = COURSE['texts']['support_link']
            support_message = COURSE['support_text'][lang]

            kb = [
                [InlineKeyboardButton(COURSE['btn_support'][lang], url=url)],
                [InlineKeyboardButton(t('back_main', lang), callback_data='back_main')]
            ]

            await query.message.reply_text(
                support_message,
                reply_markup=InlineKeyboardMarkup(kb),
                disable_web_page_preview=False
            )
            return

        # 10) Обратная связь
        if data == 'menu_feedback':
            url = COURSE['support_form_link'][lang]
            feedback_message = COURSE['texts'].get('feedback_message', {}).get(lang)
            
            kb = [
                [InlineKeyboardButton(COURSE['texts']['btn_feedback'][lang], url=url)],
                [InlineKeyboardButton(t('back_main', lang), callback_data='back_main')]
            ]

            await query.message.reply_text(
                feedback_message,
                reply_markup=InlineKeyboardMarkup(kb),
                disable_web_page_preview=False
            )
            return


        # 11) Задать вопрос
        if data == 'menu_ask':
            # переключаем бота в режим ожидания текста
            context.user_data['awaiting_question'] = True
            await query.message.reply_text(
                COURSE['ask_prompt'][lang],
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t('cancel_question', lang), callback_data='cancel_question')]
                ])
            )
            logger.info("button_handler got callback_data=%s, awaiting_question now=%s",
                        data,
                        context.user_data.get('awaiting_question'))
            return

        # 12) Назад в главное меню
        if data == 'back_main':
            return await query.message.reply_text(
                t('main_menu_title', lang),
                reply_markup=build_main_menu(uid, lang)
            )
        if data == 'cancel_final':
            user_final.pop(uid, None)
            return await query.message.reply_text(
                t('cancelled', lang),
                reply_markup=build_main_menu(uid, lang)
            )

        if data == 'cancel_question':
            context.user_data['awaiting_question'] = False
            await query.message.reply_text(
                t('cancelled_question', lang),
                reply_markup=build_main_menu(uid, lang)
            )
            return

        # во всех остальных случаях
        await query.message.reply_text(t('unknown', lang))

    except Exception:
        logger.exception("Error in button_handler for data=%s", data)
        await query.message.reply_text("❗ Произошла ошибка, смотрите логи.")
        

# --- Обработчик текстовых сообщений ---
async def question_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.message.from_user.id)
    lang = context.user_data.get('lang', 'ru')
    
    # 1) Имя для сертификата?
    if await name_handler(update, context):
        # name_handler сбросил awaiting_name и выслал документ
        return

    # 2) Ждём вопрос?
    if context.user_data.get('awaiting_question'):
        question = update.message.text.strip()
        username = update.message.from_user.username or "без_username"

        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"❓ Новый вопрос от @{username} ({uid}):\n\n{question}"
        )
        # отвечаем пользователю
        await update.message.reply_text(
            t('ask_sent', lang),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('btn_back_main', lang), callback_data='back_main')]
            ])
        )
        # сбросим флаг и выйдем
        context.user_data['awaiting_question'] = False
        return

    # 3) Всё прочее → «неизвестная команда»
    await update.message.reply_text(
        t('unknown', lang),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('btn_back_main', lang), callback_data='back_main')]
        ])
    )
    
def build_course_menu(uid: str, lang: str) -> InlineKeyboardMarkup:
    kb = []

    for idx, step in enumerate(COURSE['steps'], start=1):
        title = step['title'][lang] if isinstance(step['title'], dict) else step['title']
        cb = f"select_step:{idx}"
        label = f"{title}"
        kb.append([InlineKeyboardButton(label, callback_data=cb)])

    # «Назад в главное меню»
    kb.append([InlineKeyboardButton(t('back_main', lang), callback_data='back_main')])
    # «Задать вопрос»
    kb.append([InlineKeyboardButton(t('menu_ask', lang), callback_data='menu_ask')])

    return InlineKeyboardMarkup(kb)


async def show_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang    = context.user_data.get('lang', 'ru')

    kb = []
    for idx, step in enumerate(COURSE['steps'], start=1):
        title = step['title'][lang] if isinstance(step['title'], dict) else step['title']
        label = f"{title}"
        cb = f"select_step:{idx}"
        kb.append([InlineKeyboardButton(label, callback_data=cb)])

    kb.append([ InlineKeyboardButton(t('back_main', lang), callback_data='back_main') ])

    await query.message.reply_text(
        t('course_list', lang),
        reply_markup=InlineKeyboardMarkup(kb)
    )


# Select and display a step
async def select_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # всегда отвечаем на клик

    lang = context.user_data.get('lang', 'ru')
    _, sid_str = query.data.split(':')
    sid = int(sid_str)

    step = COURSE['steps'][sid-1]
    title = step['title'][lang] if isinstance(step['title'], dict) else step['title']
    prog  = t('progress', lang).format(step=sid, total=len(COURSE['steps']))
    header = step.get('header', {}).get(lang, '')
    body   = step.get('body', {}).get(lang, step.get('text', ''))

    text = (
        f"<b>{title}</b>\n"
        f"<i>{prog}</i>\n\n"
        f"<b>{header}</b>\n\n"
        f"{body}"
    )

    # Если последний шаг — предлагаем финальный тест
    kb = []
    if sid == len(COURSE['steps']):
        kb.append([
            InlineKeyboardButton(t('menu_final', lang), callback_data='menu_final')
        ])
    else:
        kb.append([
            InlineKeyboardButton(t('start_test', lang), callback_data=f"test_step:{sid}:start")
        ])

    kb.append([
        InlineKeyboardButton(t('back_steps', lang), callback_data="show_course")
    ])

    await query.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(kb)
    )


# Handle mini-test
async def take_test(update, context):
    query = update.callback_query; await query.answer()
    lang = context.user_data.get('lang','ru')
    _, sid, action = query.data.split(':')
    sid = int(sid)
    test = COURSE['steps'][sid-1]['test']
    if action == 'start':
        kb = [[InlineKeyboardButton(opt, callback_data=f'test_step:{sid}:{i}')] for i,opt in enumerate(test['options'][lang],1)]
        return await query.message.reply_text(test['question'][lang], reply_markup=InlineKeyboardMarkup(kb))
    choice = int(action)
    correct = test['correct'][lang] + 1
    uid = str(query.from_user.id)
    if choice == correct:
        progress[uid]['step'] = sid+1
        save_progress()
        return await query.message.reply_text(t('correct', lang), reply_markup=build_main_menu(uid, lang))
    kb = [[InlineKeyboardButton(t('retry', lang), callback_data=f'test_step:{sid}:start')]]
    await query.message.reply_text(t('incorrect', lang), reply_markup=InlineKeyboardMarkup(kb))

# отправить вопрос финального теста
async def send_final_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    lang = context.user_data.get('lang', 'ru')
    state = user_final.get(uid)

    if not state:
        return await query.message.reply_text(t('unknown', lang))

    q_idx = state['q']
    questions = COURSE['final_test']['questions']
    total_q = len(questions)

    if q_idx >= total_q:
        return await final_test_result(update, context)

    question = questions[q_idx]
    question_text = question['question'][lang]
    opts = question['options'][lang]

    # Кнопки ответов
    kb = [
        [InlineKeyboardButton(opt, callback_data=f"test_final:{q_idx}:{i}")]
        for i, opt in enumerate(opts)
    ]

    # Кнопки управления
    kb.append([
        InlineKeyboardButton(t('cancel_test', lang), callback_data='cancel_final')
    ])

    await query.message.reply_text(
        text=f"❓ {t('test_progress', lang).format(current=q_idx+1, total=total_q)}\n\n{question_text}",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# обработать ответ на финальный тест
async def handle_final_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    lang = context.user_data.get('lang', 'ru')
    data = query.data

    # Разобрать данные
    _, q_idx_str, choice_str = data.split(':')
    q_idx = int(q_idx_str)
    choice = int(choice_str)

    questions = COURSE['final_test']['questions']
    correct_idx = questions[q_idx]['correct'][lang]
    total_q = len(questions)

        # Показываем ответ
    if choice == correct_idx:
        user_final[uid]['score'] += 1
        user_final[uid]['q'] += 1  # <-- только если правильный ответ
        
        await query.message.reply_text(t('final_correct', lang))

        if user_final[uid]['q'] < total_q:
            return await send_final_question(update, context)
        else:
            return await final_test_result(update, context)

    else:
        # Неправильный ответ — остаёмся на этом же вопросе
        await query.message.reply_text(t('incorrect', lang))
        return await send_final_question(update, context)  # повтор того же вопроса


# показать результат финального теста
async def final_test_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    lang = context.user_data.get('lang', 'ru')

    score = user_final[uid]['score']
    total_q = len(COURSE['final_test']['questions'])

    user_final.pop(uid, None)  # очищаем состояние пользователя

    if score == total_q:
        # Прошёл тест успешно
        user_final_passed[uid] = True

        # Кнопки: Бонусы + Сертификат + Поддержка
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t('menu_bonus', lang), callback_data="menu_bonus")],
            [InlineKeyboardButton(t('menu_certificate', lang), callback_data="menu_certificate")],
            [InlineKeyboardButton(t('menu_support', lang), url=COURSE['texts']['support_link'])],
            [InlineKeyboardButton(t('back_main', lang), callback_data='back_main')]
        ])

        final_message = COURSE['texts']['final_message'][lang]

        await query.message.reply_text(
            text=final_message,
            reply_markup=kb,
            parse_mode='HTML'
        )

    else:
        # Провалил тест — предлагаем повторить
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t('menu_final', lang), callback_data="menu_final")
            ],
            [
                InlineKeyboardButton(t('back_main', lang), callback_data="back_main")
            ]
        ])

        await query.message.reply_text(
            text=f"😔 {t('final_failed', lang)}\n\n{t('test_progress', lang).format(current=score, total=total_q)}",
            reply_markup=kb
        ) 
    
async def back_to_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки Назад к списку шагов."""
    query = update.callback_query
    await query.answer()

    lang = context.user_data.get('lang', 'ru')
    uid = str(query.from_user.id)
    current = progress.get(uid, {}).get('step', 1)

    # Сформировать список шагов заново
    kb = []
    for idx, step in enumerate(COURSE['steps'], 1):
        title = step['title'][lang]
        if idx < current:
            label, cb = f"✓ {idx}. {title}", f'select_step:{idx}'
        elif idx == current:
            label, cb = f"▶ {idx}. {title}", f'select_step:{idx}'
        else:
            label, cb = f"🔒 {idx}. {title}", 'locked'
        kb.append([InlineKeyboardButton(label, callback_data=cb)])
    kb.append([InlineKeyboardButton(t('back_main', lang), callback_data='back_main')])

    await query.message.reply_text(
        text=t('course_list', lang),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def locked_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = context.user_data.get('lang', 'ru')

    await query.answer(
        text=COURSE['texts']['locked_step'][lang],
        show_alert=True
    )
    return  # <<< ОБЯЗАТЕЛЬНО!
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.message.from_user.id)
    lang = get_user_language(uid)
    await update.message.reply_text(
        t('help_brief', lang),
        reply_markup=build_main_menu(int(uid), lang)
    )
    
# --- Универсальный хендлер ошибок приложения ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=context.error)
    
# === Основной запуск ===
def main():
    # 1) Загрузка кэша и старт фонового автосэйва
    global progress
    progress = load_progress()
    threading.Thread(target=auto_save_loop, daemon=True).start()
    
    # 2) Создаём и конфигурируем бот
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("finaltest", finaltest_command))
    app.add_handler(CallbackQueryHandler(locked_step, pattern="^locked$"))
    app.add_handler(CallbackQueryHandler(button_handler), group=0)
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, question_handler), group=1)
    app.add_error_handler(error_handler)


    # 3) Запуск polling()
    logger.info("Starting bot polling...")
    app.run_polling()

if __name__ == "__main__":
    main()