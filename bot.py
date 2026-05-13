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

# --- 1. CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')  
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = os.getenv('RAZORPAY_WEBHOOK_SECRET')

# Database & Bot Setup
bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']
transactions_col = db['transactions']

# Razorpay Client
rz_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except: pass

# --- 2. HELPERS ---
def format_clean_duration(minutes):
    mins = int(minutes)
    if mins < 60: return f"{mins} MIN"
    elif mins < 1440:
        hours = mins // 60
        return f"{hours} HOUR" if hours == 1 else f"{hours} HOURS"
    else:
        days = mins // 1440
        return f"{days} DAY" if days == 1 else f"{days} DAYS"

# --- 3. FLASK & RAZORPAY WEBHOOK (AUTO APPROVE) ---
app = Flask('')

@app.route('/')
def home(): return "Bot is Alive!"

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
                process_approval(tx['user_id'], tx['channel_id'], tx['minutes'])
                transactions_col.update_one({"order_id": order_id}, {"$set": {"status": "success"}})
    except: pass
    return jsonify({"status": "ok"}), 200

def process_approval(u_id, ch_id, mins):
    expiry_ts = int((datetime.now(timezone.utc) + timedelta(minutes=int(mins))).timestamp())
    link = bot.create_chat_invite_link(int(ch_id), member_limit=1, expire_date=expiry_ts)
    users_col.update_one({"user_id": int(u_id), "channel_id": int(ch_id)}, {"$set": {"expiry": expiry_ts}}, upsert=True)
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 JOIN CHANNEL NOW", url=link.invite_link))
    bot.send_message(u_id, "🥳 <b>PAYMENT VERIFIED!</b>\nYour access is now active.", reply_markup=markup, parse_mode="HTML")

# --- 4. ADMIN: ADD STORY WITH CARD ---
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
        markup.add(InlineKeyboardButton("➕ ADD NEW STORY", callback_data="add_new"))
        bot.send_message(message.chat.id, "🛠 <b>ADMIN CONTROL PANEL</b>", reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "Welcome! Please use a valid story link.")

@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def add_new_step1(call):
    msg = bot.send_message(ADMIN_ID, "📤 <b>STEP 1:</b> Forward any message from the story channel.")
    bot.register_next_step_handler(msg, add_new_step2)

def add_new_step2(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        instr = (
            f"✅ <b>Channel:</b> {ch_name}\n\n"
            "📝 <b>STEP 2:</b> Send details in this format:\n"
            "<code>PhotoURL | Episodes | Price | Minutes | Description</code>"
        )
        msg = bot.send_message(ADMIN_ID, instr, parse_mode="HTML")
        bot.register_next_step_handler(msg, add_new_step3, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Please forward from a channel.")

def add_new_step3(message, ch_id, ch_name):
    try:
        p = message.text.split('|')
        data = {"photo":p[0].strip(),"eps":p[1].strip(),"price":p[2].strip(),"mins":p[3].strip(),"desc":p[4].strip(),"name":ch_name}
        msg = bot.send_message(ADMIN_ID, "🔗 <b>STEP 3:</b> Send Demo Link (or 'none'):")
        bot.register_next_step_handler(msg, add_new_final, ch_id, data)
    except: bot.send_message(ADMIN_ID, "❌ Format Error. Try again.")

def add_new_final(message, ch_id, data):
    if message.text.lower() != 'none': data["demo"] = message.text
    data["admin_id"] = ADMIN_ID
    channels_col.update_one({"channel_id": ch_id}, {"$set": data}, upsert=True)
    bot.send_message(ADMIN_ID, f"✅ <b>Story Added!</b>\nLink: <code>t.me/{bot.get_me().username}?start={ch_id}</code>", parse_mode="HTML")

# --- 5. USER: STORY CARD & PAYMENT ---
def show_plans_menu(chat_id, ch_id):
    ch = channels_col.find_one({"channel_id": ch_id})
    if ch:
        caption = (
            f"🎬 <b>STORY: {ch['name']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔢 <b>Episodes:</b> {ch['eps']}\n"
            f"⏳ <b>Validity:</b> {format_clean_duration(ch['mins'])}\n"
            f"💰 <b>Price:</b> ₹{ch['price']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 <b>Description:</b>\n{ch['desc']}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(f"💳 BUY NOW - ₹{ch['price']}", callback_data=f"pay_{ch_id}_{ch['mins']}"))
        if 'demo' in ch: markup.add(InlineKeyboardButton("📺 WATCH DEMO", url=ch['demo']))
        
        try: bot.send_photo(chat_id, ch['photo'], caption=caption, reply_markup=markup, parse_mode="HTML")
        except: bot.send_message(chat_id, caption, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_'))
def payment_options(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⚡ UPI QR (MANUAL)", callback_data=f"qr_{ch_id}_{mins}"))
    if rz_client:
        markup.add(InlineKeyboardButton("💳 ONLINE (RAZORPAY)", callback_data=f"rz_{ch_id}_{mins}"))
    bot.edit_message_caption("Choose Payment Method:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('qr_'))
def send_manual_qr(call):
    _, ch_id, mins = call.data.split('_')
    ch = channels_col.find_one({"channel_id": int(ch_id)})
    upi_url = f"upi://pay?pa={UPI_ID}&pn=Store&am={ch['price']}&cu=INR"
    qr_api = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(upi_url)}"
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ I HAVE PAID", callback_data=f"proof_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr_api, caption=f"💰 Pay ₹{ch['price']} to <code>{UPI_ID}</code>\nSend screenshot after payment.", reply_markup=markup, parse_mode="HTML")

# --- 6. MANUAL APPROVE/REJECT LOGIC ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('proof_'))
def ask_screenshot(call):
    _, ch_id, mins = call.data.split('_')
    msg = bot.send_message(call.message.chat.id, "📸 Please upload your Payment Screenshot now.")
    bot.register_next_step_handler(msg, handle_screenshot, int(ch_id), int(mins))

def handle_screenshot(message, ch_id, mins):
    if not message.photo: return bot.send_message(message.chat.id, "❌ Error: Please send a photo.")
    
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ APPROVE", callback_data=f"adm_app_{message.from_user.id}_{ch_id}_{mins}"),
        InlineKeyboardButton("❌ REJECT", callback_data=f"adm_rej_{message.from_user.id}")
    )
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"New Payment Proof\nUser: {message.from_user.id}", reply_markup=markup)
    bot.send_message(message.chat.id, "🕒 Proof sent! Wait for Admin to verify.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_action(call):
    data = call.data.split('_')
    u_id = int(data[2])
    
    if data[1] == "app":
        process_approval(u_id, data[3], data[4])
        bot.edit_message_caption(f"✅ Approved User {u_id}", call.message.chat.id, call.message.message_id)
    else:
        bot.send_message(u_id, "❌ <b>PAYMENT REJECTED!</b>\nYour proof was invalid. Contact Admin.")
        bot.edit_message_caption(f"❌ Rejected User {u_id}", call.message.chat.id, call.message.message_id)

# --- 7. AUTO KICK (EXPIRE) SCHEDULER ---
def kick_expired_users():
    now = datetime.now(timezone.utc).timestamp()
    expired = users_col.find({"expiry": {"$lte": now}})
    for u in expired:
        try:
            bot.ban_chat_member(u['channel_id'], u['user_id'])
            bot.unban_chat_member(u['channel_id'], u['user_id']) # Unban so they can rejoin later
            users_col.delete_one({"_id": u['_id']})
            bot.send_message(u['user_id'], "⚠️ <b>Subscription Expired!</b>\nYour access has been removed. Renew to continue.")
        except: pass

# --- 8. RUNTIME ---
if __name__ == '__main__':
    # Start Flask
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))).start()
    
    # Start Scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    
    print("Bot Started Successfully...")
    bot.infinity_polling()
