import os
import telebot
import urllib.parse
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from threading import Thread
from apscheduler.schedulers.background import BackgroundScheduler
import razorpay

# --- 1. CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = os.getenv('RAZORPAY_WEBHOOK_SECRET')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col, users_col, transactions_col = db['channels'], db['users'], db['transactions']

rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID else None

# --- 2. KEYBOARDS ---
def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    stories = list(channels_col.find())
    for i, s in enumerate(stories, start=1):
        markup.add(KeyboardButton(f"{i}. {s['name']} [ ₹{s['price']} ] Nᴇᴡ"))
    return markup

# --- 3. AUTO-APPROVE LOGIC (PROCESSOR) ---
def process_approval(u_id, ch_id, mins):
    expiry_ts = int((datetime.now(timezone.utc) + timedelta(minutes=int(mins))).timestamp())
    link = bot.create_chat_invite_link(int(ch_id), member_limit=1, expire_date=expiry_ts)
    users_col.update_one({"user_id": int(u_id), "channel_id": int(ch_id)}, {"$set": {"expiry": expiry_ts}}, upsert=True)
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 Jᴏɪɴ Nᴏᴡ", url=link.invite_link))
    bot.send_message(u_id, "✅ <b>Pᴀʏᴍᴇɴᴛ Vᴇʀɪғɪᴇᴅ!</b>\nYᴏᴜʀ ᴀᴄᴄᴇss ɪs ɴᴏᴡ ᴀᴄᴛɪᴠᴇ.", reply_markup=markup, parse_mode="HTML")

# --- 4. WEBHOOK FOR RAZORPAY (AUTO-APPROVE) ---
app = Flask('')
@app.route('/razorpay_webhook', methods=['POST'])
def rz_webhook():
    payload = request.data
    signature = request.headers.get('X-Razorpay-Signature')
    try:
        rz_client.utility.verify_webhook_signature(payload.decode('utf-8'), signature, RAZORPAY_WEBHOOK_SECRET)
        data = request.json
        if data['event'] == "payment.captured":
            order_id = data['payload']['payment']['entity']['order_id']
            tx = transactions_col.find_one({"order_id": order_id})
            if tx:
                process_approval(tx['user_id'], tx['channel_id'], tx['mins'])
    except: pass
    return jsonify({"status": "ok"}), 200

@app.route('/')
def home(): return "Bᴏᴛ Is Aʟɪᴠᴇ"

# --- 5. USER FLOW (CATCHING STORY) ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    bot.send_message(message.chat.id, "<b>〖 Aᴠᴀɪʟᴀʙʟᴇ Sᴛᴏʀɪᴇs 〗</b>", reply_markup=get_main_menu(), parse_mode="HTML")

@bot.message_handler(func=lambda message: " [ ₹" in message.text)
def handle_story(message):
    try:
        name = message.text.split('. ')[1].split(' [')[0].strip()
        ch = channels_col.find_one({"name": name})
        if ch:
            cap = f"🎬 <b>{ch['name'].upper()}</b>\n💰 <b>Pʀɪᴄᴇ: ₹{ch['price']}</b>\n📖 <b>{ch['desc']}</b>"
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✅ CONFIRM", callback_data=f"conf_{ch['channel_id']}"))
            bot.send_photo(message.chat.id, ch['photo'], caption=cap, reply_markup=markup, parse_mode="HTML")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('conf_'))
def show_terms(call):
    ch_id = call.data.split('_')[1]
    ch = channels_col.find_one({"channel_id": int(ch_id)})
    text = f"👤 <b>Usᴇʀ:</b> {call.from_user.first_name}\n📖 <b>Sᴛᴏʀʏ: {ch['name']}</b>\n\n<b>Aɢʀᴇᴇ ᴛᴏ Tᴇʀᴍs?</b>"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("I Aᴄᴄᴇᴘᴛ", callback_data=f"paymeth_{ch_id}"))
    bot.edit_message_caption(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paymeth_'))
def select_pay(call):
    ch_id = call.data.split('_')[1]
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("⚡ UPI QR", callback_data=f"qr_{ch_id}"),
                                       InlineKeyboardButton("💳 Rᴀᴢᴏʀᴘᴀʏ (Aᴜᴛᴏ)", callback_data=f"rz_{ch_id}"))
    bot.edit_message_caption("Sᴇʟᴇᴄᴛ Pᴀʏᴍᴇɴᴛ Mᴇᴛʜᴏᴅ:", call.message.chat.id, call.message.message_id, reply_markup=markup)

# --- 6. ADMIN COMMANDS (ADD/MANAGE) ---
@bot.message_handler(commands=['add'])
def add_story(message):
    if message.from_user.id != ADMIN_ID: return
    msg = bot.send_message(ADMIN_ID, "Fᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ᴛʜᴇ sᴛᴏʀʏ ᴄʜᴀɴɴᴇʟ:")
    bot.register_next_step_handler(msg, step1)

def step1(message):
    if message.forward_from_chat:
        cid, name = message.forward_from_chat.id, message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, "Sᴇɴᴅ: `Pʜᴏᴛᴏ | Eᴘs | OʟᴅPʀɪᴄᴇ | NᴇᴡPʀɪᴄᴇ | Mɪɴs | Dᴇsᴄ`")
        bot.register_next_step_handler(msg, step2, cid, name)

def step2(message, cid, name):
    p = message.text.split('|')
    channels_col.update_one({"channel_id": cid}, {"$set": {"name": name, "photo": p[0].strip(), "eps": p[1].strip(), "old_price": p[2].strip(), "price": p[3].strip(), "mins": p[4].strip(), "desc": p[5].strip()}}, upsert=True)
    bot.send_message(ADMIN_ID, "✅ Sᴛᴏʀʏ Aᴅᴅᴇᴅ!")

# --- 7. AUTO-KICK SYSTEM ---
def auto_kick():
    now = datetime.now(timezone.utc).timestamp()
    expired = users_col.find({"expiry": {"$lte": now}})
    for u in expired:
        try:
            bot.ban_chat_member(u['channel_id'], u['user_id'])
            bot.unban_chat_member(u['channel_id'], u['user_id'])
            users_col.delete_one({"_id": u['_id']})
        except: pass

# --- 8. MANUAL APPROVAL BUTTONS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_manual(call):
    d = call.data.split('_')
    if d[1] == "app": process_approval(d[2], d[3], d[4])
    else: bot.send_message(d[2], "❌ Pᴀʏᴍᴇɴᴛ Rᴇᴊᴇᴄᴛᴇᴅ!")
    bot.edit_message_caption(f"Dᴇᴄɪsɪᴏɴ: {d[1]}", call.message.chat.id, call.message.message_id)

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_kick, 'interval', minutes=1)
    scheduler.start()
    Thread(target=lambda: app.run(host='0.0.0.0', port=5000)).start()
    bot.infinity_polling()
