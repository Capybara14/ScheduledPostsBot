import time
import threading
import os

import telebot
from telebot import types
import redis
import io
import imagehash
from PIL import Image
from dotenv import load_dotenv

# Очередь и база данных(bd) это одно и то же

DIFF_VALUE = 5  # Значение показывает насколько сильно должны совпадать картинки, чтобы называться дубликатами, диапазон значений 0-64, больше 10 это уже совершенно разные изображения со слов чатгпт
TIMEOUT = 600
ONE_PAGE_SIZE = 40  # 40 это максимум для телеги
TIMEZONE_OFFSET = 3  # Часовой пояс

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
BOT_ID = os.getenv("BOT_USERNAME")
REDIS_URL = os.getenv("REDIS_URL")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
r = redis.from_url(
    REDIS_URL,
    decode_responses=True
)

# Добавляет меню команд в углу экрана
while True:
    try:
        bot.set_my_commands([
            types.BotCommand("start", "Приветствие"),
            types.BotCommand("menu", "Меню")
        ])
        break
    except:
        time.sleep(TIMEOUT)


# Стартер, отправлятор и удалятор картинок по нажатию в базе данных
@bot.message_handler(commands=['start'])
def start_worker(message):
    parts = message.text.split()
    if len(parts) == 1:
        bot.reply_to(message, 'Привет! Это бот для автоматического управления отложкой в канале')
    else:
        bot.delete_message(message.chat.id, message.message_id)
        if parts[1][:4] == 'del_':
            indx = r.lindex("post_list", int(parts[1][4:]) - 1)
            try:
                bot.send_photo(message.chat.id, photo=indx[4:])
            except:
                bot.send_video(message.chat.id, video=indx[4:])
            r.lset("post_list", int(parts[1][4:]) - 1, "TO_DELETE")
            r.lrem("post_list", count=1, value='TO_DELETE')
            bot.send_message(message.from_user.id, f'✅ Элемент №{parts[1][4:]} успешно удалён.')
        else:
            indx = r.lindex("post_list", int(parts[1]) - 1)
            try:
                bot.send_photo(message.chat.id, photo=indx[4:])
            except:
                bot.send_video(message.chat.id, video=indx[4:])


# Раз в TIMEOUT проверяет прошло ли назначенное время с прошлого поста и если да, то запускает постинг
def posting_checker():
    while True:
        try:
            interval = int(r.get('interval'))
            last_post = r.get("last_post_time")
            schedule = r.get("schedule")
            if last_post is None:
                last_post = time.time()
                r.set("last_post_time", last_post)
            if schedule is None:
                schedule = "0822"
                r.set("schedule", schedule)
            if int(schedule[:2]) <= (time.gmtime().tm_hour + TIMEZONE_OFFSET) % 24 <= int(schedule[2:]) and (
                    time.time() - float(last_post)) > interval and r.llen('post_list') > 0:
                posting()
        except:
            print('Ошибка в posting_checker')
        time.sleep(TIMEOUT)


# Постит и обновляет таймер
def posting():
    channel_id = r.get("channel_id")
    if channel_id is None:
        bot.send_message(OWNER_ID, 'Канал не выбран')
        return
    pic_id = r.lpop("post_list")
    if pic_id[:3] == 'img':
        bot.send_photo(channel_id, photo=pic_id[4:])
    else:
        bot.send_video(channel_id, video=pic_id[4:])
    r.set("last_post_time", time.time())


# Записывает посты в очередь
@bot.message_handler(content_types=['photo', 'video'])
def media_worker(message):
    if not verificator(message):
        return
    if message.photo:
        file_id = message.photo[-1].file_id
        preview_size = message.photo[1] if len(message.photo) > 1 else message.photo[0]
        preview_file_id = preview_size.file_id
        downloaded_bytes = bot.download_file(bot.get_file(preview_file_id).file_path)
        img = Image.open(io.BytesIO(downloaded_bytes))
        file_hash = imagehash.phash(img)
        is_duplicate = False
        duplicate_file = 0
        for item in r.lrange("hash_list", 0, -1):
            diff = file_hash - imagehash.hex_to_hash(item)
            if diff <= DIFF_VALUE:
                print(f"Было найдено совпадение фото с {item}")
                duplicate_file = item
                is_duplicate = True
                break
        if is_duplicate:
            bot.reply_to(
                message,
                f"""Найдено совпадение файлов! 
• Новый: {str(file_hash)} 
• Старый: {duplicate_file}""",
                reply_markup=post_anyway(),
            )
        else:
            r.rpush("post_list", f"img:{file_id}")
            r.rpush("hash_list", str(file_hash))
            bot.reply_to(message, f"В очередь добавлен файл {file_id} ({str(file_hash)})")

    elif message.video:
        file_id = message.video.file_id
        if message.video.thumbnail:
            thumb_id = message.video.thumbnail.file_id
            downloaded_bytes = bot.download_file(bot.get_file(thumb_id).file_path)
            img = Image.open(io.BytesIO(downloaded_bytes))
            file_hash = imagehash.phash(img)

            if 4 <= file_hash.hash.sum() <= 60:
                is_duplicate = False
                for item in r.lrange("hash_list", 0, -1):
                    diff = file_hash - imagehash.hex_to_hash(item)
                    if diff <= DIFF_VALUE:
                        print(f"Было найдено совпадение видео с {item}")
                        is_duplicate = True
                        break
                if is_duplicate:
                    bot.reply_to(
                        message,
                        f"Этот файл уже постили! {file_id} ({str(file_hash)})",
                        reply_markup=post_anyway(),
                    )
                    return
                r.rpush("post_list", f"vid:{file_id}")
                r.rpush("hash_list", str(file_hash))
                bot.reply_to(message, f"В очередь добавлен файл {file_id} ({str(file_hash)})")
                return
        r.rpush("post_list", f"vid:{file_id}")
        bot.reply_to(message, f"В очередь добавлен файл {file_id}")


# # Отдаёт статистику
# @bot.message_handler(commands=['stats'])
# def stats_worker(message):
#     if not verificator(message):
#         return
#     last_post = float(r.get("last_post_time"))
#     bot.reply_to(message, f"""📊 Статистика:
# Постов в очереди: {r.llen("post_list")}
# Последний пост был:
#  — {time.ctime(last_post)}
# С последнего поста прошло:
#  — {round((time.time() - last_post) / 60, 2)} минут или {round((time.time() - last_post) / 3600, 2)} часов
# """)


# # Вход в настройки
# @bot.message_handler(commands=['settings'])
# def open_settings(message):
#     if not verificator(message):
#         return
#     bot.reply_to(
#         message,
#         "⚙️ **Выберете что хотите настроить: **",
#         reply_markup=settings_keyboard(),
#         parse_mode="Markdown"
#     )


# Вход в меню
@bot.message_handler(commands=['menu'])
def open_menu(message):
    if not verificator(message):
        return
    last_post = float(r.get("last_post_time"))
    bot.reply_to(
        message,
        f"""⚙️ *МЕНЮ*
• Постов в очереди: _{r.llen("post_list")}_
• Последний пост был: 
 — _{time.ctime(last_post)}_
• С последнего поста прошло: 
 — _{round((time.time() - last_post) / 60, 2)} минут_ или _{round((time.time() - last_post) / 3600, 2)} часов_""",
        reply_markup=settings_keyboard(),
        parse_mode="Markdown",
    )


# Клавиатура меню
def settings_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_admins = types.InlineKeyboardButton("👨‍🎓 Админы", callback_data="btn_admins")
    btn_interval = types.InlineKeyboardButton("⏳ Расписание", callback_data="btn_interval")
    btn_channel = types.InlineKeyboardButton("📢 Канал", callback_data="btn_channel")
    btn_all_bd = types.InlineKeyboardButton("📄 Очередь", callback_data="btn_all_bd")
    btn_send_now = types.InlineKeyboardButton("📤 Отправить сейчас", callback_data="btn_send_now")
    markup.add(btn_send_now)
    markup.add(btn_interval, btn_all_bd)
    markup.add(btn_admins, btn_channel)
    return markup


# Клавиатура админов
def admins_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_new_admin = types.InlineKeyboardButton("➕ Назначить нового админа", callback_data="btn_new_admin")
    btn_back = types.InlineKeyboardButton("⤴️ Назад", callback_data="btn_back")
    btn_remove_admins = types.InlineKeyboardButton("🗑 Удалить всех", callback_data="btn_remove_admins")
    markup.add(btn_new_admin, btn_remove_admins, btn_back)
    return markup


# Клавиатура интервала
def interval_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_change_interval = types.InlineKeyboardButton("⏱️ Изменить интервал", callback_data="btn_change_interval")
    btn_change_schedule = types.InlineKeyboardButton("📆 Изменить расписание", callback_data="btn_change_schedule")
    btn_back = types.InlineKeyboardButton("⤴️ Назад", callback_data="btn_back")
    markup.add(btn_change_interval, btn_change_schedule, btn_back)
    return markup


# Клавиатура канала
def channel_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_change_channel = types.InlineKeyboardButton("✏️ Изменить", callback_data="btn_change_channel")
    btn_back = types.InlineKeyboardButton("⤴️ Назад", callback_data="btn_back")
    markup.add(btn_change_channel, btn_back)
    return markup


# Клавиатура возврата
def back_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_back = types.InlineKeyboardButton("⤴️ Назад", callback_data="btn_back")
    markup.add(btn_back)
    return markup


# Клавиатура базы данных
def bd_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_bd_watch = types.InlineKeyboardButton("👁️ Посмотреть очередь", callback_data="btn_bd_watch")
    btn_bd_remove = types.InlineKeyboardButton("✂️ Удалить выбранное", callback_data="btn_bd_remove")
    btn_bd_remove_all = types.InlineKeyboardButton("🧹 Очистить всё", callback_data="btn_bd_remove_all")
    btn_back = types.InlineKeyboardButton("⤴️ Назад", callback_data="btn_back")
    markup.add(btn_bd_watch, btn_bd_remove, btn_bd_remove_all, btn_back)
    return markup


def post_anyway():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_post_anyway = types.InlineKeyboardButton("Добавить в очередь всё равно", callback_data="btn_post_anyway")
    markup.add(btn_post_anyway)
    return markup


# Настройки бота -- обработка
@bot.callback_query_handler(func=lambda call: True)
def button_worker(call):
    if not verificator(call):
        return
    bot.answer_callback_query(call.id)
    if call.data == "btn_admins":
        admins_set = r.smembers("admins")
        all_admins = [f'[{i}](tg://user?id={i})' for i in admins_set]
        bot.edit_message_text(f"Назначенные админы:\n{'\n'.join(all_admins)}", call.message.chat.id,
                              call.message.message_id, reply_markup=admins_keyboard(), parse_mode="Markdown")
    elif call.data == "btn_back":
        last_post = float(r.get("last_post_time"))
        bot.edit_message_text(
            f"""⚙️ *МЕНЮ*
• Постов в очереди: _{r.llen("post_list")}_
• Последний пост был: 
 — _{time.ctime(last_post)}_
• С последнего поста прошло: 
 — _{round((time.time() - last_post) / 60, 2)} минут_ или _{round((time.time() - last_post) / 3600, 2)} часов_""",
            call.message.chat.id, call.message.message_id,
            reply_markup=settings_keyboard(),
            parse_mode="Markdown",
        )
    elif call.data == 'btn_new_admin':
        if call.message.chat.id == OWNER_ID:
            msg = bot.edit_message_text("Введите id нового админа:", call.message.chat.id, call.message.message_id,
                                        parse_mode="Markdown")
            bot.register_next_step_handler(msg, save_admin)
        else:
            bot.send_message(call.message.chat.id, '❌ Доступ запрещён')
    elif call.data == 'btn_remove_admins':
        bot.edit_message_text(f'Все админы удалены', call.message.chat.id,
                              call.message.message_id, reply_markup=back_keyboard(), parse_mode="Markdown")
        r.delete("admins")
    elif call.data == 'btn_interval':
        schedule = r.get('schedule')
        bot.edit_message_text(f"""*Текущие настройки:*
 — Интервал: _{round(int(r.get('interval')) / 3600, 1)} часа_
 — Расписание: _с {schedule[:2]} до {schedule[2:]} часов_""",
                              call.message.chat.id,
                              call.message.message_id, reply_markup=interval_keyboard(),
                              parse_mode="Markdown")
    elif call.data == 'btn_change_interval':
        msg = bot.edit_message_text(f"Введите новый интервал (в часах):", call.message.chat.id, call.message.message_id,
                                    parse_mode="Markdown")
        bot.register_next_step_handler(msg, change_interval)
    elif call.data == 'btn_channel':
        bot.edit_message_text(f"Текущий канал: _{r.get('channel_id')}_", call.message.chat.id,
                              call.message.message_id, reply_markup=channel_keyboard(), parse_mode="Markdown")
    elif call.data == 'btn_change_channel':
        msg = bot.edit_message_text(f"Введите новый канал (@example):", call.message.chat.id, call.message.message_id,
                                    parse_mode="Markdown")
        bot.register_next_step_handler(msg, change_channel)
    elif call.data == 'btn_all_bd':
        if not r.exists('post_list'):
            bot.edit_message_text(f"❌ Очередь пуста", call.message.chat.id,
                                  call.message.message_id, reply_markup=back_keyboard(), parse_mode="Markdown")
        else:
            bot.edit_message_text('🛠️ Выберите действие:', call.message.chat.id, call.message.message_id,
                                  reply_markup=bd_keyboard(), parse_mode="Markdown")
    elif call.data == 'btn_bd_watch':
        full_bd, number = [], 0
        for i in r.lrange("post_list", 0, -1):
            number += 1
            full_bd.append(f"{number}. [{i}](https://t.me/{BOT_ID}?start={number})")
        big_parts_bd = []
        for i in range(0, len(full_bd), ONE_PAGE_SIZE):
            big_parts_bd.append('\n'.join(full_bd[i:i + ONE_PAGE_SIZE]))
        if len(big_parts_bd) == 1:
            bot.edit_message_text(f"*Текущая очередь:*\n{''.join(big_parts_bd)}", call.message.chat.id,
                                  call.message.message_id, reply_markup=back_keyboard(), parse_mode="Markdown")
        else:
            bot.edit_message_text(f"*Текущая очередь:*\n{''.join(big_parts_bd[0])}", call.message.chat.id,
                                  call.message.message_id, parse_mode="Markdown")
            if len(big_parts_bd) > 2:
                for i in big_parts_bd[1:-1]:
                    bot.send_message(call.message.chat.id, i, parse_mode="Markdown")
            if len(big_parts_bd) > 1:
                bot.send_message(call.message.chat.id, big_parts_bd[-1], reply_markup=back_keyboard(),
                                 parse_mode="Markdown")

    elif call.data == 'btn_bd_remove':
        full_bd, number = [], 0
        for i in r.lrange("post_list", 0, -1):
            number += 1
            full_bd.append(f"{number}. [{i}](https://t.me/{BOT_ID}?start=del_{number})")
        big_parts_bd = []
        for i in range(0, len(full_bd), ONE_PAGE_SIZE):
            big_parts_bd.append('\n'.join(full_bd[i:i + ONE_PAGE_SIZE]))
        bot.edit_message_text(f"*Выберите файл для удаления из учереди:*\n{''.join(big_parts_bd[0])}",
                              call.message.chat.id,
                              call.message.message_id, parse_mode="Markdown")
        if len(big_parts_bd) > 2:
            for i in big_parts_bd[1:-1]:
                bot.send_message(call.message.chat.id, i, parse_mode="Markdown")
        if len(big_parts_bd) > 1:
            bot.send_message(call.message.chat.id, big_parts_bd[-1], reply_markup=back_keyboard(),
                             parse_mode="Markdown")
    elif call.data == 'btn_bd_remove_all':
        msg = bot.edit_message_text(f"Если вы уверены, что хотите удалить ВСЁ, введите УДАЛИТЬ", call.message.chat.id,
                                    call.message.message_id, reply_markup=back_keyboard(),
                                    parse_mode="Markdown")
        bot.register_next_step_handler(msg, remove_confirm)

    elif call.data == 'btn_send_now':
        if r.exists("post_list"):
            posting()
            bot.edit_message_text("✅ Пост отправлен", call.message.chat.id,
                                  call.message.message_id, reply_markup=back_keyboard(), parse_mode="Markdown")
        else:
            bot.edit_message_text("❌ Очередь пуста, отправлять нечего", call.message.chat.id,
                                  call.message.message_id, reply_markup=back_keyboard(), parse_mode="Markdown")
    elif call.data == 'btn_change_schedule':
        msg = bot.edit_message_text(f"Введите новое расписание (в формате 08-22)", call.message.chat.id,
                                    call.message.message_id,
                                    parse_mode="Markdown")
        bot.register_next_step_handler(msg, change_schedule)
    elif call.data == 'btn_post_anyway':
        msg = call.message.reply_to_message
        if msg.photo:
            file_id = msg.photo[-1].file_id
            r.rpush("post_list", f"img:{file_id}")
        elif msg.video:
            file_id = msg.video.file_id
            r.rpush("post_list", f"vid:{file_id}")
        bot.edit_message_text(
            f"В очередь добавлен файл {file_id}",
            call.message.chat.id,
            call.message.message_id
        )


# Сохраняет нового админа
def save_admin(message):
    r.sadd("admins", message.text.strip())
    bot.send_message(message.chat.id,
                     f"✅ Админ [{message.text.strip()}](tg://user?id={message.text.strip()}) успешно зарегистрирован!",
                     reply_markup=back_keyboard(), parse_mode="Markdown")


# Меняет интервал
def change_interval(message):
    r.set("interval", int(float(message.text.strip()) * 3600))
    bot.send_message(message.chat.id, f"✅ Интервал успешно изменён на {message.text.strip()} часа",
                     reply_markup=back_keyboard(), parse_mode="Markdown")


# Меняет канал
def change_channel(message):
    r.set("channel_id", message.text.strip())
    bot.send_message(message.chat.id, f"✅ Канал успешно изменён на {message.text.strip()}",
                     reply_markup=back_keyboard(), parse_mode="Markdown")


# Подтверждение удаления полной очереди
def remove_confirm(message):
    if message.text.strip() == 'УДАЛИТЬ':
        r.delete("post_list")
        bot.send_message(message.chat.id, "✅ Очередь успешно очищена", reply_markup=back_keyboard(),
                         parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ Неверный ввод", reply_markup=back_keyboard(),
                         parse_mode="Markdown")


# Меняет расписание
def change_schedule(message):
    r.set("schedule", message.text.strip()[:2] + message.text.strip()[3:])
    bot.send_message(message.chat.id, f"✅ Расписание успешно изменено на {message.text.strip()}",
                     reply_markup=back_keyboard(), parse_mode="Markdown")


# Проверяет, пользуется ботом разрешенный пользователь или нет, если нет выдаёт айдишник пользователя
def verificator(event):
    if not r.exists("admins"):
        r.sadd("admins", OWNER_ID)
    if not r.sismember("admins", event.from_user.id):
        bot.send_message(event.from_user.id, "Ваш id: " + str(event.from_user.id))
        return False
    return True


t = threading.Thread(target=posting_checker, daemon=True)
t.start()
bot.infinity_polling()
