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
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = os.getenv('RAZORPAY_WEBHOOK_SECRET')

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

# --- HELPERS ---
def format_clean_duration(minutes):
    mins = int(minutes)
    if mins < 60: return f"{mins} MIN"
    if mins < 1440: return f"{mins // 60} HOUR"
    return f"{mins // 1440} DAY"

# --- WEBHOOK & FLASK ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Healthy"

@app.route('/razorpay_webhook', methods=['POST'])
def razorpay_webhook():
    if not rz_client: return jsonify({"status": "error"}), 400
    try:
        data = request.json
        if data.get("event") == "payment.captured":
            p_entity = data['payload']['payment']['entity']
            tx = transactions_col.find_one({"order_id": p_entity.get('order_id'), "status": "pending"})
            if tx:
                process_approval(tx['user_id'], tx['channel_id'], tx['minutes'], "RAZORPAY AUTO")
                transactions_col.update_one({"order_id": tx['order_id']}, {"$set": {"status": "success"}})
        return jsonify({"status": "ok"}), 200
    except: return jsonify({"status": "failed"}), 400

def process_approval(u_id, ch_id, mins, method):
    expiry = datetime.now(timezone.utc) + timedelta(minutes=mins)
    link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int(expiry.timestamp()))
    users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry.timestamp()}}, upsert=True)
    bot.send_message(u_id, f"🥳 <b>VERIFIED BY {method}!</b>\n\nLINK: {link.invite_link}", parse_mode="HTML")

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    text = message.text.split()
    if len(text) > 1:
        show_plans_menu(message.chat.id, int(text[1]))
    elif message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 <b>ADMIN PANEL</b>\n/add - Setup Channel\n/channels - Manage All", parse_mode="HTML")

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_start(message):
    msg = bot.send_message(ADMIN_ID, "👉 Forward any message from your channel here.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id, ch_name = message.forward_from_chat.id, message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"Channel: {ch_name}\nEnter Plans (Example: 1440:99, 43200:299)")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else: bot.send_message(ADMIN_ID, "❌ Error: Forward a message from channel.")

def finalize_channel(message, ch_id, ch_name):
    try:
        plans = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans, "admin_id": ADMIN_ID}}, upsert=True)
        bot.send_message(ADMIN_ID, f"✅ Done! Link: `https://t.me/{bot.get_me().username}?start={ch_id}`", parse_mode="HTML")
    except: bot.send_message(ADMIN_ID, "❌ Format error. Use Min:Price.")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_ch(message):
    markup = InlineKeyboardMarkup()
    for ch in channels_col.find({"admin_id": ADMIN_ID}):
        markup.add(InlineKeyboardButton(ch['name'], callback_data=f"manage_{ch['channel_id']}"))
    bot.send_message(ADMIN_ID, "Channels:", reply_markup=markup)

# --- USER FLOW & CANCEL ---
def show_plans_menu(chat_id, ch_id):
    ch_data = channels_col.find_one({"channel_id": ch_id})
    if not ch_data: return
    markup = InlineKeyboardMarkup()
    for t, p in ch_data['plans'].items():
        markup.add(InlineKeyboardButton(f"💳 {format_clean_duration(t)} - ₹{p}", callback_data=f"select_{ch_id}_{t}"))
    bot.send_message(chat_id, f"Plans for <b>{ch_data['name']}</b>:", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def pay_mode(call):
    _, ch_id, mins = call.data.split('_')
    price = int(channels_col.find_one({"channel_id": int(ch_id)})['plans'][mins])
    markup = InlineKeyboardMarkup()
    if rz_client:
        order = rz_client.order.create({"amount": price * 100, "currency": "INR", "payment_capture": 1})
        transactions_col.insert_one({"order_id": order['id'], "user_id": call.from_user.id, "channel_id": int(ch_id), "minutes": int(mins), "status": "pending"})
        url = f"https://api.razorpay.com/v1/checkout/hosted?key_id={RAZORPAY_KEY_ID}&order_id={order['id']}"
        markup.add(InlineKeyboardButton("⚡ AUTOMATIC PAY", url=url))
    markup.add(InlineKeyboardButton("✏️ MANUAL PAY", callback_data=f"manual_{ch_id}_{mins}"))
    bot.send_message(call.message.chat.id, "Select Method:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_'))
def manual_qr(call):
    _, ch_id, mins = call.data.split('_')
    price = channels_col.find_one({"channel_id": int(ch_id)})['plans'][mins]
    qr = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ I HAVE PAID", callback_data=f"paid_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr, caption=f"Pay ₹{price} to `{UPI_ID}`", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def proof_msg(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ CANCEL PROCESS", callback_data=f"cancel_p_{ch_id}"))
    msg = bot.send_message(call.message.chat.id, "📷 Send Screenshot now or click Cancel:", reply_markup=markup)
    bot.register_next_step_handler(msg, receive_screenshot, int(ch_id), int(mins))

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_p_'))
def cancel_notif(call):
    bot.answer_callback_query(call.id, "Cancelled")
    bot.clear_step_handlers_by_chat_id(call.message.chat.id)
    bot.edit_message_text("❌ <b>Action Cancelled Successfully!</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    show_plans_menu(call.message.chat.id, int(call.data.split('_')[2]))

def receive_screenshot(message, ch_id, mins):
    if message.text and message.text.lower() in ['cancel', '/cancel']:
        bot.clear_step_handlers_by_chat_id(message.chat.id)
        bot.send_message(message.chat.id, "❌ Cancelled.")
        show_plans_menu(message.chat.id, ch_id)
        return
    if not message.photo:
        msg = bot.send_message(message.chat.id, "⚠️ Send a Photo or 'cancel'.")
        bot.register_next_step_handler(msg, receive_screenshot, ch_id, mins)
        return
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ APPROVE", callback_data=f"ap_{message.from_user.id}_{ch_id}_{mins}"), InlineKeyboardButton("❌ REJECT", callback_data=f"rj_{message.from_user.id}"))
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"Proof from {message.from_user.id}", reply_markup=markup)
    bot.send_message(message.chat.id, "✅ Sent to Admin for approval.")

# --- CALLBACKS & KICKER ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('ap_', 'rj_', 'manage_')))
def actions(call):
    d = call.data.split('_')
    if d[0] == 'ap': 
        process_approval(int(d[1]), int(d[2]), int(d[3]), "MANUAL")
        bot.edit_message_caption("✅ Approved", call.message.chat.id, call.message.message_id)
    elif d[0] == 'rj':
        bot.send_message(int(d[1]), "❌ Payment Rejected.")
        bot.edit_message_caption("❌ Rejected", call.message.chat.id, call.message.message_id)
    elif d[0] == 'manage':
        ch = channels_col.find_one({"channel_id": int(d[1])})
        bot.send_message(ADMIN_ID, f"Name: {ch['name']}\nLink: `https://t.me/{bot.get_me().username}?start={ch['channel_id']}`", parse_mode="HTML")

def kick_expired():
    now = datetime.now(timezone.utc).timestamp()
    for user in users_col.find({"expiry": {"$lte": now}}):
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            users_col.delete_one({"_id": user['_id']})
        except: pass

# --- STARTUP (SAFE ORDER) ---
if __name__ == '__main__':
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))).start()
    sch = BackgroundScheduler()
    sch.add_job(kick_expired, 'interval', minutes=1)
    sch.start()
    print("Bot is Running...")
    bot.infinity_polling()
