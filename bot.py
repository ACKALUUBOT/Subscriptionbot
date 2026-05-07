import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, request, jsonify
from threading import Thread
import razorpay

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME') or "Admin"
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']
transactions_col = db['transactions']

rz_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except: pass

# --- HELPERS (Small Caps Style) ---
def format_clean_duration(minutes):
    mins = int(minutes)
    if mins < 60: return f"{mins} ᴍɪɴ"
    if mins < 1440: return f"{mins // 60} ʜᴏᴜʀ"
    return f"{mins // 1440} ᴅᴀʏ"

# --- WEBHOOK & APPROVAL ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Online"

def process_approval(u_id, ch_id, mins, method):
    try:
        expiry_time = datetime.now(timezone.utc) + timedelta(minutes=mins)
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int(expiry_time.timestamp()))
        
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_time.timestamp()}}, upsert=True)
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚀 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ɴᴏᴡ", url=link.invite_link))
        
        bot.send_message(
            u_id, 
            f"🥳 <b>ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғɪᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ</b>\n\n"
            f"ᴍᴇᴛʜᴏᴅ: {method}\n"
            f"ᴅᴜʀᴀᴛɪᴏɴ: {format_clean_duration(mins)}\n\n"
            f"ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴊᴏɪɴ. ʟɪɴᴋ ᴡɪʟʟ ᴇxᴘɪʀᴇ ᴀғᴛᴇʀ ᴏɴᴇ ᴜsᴇ!", 
            reply_markup=markup, 
            parse_mode="HTML"
        )
    except Exception as e: print(f"Approval Error: {e}")

# --- START & MYPLAN COMMANDS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    text = message.text.split()
    if len(text) > 1:
        show_plans_menu(message.chat.id, int(text[1]))
        return

    if message.from_user.id == ADMIN_ID:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("➕ ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ", callback_data="admin_add"), InlineKeyboardButton("📊 ᴄʜᴀɴɴᴇʟs", callback_data="admin_list"))
        bot.send_message(message.chat.id, "👋 <b>ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ ᴀᴅᴍɪɴ</b>\nᴍᴀɴᴀɢᴇ ʏᴏᴜʀ sʏsᴛᴇᴍ ʙᴇʟᴏᴡ.", reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "👋 <b>ʜᴇʟʟᴏ!</b>\nᴘʟᴇᴀsᴇ ᴜsᴇ ᴀ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ ᴛᴏ ᴠɪᴇᴡ ᴘʟᴀɴs ᴏʀ ᴜsᴇ /myplan.", parse_mode="HTML")

@bot.message_handler(commands=['myplan'])
def my_plan_handler(message):
    user_data = users_col.find_one({"user_id": message.from_user.id})
    if user_data:
        ch_data = channels_col.find_one({"channel_id": user_data['channel_id']})
        expiry_date = datetime.fromtimestamp(user_data['expiry'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        bot.send_message(
            message.chat.id, 
            f"📋 <b>ʏᴏᴜʀ ᴀᴄᴛɪᴠᴇ ᴘʟᴀɴ</b>\n\n"
            f"ᴄʜᴀɴɴᴇʟ: <code>{ch_data['name'] if ch_data else 'Unknown'}</code>\n"
            f"ᴇxᴘɪʀᴇs ᴏɴ: <code>{expiry_date} ᴜᴛᴄ</code>\n\n"
            f"sᴛᴀᴛᴜs: ᴀᴄᴛɪᴠᴇ ✅", 
            parse_mode="HTML"
        )
    else:
        bot.send_message(message.chat.id, "❌ <b>ɴᴏ ᴀᴄᴛɪᴠᴇ ᴘʟᴀɴ ғᴏᴜɴᴅ</b>\nʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴀɴʏ ᴀᴄᴛɪᴠᴇ sᴜʙsᴄʀɪᴘᴛɪᴏɴs.", parse_mode="HTML")

# --- ADMIN FUNCTIONS ---
@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_cmd(message):
    msg = bot.send_message(ADMIN_ID, "👉 ғᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id, ch_name = message.forward_from_chat.id, message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"✅ ᴄʜᴀɴɴᴇʟ: {ch_name}\nᴇɴᴛᴇʀ ᴘʟᴀɴs (ᴍɪɴ:ᴘʀɪᴄᴇ, ᴍɪɴ:ᴘʀɪᴄᴇ)")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else: bot.send_message(ADMIN_ID, "❌ ᴇʀʀᴏʀ: ғᴏʀᴡᴀʀᴅ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ᴄʜᴀɴɴᴇʟ.")

def finalize_channel(message, ch_id, ch_name):
    try:
        plans = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans, "admin_id": ADMIN_ID}}, upsert=True)
        bot.send_message(ADMIN_ID, f"✅ sᴇᴛᴜᴘ ᴅᴏɴᴇ!\nʟɪɴᴋ: <code>https://t.me/{bot.get_me().username}?start={ch_id}</code>", parse_mode="HTML")
    except: bot.send_message(ADMIN_ID, "❌ ғᴏʀᴍᴀᴛ ᴇʀʀᴏʀ.")

# --- USER FLOW & CANCEL ---
def show_plans_menu(chat_id, ch_id):
    ch_data = channels_col.find_one({"channel_id": ch_id})
    if not ch_data: return
    markup = InlineKeyboardMarkup()
    for t, p in ch_data['plans'].items():
        markup.add(InlineKeyboardButton(f"💳 {format_clean_duration(t)} - ₹{p}", callback_data=f"select_{ch_id}_{t}"))
    bot.send_message(chat_id, f"⭐ <b>ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ {ch_data['name'].upper()}</b>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def pay_mode(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✏️ ᴍᴀɴᴜᴀʟ ᴘᴀʏ (ǫʀ)", callback_data=f"manual_{ch_id}_{mins}"))
    bot.send_message(call.message.chat.id, "✨ <b>ᴄʜᴏᴏsᴇ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅ</b>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_'))
def manual_qr(call):
    _, ch_id, mins = call.data.split('_')
    price = channels_col.find_one({"channel_id": int(ch_id)})['plans'][mins]
    qr = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ", callback_data=f"paid_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr, caption=f"💰 ᴀᴍᴏᴜɴᴛ: ₹{price}\n🏦 ᴜᴘɪ: <code>{UPI_ID}</code>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def proof_msg(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ ᴘʀᴏᴄᴇss", callback_data=f"cancel_p_{ch_id}"))
    msg = bot.send_message(call.message.chat.id, "📷 <b>sᴜʙᴍɪᴛ sᴄʀᴇᴇɴsʜᴏᴛ ᴘʀᴏᴏғ</b>", reply_markup=markup, parse_mode="HTML")
    bot.register_next_step_handler(msg, receive_screenshot, int(ch_id), int(mins))

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_p_'))
def cancel_handler(call):
    bot.answer_callback_query(call.id, "ᴄᴀɴᴄᴇʟʟᴇᴅ")
    bot.clear_step_handlers_by_chat_id(call.message.chat.id)
    bot.edit_message_text("❌ <b>ᴘʀᴏᴄᴇss ᴄᴀɴᴄᴇʟʟᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    show_plans_menu(call.message.chat.id, int(call.data.split('_')[2]))

def receive_screenshot(message, ch_id, mins):
    if message.text and message.text.lower() in ['cancel', '/cancel']:
        bot.clear_step_handlers_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ ᴄᴀɴᴄᴇʟʟᴇᴅ.")
        show_plans_menu(message.chat.id, ch_id)
        return
    if not message.photo:
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"cancel_p_{ch_id}"))
        msg = bot.send_message(message.chat.id, "⚠️ sᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ ᴏʀ ᴄʟɪᴄᴋ ᴄᴀɴᴄᴇʟ.", reply_markup=markup, parse_mode="HTML")
        bot.register_next_step_handler(msg, receive_screenshot, ch_id, mins)
        return
    
    username = f"@{message.from_user.username}" if message.from_user.username else "No Username"
    admin_caption = (
        f"📩 <u><b>ɴᴇᴡ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ ʀᴇᴄᴇɪᴠᴇᴅ</b></u>\n\n"
        f"👤 ᴜsᴇʀ: <u>{username}</u>\n"
        f"🆔 ᴜsᴇʀ ɪᴅ: <code>{message.from_user.id}</code>\n"
        f"⏳ ᴘʟᴀɴ: {format_clean_duration(mins)}"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ ᴀᴘᴘʀᴏᴠᴇ", callback_data=f"ap_{message.from_user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ ʀᴇᴊᴇᴄᴛ", callback_data=f"rj_{message.from_user.id}"))
    
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=admin_caption, reply_markup=markup, parse_mode="HTML")
    bot.send_message(message.chat.id, "✅ <b>ᴘʀᴏᴏғ sᴇɴᴛ!</b> ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ғᴏʀ ᴀᴅᴍɪɴ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ.")

# --- CALLBACK ACTIONS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('ap_', 'rj_', 'manage_', 'admin_')))
def actions(call):
    d = call.data.split('_')
    if d[0] == 'ap': 
        process_approval(int(d[1]), int(d[2]), int(d[3]), "ᴍᴀɴᴜᴀʟ")
        bot.edit_message_caption("✅ <u><b>ᴘᴀʏᴍᴇɴᴛ ᴀᴘᴘʀᴏᴠᴇᴅ</b></u>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    elif d[0] == 'rj':
        bot.send_message(int(d[1]), "❌ <b>ᴘᴀʏᴍᴇɴᴛ ʀᴇᴊᴇᴄᴛᴇᴅ</b>\nʏᴏᴜʀ ᴘʀᴏᴏғ ᴡᴀs ɴᴏᴛ ᴠᴇʀɪғɪᴇᴅ ʙʏ ᴀᴅᴍɪɴ.", parse_mode="HTML")
        bot.edit_message_caption("❌ <u><b>ᴘᴀʏᴍᴇɴᴛ ʀᴇᴊᴇᴄᴛᴇᴅ</b></u>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    elif d[0] == 'admin' and d[1] == 'add': add_cmd(call.message)
    elif d[0] == 'admin' and d[1] == 'list': list_ch_cmd(call.message)
    elif d[0] == 'manage':
        ch = channels_col.find_one({"channel_id": int(d[1])})
        bot.send_message(ADMIN_ID, f"🔗 ʟɪɴᴋ: <code>https://t.me/{bot.get_me().username}?start={ch['channel_id']}</code>", parse_mode="HTML")

def kick_expired():
    now = datetime.now(timezone.utc).timestamp()
    for user in users_col.find({"expiry": {"$lte": now}}):
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            users_col.delete_one({"_id": user['_id']})
        except: pass

if __name__ == '__main__':
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))).start()
    sch = BackgroundScheduler()
    sch.add_job(kick_expired, 'interval', minutes=1)
    sch.start()
    bot.infinity_polling()
