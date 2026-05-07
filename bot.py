import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, request, jsonify
from threading import Thread
import razorpay
import uuid

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
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

# --- HELPERS (UI TEXT STYLE) ---
def format_clean_duration(minutes):
    mins = int(minutes)
    if mins < 60: return f"{mins} ᴍɪɴ"
    if mins < 1440: return f"{mins // 60} ʜᴏᴜʀ"
    return f"{mins // 1440} ᴅᴀʏ"

def process_approval(u_id, ch_id, mins, method, tx_id="ɴ/ᴀ"):
    try:
        expiry_time = datetime.now(timezone.utc) + timedelta(minutes=mins)
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int(expiry_time.timestamp()))
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_time.timestamp()}}, upsert=True)
        
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ɴᴏᴡ", url=link.invite_link))
        bot.send_message(
            u_id, 
            f"🥳 <b>ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғɪᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ</b>\n\n"
            f"ᴛx ɪᴅ: <code>{tx_id}</code>\n"
            f"ᴍᴇᴛʜᴏᴅ: {method}\n"
            f"ᴅᴜʀᴀᴛɪᴏɴ: {format_clean_duration(mins)}\n\n"
            f"ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴊᴏɪɴ!", 
            reply_markup=markup, 
            parse_mode="HTML"
        )
    except Exception as e: print(f"Error: {e}")

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    text = message.text.split()
    if len(text) > 1:
        show_plans_menu(message.chat.id, int(text[1]))
        return
    if message.from_user.id == ADMIN_ID:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("➕ ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ", callback_data="admin_add"))
        markup.add(InlineKeyboardButton("📊 ᴍᴀɴᴀɢᴇ ᴄʜᴀɴɴᴇʟs", callback_data="admin_list"))
        bot.send_message(message.chat.id, "👋 <b>ᴡᴇʟᴄᴏᴍᴇ ᴀᴅᴍɪɴ</b>", reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "👋 <b>ʜᴇʟʟᴏ!</b>\nᴘʟᴇᴀsᴇ ᴜsᴇ ᴀ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ ᴛᴏ ᴠɪᴇᴡ ᴘʟᴀɴs.", parse_mode="HTML")

@bot.message_handler(commands=['myplan'])
def my_plan_handler(message):
    user_data = users_col.find_one({"user_id": message.from_user.id})
    if user_data:
        ch_data = channels_col.find_one({"channel_id": user_data['channel_id']})
        expiry_date = datetime.fromtimestamp(user_data['expiry'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        bot.send_message(message.chat.id, f"📋 <b>ʏᴏᴜʀ ᴀᴄᴛɪᴠᴇ ᴘʟᴀɴ</b>\n\nᴄʜᴀɴɴᴇʟ: <code>{ch_data['name'] if ch_data else 'Unknown'}</code>\nᴇxᴘɪʀᴇs: <code>{expiry_date} ᴜᴛᴄ</code>", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "❌ <b>ɴᴏ ᴀᴄᴛɪᴠᴇ ᴘʟᴀɴ ғᴏᴜɴᴅ</b>", parse_mode="HTML")

@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    bot.clear_step_handlers_by_chat_id(message.chat.id)
    bot.send_message(message.chat.id, "❌ <b>ᴘʀᴏᴄᴇss ᴄᴀɴᴄᴇʟʟᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ</b>", parse_mode="HTML")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def channels_cmd(message):
    channels = list(channels_col.find({"admin_id": ADMIN_ID}))
    if not channels:
        bot.send_message(message.chat.id, "❌ ɴᴏ ᴄʜᴀɴɴᴇʟs ᴀᴅᴅᴇᴅ.")
        return
    markup = InlineKeyboardMarkup()
    for ch in channels:
        markup.add(InlineKeyboardButton(f"📁 {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
    bot.send_message(message.chat.id, "📊 <b>ᴀᴅᴅᴇᴅ ᴄʜᴀɴɴᴇʟs:</b>", reply_markup=markup, parse_mode="HTML")

# --- USER PURCHASE FLOW ---
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
    price = int(channels_col.find_one({"channel_id": int(ch_id)})['plans'][mins])
    markup = InlineKeyboardMarkup()
    
    if rz_client:
        try:
            order = rz_client.order.create({"amount": price * 100, "currency": "INR", "payment_capture": 1})
            pay_url = f"https://api.razorpay.com/v1/checkout/hosted?key_id={RAZORPAY_KEY_ID}&order_id={order['id']}"
            transactions_col.insert_one({"order_id": order['id'], "user_id": call.from_user.id, "channel_id": int(ch_id), "minutes": int(mins), "status": "pending"})
            markup.add(InlineKeyboardButton("⚡ ᴀᴜᴛᴏᴍᴀᴛɪᴄ ᴘᴀʏ", url=pay_url))
        except:
            pass
    
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
    msg = bot.send_message(call.message.chat.id, "📷 <b>sᴜʙᴍɪᴛ sᴄʀᴇᴇɴsʜᴏᴛ ᴘʀᴏᴏғ</b>\n💡 ᴛʏᴘᴇ /cancel ᴛᴏ sᴛᴏᴘ.", parse_mode="HTML")
    bot.register_next_step_handler(msg, receive_screenshot, int(ch_id), int(mins))

def receive_screenshot(message, ch_id, mins):
    if message.text and message.text.lower() == '/cancel':
        cancel_command(message)
        return
    if not message.photo:
        msg = bot.send_message(message.chat.id, "⚠️ sᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ ᴏʀ /cancel.")
        bot.register_next_step_handler(msg, receive_screenshot, ch_id, mins)
        return
    
    tx_id = str(uuid.uuid4())[:8].upper()
    username = f"@{message.from_user.username}" if message.from_user.username else "ɴᴏ ᴜsᴇʀɴᴀᴍᴇ"
    admin_caption = f"📩 <u><b>ɴᴇᴡ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ</b></u>\n\n👤 ᴜsᴇʀ: <u>{username}</u>\n🆔 ɪᴅ: <code>{tx_id}</code>\n⏳ ᴘʟᴀɴ: {format_clean_duration(mins)}"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ ᴀᴘᴘʀᴏᴠᴇ", callback_data=f"ap_{message.from_user.id}_{ch_id}_{mins}_{tx_id}"))
    markup.add(InlineKeyboardButton("❌ ʀᴇᴊᴇᴄᴛ", callback_data=f"rj_{message.from_user.id}_{tx_id}"))
    
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=admin_caption, reply_markup=markup, parse_mode="HTML")
    bot.send_message(message.chat.id, f"✅ ᴘʀᴏᴏғ sᴇɴᴛ! ʏᴏᴜʀ ᴛᴇᴍᴘ ɪᴅ: <code>{tx_id}</code>", parse_mode="HTML")

# --- CALLBACKS & ACTIONS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('ap_', 'rj_', 'manage_', 'admin_')))
def actions(call):
    d = call.data.split('_')
    if d[0] == 'ap':
        process_approval(int(d[1]), int(d[2]), int(d[3]), "ᴍᴀɴᴜᴀʟ", d[4])
        bot.edit_message_caption(f"✅ ᴀᴘᴘʀᴏᴠᴇᴅ (ɪᴅ: {d[4]})", call.message.chat.id, call.message.message_id)
    elif d[0] == 'rj':
        bot.send_message(int(d[1]), f"❌ ᴘᴀʏᴍᴇɴᴛ ʀᴇᴊᴇᴄᴛᴇᴅ (ɪᴅ: {d[2]})")
        bot.edit_message_caption(f"❌ ʀᴇᴊᴇᴄᴛᴇᴅ (ɪᴅ: {d[2]})", call.message.chat.id, call.message.message_id)
    elif d[0] == 'admin' and d[1] == 'add':
        msg = bot.send_message(ADMIN_ID, "👉 ғᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ.")
        bot.register_next_step_handler(msg, get_plans_admin)
    elif d[0] == 'admin' and d[1] == 'list':
        channels_cmd(call.message)
    elif d[0] == 'manage':
        ch = channels_col.find_one({"channel_id": int(d[1])})
        bot.send_message(ADMIN_ID, f"🔗 ʟɪɴᴋ: <code>https://t.me/{bot.get_me().username}?start={ch['channel_id']}</code>", parse_mode="HTML")

def get_plans_admin(message):
    if message.forward_from_chat:
        ch_id, ch_name = message.forward_from_chat.id, message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"✅ ᴄʜᴀɴɴᴇʟ: {ch_name}\nᴇɴᴛᴇʀ ᴘʟᴀɴs (ᴍɪɴ:ᴘʀɪᴄᴇ, ᴍɪɴ:ᴘʀɪᴄᴇ)")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)

def finalize_channel(message, ch_id, ch_name):
    try:
        plans = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans, "admin_id": ADMIN_ID}}, upsert=True)
        bot.send_message(ADMIN_ID, "✅ sᴇᴛᴜᴘ ᴅᴏɴᴇ!", parse_mode="HTML")
    except: bot.send_message(ADMIN_ID, "❌ ᴇʀʀᴏʀ ɪɴ ғᴏʀᴍᴀᴛ.")

# --- WEBHOOK & KICKER ---
app = Flask('')
@app.route('/razorpay_webhook', methods=['POST'])
def rz_webhook():
    data = request.json
    if data.get("event") == "payment.captured":
        p_entity = data['payload']['payment']['entity']
        tx = transactions_col.find_one({"order_id": p_entity.get('order_id'), "status": "pending"})
        if tx:
            process_approval(tx['user_id'], tx['channel_id'], tx['minutes'], "ᴀᴜᴛᴏᴍᴀᴛɪᴄ", tx['order_id'])
            transactions_col.update_one({"order_id": tx['order_id']}, {"$set": {"status": "success"}})
    return jsonify({"status": "ok"}), 200

def kick_expired():
    now = datetime.now(timezone.utc).timestamp()
    for user in users_col.find({"expiry": {"$lte": now}}):
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            users_col.delete_one({"_id": user['_id']})
        except: pass

if __name__ == '__main__':
    Thread(target=lambda: app.run(host='0.0.0.0', port=5000)).start()
    sch = BackgroundScheduler()
    sch.add_job(kick_expired, 'interval', minutes=1)
    sch.start()
    bot.infinity_polling()
