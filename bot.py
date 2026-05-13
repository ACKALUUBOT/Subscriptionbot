import os
import telebot
import urllib.parse
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

# Clients Setup
bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']
transactions_col = db['transactions']

# --- RAZORPAY INITIALIZATION ---
rz_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except: pass

# --- HELPERS ---
def format_clean_duration(minutes):
    mins = int(minutes)
    if mins < 60: return f"{mins} MIN"
    elif mins < 1440:
        hours = mins // 60
        return f"{hours} HOUR" if hours == 1 else f"{hours} HOURS"
    else:
        days = mins // 1440
        return f"{days} DAY" if days == 1 else f"{days} DAYS"

# --- FLASK WEBHOOK ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"

@app.route('/razorpay_webhook', methods=['POST'])
def razorpay_webhook():
    if not rz_client: return jsonify({"status": "no_rz"}), 400
    try:
        payload = request.data
        signature = request.headers.get('X-Razorpay-Signature')
        rz_client.utility.verify_webhook_signature(payload.decode('utf-8'), signature, RAZORPAY_WEBHOOK_SECRET)
        data = request.json
        if data.get("event") == "payment.captured":
            payment_entity = data['payload']['payment']['entity']
            order_id = payment_entity.get('order_id')
            tx = transactions_col.find_one({"order_id": order_id, "status": "pending"})
            if tx:
                expiry_ts = int((datetime.now(timezone.utc) + timedelta(minutes=tx['minutes'])).timestamp())
                link = bot.create_chat_invite_link(tx['channel_id'], member_limit=1, expire_date=expiry_ts)
                users_col.update_one({"user_id": tx['user_id'], "channel_id": tx['channel_id']}, {"$set": {"expiry": expiry_ts}}, upsert=True)
                transactions_col.update_one({"order_id": order_id}, {"$set": {"status": "success"}})
                markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 JOIN NOW", url=link.invite_link))
                bot.send_message(tx['user_id'], "🥳 <b>PAYMENT VERIFIED!</b>", reply_markup=markup, parse_mode="HTML")
    except: pass
    return jsonify({"status": "ok"}), 200

# --- ADMIN PANEL & CHANNEL MANAGEMENT ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    text = message.text.split()
    if len(text) > 1:
        try:
            show_plans_menu(message.chat.id, int(text[1]))
            return
        except: pass
    
    if message.from_user.id == ADMIN_ID:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📺 MANAGE CHANNELS", callback_data="manage_all"))
        markup.add(InlineKeyboardButton("➕ ADD NEW CHANNEL", callback_data="add_new"))
        bot.send_message(message.chat.id, "🛠 <b>ADMIN CONTROL PANEL</b>", reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "Welcome! Use the link provided by Admin.")

@bot.callback_query_handler(func=lambda call: call.data == "manage_all")
def list_channels_callback(call):
    markup = InlineKeyboardMarkup()
    channels = channels_col.find({"admin_id": ADMIN_ID})
    found = False
    for ch in channels:
        found = True
        markup.add(InlineKeyboardButton(f"📡 {ch['name']}", callback_data=f"edit_ch_{ch['channel_id']}"))
    
    markup.add(InlineKeyboardButton("➕ ADD NEW", callback_data="add_new"))
    markup.add(InlineKeyboardButton("🔙 BACK", callback_data="admin_home"))
    
    msg = "📋 <b>YOUR CONNECTED CHANNELS:</b>" if found else "❌ No channels added yet."
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "admin_home")
def admin_home(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📺 MANAGE CHANNELS", callback_data="manage_all"))
    markup.add(InlineKeyboardButton("➕ ADD NEW CHANNEL", callback_data="add_new"))
    bot.edit_message_text("🛠 <b>ADMIN CONTROL PANEL</b>", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_ch_"))
def edit_channel(call):
    ch_id = int(call.data.split('_')[2])
    ch = channels_col.find_one({"channel_id": ch_id})
    if ch:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🗑 DELETE CHANNEL", callback_data=f"del_ch_{ch_id}"))
        markup.add(InlineKeyboardButton("🔙 BACK", callback_data="manage_all"))
        
        info = f"<b>CHANNEL:</b> {ch['name']}\n<b>ID:</b> <code>{ch_id}</code>\n\n<b>Link:</b>\n<code>t.me/{bot.get_me().username}?start={ch_id}</code>"
        bot.edit_message_text(info, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_ch_"))
def delete_channel(call):
    ch_id = int(call.data.split('_')[2])
    channels_col.delete_one({"channel_id": ch_id})
    bot.answer_callback_query(call.id, "✅ Channel Deleted")
    list_channels_callback(call)

@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def add_new_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(ADMIN_ID, "📤 Forward any message from your channel here.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id, ch_name = message.forward_from_chat.id, message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"✅ Channel: {ch_name}\n\n📝 Enter Plans in this format:\n`Minutes:Price, Minutes:Price` \n(Example: `1440:100, 10080:500`)")
        bot.register_next_step_handler(msg, get_demo_input, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Please forward a message from a channel.")

def get_demo_input(message, ch_id, ch_name):
    try:
        plans = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
        msg = bot.send_message(ADMIN_ID, "🔗 Send Demo/Episodes Link (or type 'none'):")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name, plans)
    except:
        bot.send_message(ADMIN_ID, "❌ Invalid format. Use `Mins:Price, Mins:Price`.")

def finalize_channel(message, ch_id, ch_name, plans):
    data = {"name": ch_name, "plans": plans, "admin_id": ADMIN_ID}
    if message.text.lower() != 'none': data["demo_link"] = message.text
    channels_col.update_one({"channel_id": ch_id}, {"$set": data}, upsert=True)
    bot.send_message(ADMIN_ID, f"✅ <b>Setup Complete!</b>\n\nChannel Link:\n<code>t.me/{bot.get_me().username}?start={ch_id}</code>", parse_mode="HTML")

# --- USER SIDE FLOW ---
def show_plans_menu(chat_id, ch_id):
    ch = channels_col.find_one({"channel_id": ch_id})
    if ch:
        markup = InlineKeyboardMarkup()
        for t, p in ch['plans'].items():
            markup.add(InlineKeyboardButton(f"💳 {format_clean_duration(t)} - ₹{p}", callback_data=f"pay_{ch_id}_{t}"))
        if 'demo_link' in ch: markup.add(InlineKeyboardButton("📺 WATCH DEMO", url=ch['demo_link']))
        bot.send_message(chat_id, f"Welcome! Joining: <b>{ch['name']}</b>\nChoose a plan:", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_'))
def select_payment_method(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⚡ UPI QR (FAST)", callback_data=f"qr_{ch_id}_{mins}"))
    if rz_client:
        markup.add(InlineKeyboardButton("💳 ONLINE (RAZORPAY)", callback_data=f"rz_{ch_id}_{mins}"))
    bot.edit_message_text("Select Payment Method:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('qr_'))
def send_qr_code(call):
    _, ch_id, mins = call.data.split('_')
    ch = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch['plans'][mins]
    upi_url = f"upi://pay?pa={UPI_ID}&pn=Admin&am={price}&cu=INR"
    encoded_upi = urllib.parse.quote(upi_url)
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_upi}"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ PAID - SEND PROOF", callback_data=f"proof_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr_url, caption=f"💰 Pay: ₹{price}\nUPI: <code>{UPI_ID}</code>\n\nScan and send screenshot.", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('proof_'))
def ask_proof(call):
    _, ch_id, mins = call.data.split('_')
    msg = bot.send_message(call.message.chat.id, "Please upload the payment screenshot 📷")
    bot.register_next_step_handler(msg, receive_proof, int(ch_id), int(mins))

def receive_proof(message, ch_id, mins):
    if not message.photo: return bot.send_message(message.chat.id, "❌ Error: Send a photo screenshot.")
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ APPROVE", callback_data=f"app_{message.from_user.id}_{ch_id}_{mins}"),
        InlineKeyboardButton("❌ REJECT", callback_data=f"rej_{message.from_user.id}")
    )
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"New Proof from @{message.from_user.username}\nID: {message.from_user.id}", reply_markup=markup)
    bot.send_message(message.chat.id, "✅ Proof sent! Waiting for Admin approval.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_user(call):
    _, u_id, ch_id, mins = call.data.split('_')
    expiry_ts = int((datetime.now(timezone.utc) + timedelta(minutes=int(mins))).timestamp())
    link = bot.create_chat_invite_link(int(ch_id), member_limit=1, expire_date=expiry_ts)
    users_col.update_one({"user_id": int(u_id), "channel_id": int(ch_id)}, {"$set": {"expiry": expiry_ts}}, upsert=True)
    bot.send_message(int(u_id), "✅ <b>Payment Approved!</b>", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 JOIN CHANNEL", url=link.invite_link)), parse_mode="HTML")
    bot.edit_message_caption("✅ Approved ✅", call.message.chat.id, call.message.message_id)

# --- AUTO KICK SCHEDULER ---
def kick_expired():
    now = datetime.now(timezone.utc).timestamp()
    expired = users_col.find({"expiry": {"$lte": now}})
    for user in expired:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            users_col.delete_one({"_id": user['_id']})
        except: pass

# --- START BOT ---
if __name__ == '__main__':
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))).start()
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired, 'interval', minutes=1)
    scheduler.start()
    print("Bot is alive...")
    bot.infinity_polling()
