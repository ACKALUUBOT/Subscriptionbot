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
    if mins < 60: return f"{mins} Mɪɴs"
    elif mins < 1440:
        hours = mins // 60
        return f"{hours} Hᴏᴜʀ" if hours == 1 else f"{hours} Hᴏᴜʀs"
    else:
        days = mins // 1440
        return f"{days} Dᴀʏ" if days == 1 else f"{days} Dᴀʏs"

def calculate_discount(original, discount):
    try:
        orig, disc = float(original), float(discount)
        if orig <= disc: return 0
        return int(((orig - disc) / orig) * 100)
    except: return 0

# --- 3. FLASK & RAZORPAY WEBHOOK ---
app = Flask('')

@app.route('/')
def home(): return "Sʏsᴛᴇᴍ Is Oɴʟɪɴᴇ"

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
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 Jᴏɪɴ Cʜᴀɴɴᴇʟ Nᴏᴡ", url=link.invite_link))
    bot.send_message(u_id, "✅ <b>Pᴀʏᴍᴇɴᴛ Vᴇʀɪғɪᴇᴅ!</b>\n\nYᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss ʜᴀs ʙᴇᴇɴ ᴀᴄᴛɪᴠᴀᴛᴇᴅ.", reply_markup=markup, parse_mode="HTML")

# --- 4. ADMIN & USER START ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    text = message.text.split()
    if len(text) > 1:
        show_plans_menu(message.chat.id, int(text[1]))
        return
    
    if message.from_user.id == ADMIN_ID:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📂 Mᴀɴᴀɢᴇ Sᴛᴏʀɪᴇs", callback_data="manage_all"))
        markup.add(InlineKeyboardButton("➕ Aᴅᴅ Nᴇᴡ Sᴛᴏʀʏ", callback_data="add_new"))
        bot.send_message(message.chat.id, "💎 <b>Aᴅᴍɪɴ Cᴏɴᴛʀᴏʟ Pᴀɴᴇʟ</b>", reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "👋 <b>Wᴇʟᴄᴏᴍᴇ!</b>\n\nUsᴇ ᴀ sᴛᴏʀʏ ʟɪɴᴋ ᴛᴏ ʙʀᴏᴡsᴇ ᴏᴜʀ ᴄᴀᴛᴀʟᴏɢ.", parse_mode="HTML")

# --- 5. USER SIDE: STORY CARD ---
def show_plans_menu(chat_id, ch_id):
    ch = channels_col.find_one({"channel_id": ch_id})
    if ch:
        old_p, new_p = ch.get('old_price', '0'), ch.get('price', '0')
        off = calculate_discount(old_p, new_p)
        
        caption = (
            f"🎬 <b>{ch['name'].upper()}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>Pʀᴇᴍɪᴜᴍ Dᴇᴛᴀɪʟs:</b>\n"
            f"🔢 <b>Eᴘɪsᴏᴅᴇs:</b> {ch['eps']}\n"
            f"⏳ <b>Vᴀʟɪᴅɪᴛʏ:</b> {format_clean_duration(ch['mins'])}\n"
            f"💰 <b>Pʀɪᴄᴇ:</b> <s>₹{old_p}</s> <b>₹{new_p}</b> " + (f"({off}% Oғғ! 🔥)" if off > 0 else "") + "\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 <b>Dᴇsᴄʀɪᴘᴛɪᴏɴ:</b>\n{ch['desc']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <i>Iɴsᴛᴀɴᴛ Aᴄᴄᴇss | Hɪɢʜ Qᴜᴀʟɪᴛʏ</i>"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(f"💳 Uɴʟᴏᴄᴋ Sᴛᴏʀʏ - ₹{new_p}", callback_data=f"pay_{ch_id}_{ch['mins']}"))
        if 'demo' in ch: markup.add(InlineKeyboardButton("📺 Wᴀᴛᴄʜ Dᴇᴍᴏ", url=ch['demo']))
        
        try: bot.send_photo(chat_id, ch['photo'], caption=caption, reply_markup=markup, parse_mode="HTML")
        except: bot.send_message(chat_id, caption, reply_markup=markup, parse_mode="HTML")

# --- 6. PAYMENT INTERFACE ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_'))
def pay_method(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⚡ UPI QR (Mᴀɴᴜᴀʟ)", callback_data=f"qr_{ch_id}_{mins}"))
    if rz_client:
        markup.add(InlineKeyboardButton("💳 Rᴀᴢᴏʀᴘᴀʏ (Aᴜᴛᴏᴍᴀᴛɪᴄ)", callback_data=f"rz_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("🔙 Bᴀᴄᴋ ᴛᴏ Sᴛᴏʀʏ", callback_data=f"back_story_{ch_id}"))

    bot.edit_message_caption(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        caption="🛠 <b>Sᴇᴄᴜʀᴇ Cʜᴇᴄᴋᴏᴜᴛ</b>\n\nSᴇʟᴇᴄᴛ ʏᴏᴜʀ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅ ʙᴇʟᴏᴡ.",
        reply_markup=markup,
        parse_mode="HTML"
    )

# Razorpay Payment Creation
@bot.callback_query_handler(func=lambda call: call.data.startswith('rz_'))
def rz_payment(call):
    _, ch_id, mins = call.data.split('_')
    ch = channels_col.find_one({"channel_id": int(ch_id)})
    
    amount = int(float(ch['price']) * 100)
    order = rz_client.order.create({"amount": amount, "currency": "INR", "payment_capture": "1"})
    
    transactions_col.insert_one({
        "order_id": order['id'], "user_id": call.from_user.id,
        "channel_id": int(ch_id), "minutes": mins, "status": "pending"
    })
    
    pay_url = f"https://api.razorpay.com/v1/checkout/embedded?key_id={RAZORPAY_KEY_ID}&order_id={order['id']}"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Pᴀʏ Nᴏᴡ", url=pay_url))
    bot.send_message(call.message.chat.id, "💳 <b>Cᴏᴍᴘʟᴇᴛᴇ Yᴏᴜʀ Pᴀʏᴍᴇɴᴛ:</b>\n\nClɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ. Aᴄᴄᴇss ᴡɪʟʟ ʙᴇ ɢʀᴀɴᴛᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.", reply_markup=markup, parse_mode="HTML")

# --- 7. MANUAL UPI & PROOF ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('qr_'))
def send_qr(call):
    _, ch_id, mins = call.data.split('_')
    ch = channels_col.find_one({"channel_id": int(ch_id)})
    bot.edit_message_caption("⬇️ <b>Sᴄᴀɴ & Pᴀʏ</b>\n\nAғᴛᴇʀ ᴘᴀʏᴍᴇɴᴛ, ᴜᴘʟᴏᴀᴅ ᴛʜᴇ sᴄʀᴇᴇɴsʜᴏᴛ.", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    
    upi = f"upi://pay?pa={UPI_ID}&pn=PremiumStore&am={ch['price']}&cu=INR"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(upi)}"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📤 Uᴘʟᴏᴀᴅ Sᴄʀᴇᴇɴsʜᴏᴛ", callback_data=f"proof_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr_url, caption=f"💰 <b>Aᴍᴏᴜɴᴛ:</b> ₹{ch['price']}\n🆔 <b>UPI ID:</b> <code>{UPI_ID}</code>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('proof_'))
def ask_proof(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    msg = bot.send_message(call.message.chat.id, "📸 <b>Sᴜʙᴍɪᴛ Pʀᴏᴏғ:</b> Sᴇɴᴅ sᴄʀᴇᴇɴsʜᴏᴛ ɴᴏᴡ.")
    bot.register_next_step_handler(msg, handle_proof, int(call.data.split('_')[1]), int(call.data.split('_')[2]))

def handle_proof(message, ch_id, mins):
    if not message.photo:
        bot.send_message(message.chat.id, "❌ Pʟᴇᴀsᴇ sᴇɴᴅ ᴀɴ ɪᴍᴀɢᴇ.")
        return
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Aᴘᴘʀᴏᴠᴇ", callback_data=f"adm_app_{message.from_user.id}_{ch_id}_{mins}"),
        InlineKeyboardButton("❌ Rᴇᴊᴇᴄᴛ", callback_data=f"adm_rej_{message.from_user.id}")
    )
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"📑 <b>Nᴇᴡ Pʀᴏᴏғ</b>\nFʀᴏᴍ: {message.from_user.id}", reply_markup=markup, parse_mode="HTML")
    bot.send_message(message.chat.id, "⌛ <b>Sᴇɴᴛ!</b> Wᴀɪᴛɪɴɢ ғᴏʀ ᴀᴘᴘʀᴏᴠᴀʟ.")

# --- 8. AUTO-KICK SCHEDULER ---
def kick_expired():
    now = datetime.now(timezone.utc).timestamp()
    for u in users_col.find({"expiry": {"$lte": now}}):
        try:
            bot.ban_chat_member(u['channel_id'], u['user_id'])
            bot.unban_chat_member(u['channel_id'], u['user_id'])
            users_col.delete_one({"_id": u['_id']})
            bot.send_message(u['user_id'], "⚠️ <b>Aᴄᴄᴇss Exᴘɪʀᴇᴅ!</b>")
        except: pass

if __name__ == '__main__':
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))).start()
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired, 'interval', minutes=1)
    scheduler.start()
    bot.infinity_polling()
