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

# --- UI HELPERS ---
def get_user_markup():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📋 ᴍʏ ᴄʜᴀɴɴᴇʟ", callback_data="user_mychannel"))
    return markup

def format_clean_duration(minutes):
    mins = int(minutes)
    if mins < 60: return f"{mins} ᴍɪɴ"
    if mins < 1440: return f"{mins // 60} ʜᴏᴜʀ"
    return f"{mins // 1440} ᴅᴀʏ"

# --- CORE LOGIC: AUTO KICK SYSTEM ---
def kick_expired_users():
    """Ye function har minute chalega aur expired users ko nikal dega"""
    now = datetime.now(timezone.utc).timestamp()
    expired_users = users_col.find({"expiry": {"$lte": now}})
    
    for user in expired_users:
        try:
            # User ko kick karna (Ban karke Unban karna taaki wo wapas join kar sake payment ke baad)
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            
            # Database se hatana
            users_col.delete_one({"_id": user['_id']})
            
            # User ko inform karna
            bot.send_message(user['user_id'], "❌ <b>ʏᴏᴜʀ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ!</b>\nʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ ғʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ. ᴘʟᴇᴀsᴇ ʀᴇ-sᴜʙsᴄʀɪʙᴇ ᴛᴏ ᴊᴏɪɴ ᴀɢᴀɪɴ.", parse_mode="HTML", reply_markup=get_user_markup())
            print(f"Kicked User: {user['user_id']} from {user['channel_id']}")
        except Exception as e:
            print(f"Error kicking user {user['user_id']}: {e}")

def process_approval(u_id, ch_id, mins, method, tx_id="ɴ/ᴀ"):
    try:
        expiry_time = datetime.now(timezone.utc) + timedelta(minutes=mins)
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int(expiry_time.timestamp() + 3600))
        
        users_col.update_one(
            {"user_id": u_id, "channel_id": ch_id}, 
            {"$set": {"expiry": expiry_time.timestamp(), "joined_at": datetime.now(timezone.utc).timestamp()}}, 
            upsert=True
        )
        
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ɴᴏᴡ", url=link.invite_link))
        bot.send_message(u_id, f"🥳 <b>ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғɪᴇᴅ!</b>\nɪᴅ: <code>{tx_id}</code>\nᴅᴜʀᴀᴛɪᴏɴ: {format_clean_duration(mins)}", reply_markup=markup, parse_mode="HTML")
    except: pass

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    args = message.text.split()
    if len(args) > 1:
        show_plans_menu(message.chat.id, int(args[1]))
        return
    
    if message.from_user.id == ADMIN_ID:
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("➕ ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ", callback_data="admin_add"),
                   InlineKeyboardButton("📊 ᴍᴀɴᴀɢᴇ", callback_data="admin_list"))
        markup.add(InlineKeyboardButton("👤 ᴍʏ ᴄʜᴀɴɴᴇʟ", callback_data="user_mychannel"))
        bot.send_message(message.chat.id, "👋 <b>ᴡᴇʟᴄᴏᴍᴇ ᴀᴅᴍɪɴ</b>", reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "👋 <b>ᴡᴇʟᴄᴏᴍᴇ!</b>", reply_markup=get_user_markup(), parse_mode="HTML")

@bot.message_handler(commands=['mychannel'])
def my_channel_handler(message):
    show_my_plan(message.chat.id, message.from_user.id)

# --- LOGIC & CALLBACKS ---
def show_my_plan(chat_id, user_id):
    user_data = users_col.find_one({"user_id": user_id})
    if user_data:
        ch_data = channels_col.find_one({"channel_id": user_data['channel_id']})
        expiry_dt = datetime.fromtimestamp(user_data['expiry'], tz=timezone.utc)
        bot.send_message(chat_id, f"📋 <b>ᴀᴄᴛɪᴠᴇ ᴘʟᴀɴ</b>\nᴄʜᴀɴɴᴇʟ: <code>{ch_data['name'] if ch_data else 'Unknown'}</code>\nᴇxᴘɪʀᴇs: <code>{expiry_dt.strftime('%Y-%m-%d %H:%M')} ᴜᴛᴄ</code>", reply_markup=get_user_markup(), parse_mode="HTML")
    else:
        bot.send_message(chat_id, "❌ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴘʟᴀɴ.", reply_markup=get_user_markup(), parse_mode="HTML")

def show_plans_menu(chat_id, ch_id):
    ch_data = channels_col.find_one({"channel_id": ch_id})
    if not ch_data: return
    markup = InlineKeyboardMarkup()
    for t, p in ch_data['plans'].items():
        markup.add(InlineKeyboardButton(f"💳 {format_clean_duration(t)} - ₹{p}", callback_data=f"select_{ch_id}_{t}"))
    markup.add(InlineKeyboardButton("📋 ᴍʏ ᴄʜᴀɴɴᴇʟ", callback_data="user_mychannel"))
    bot.send_message(chat_id, f"⭐ <b>ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ {ch_data['name'].upper()}</b>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    d = call.data.split('_')
    if d[0] == 'user' and d[1] == 'mychannel': show_my_plan(call.message.chat.id, call.from_user.id)
    elif d[0] == 'admin' and d[1] == 'add':
        msg = bot.send_message(ADMIN_ID, "👉 <b>ғᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ.</b>", parse_mode="HTML")
        bot.register_next_step_handler(msg, get_plans_admin)
    elif d[0] == 'admin' and d[1] == 'list':
        channels = list(channels_col.find({"admin_id": ADMIN_ID}))
        markup = InlineKeyboardMarkup()
        for ch in channels: markup.add(InlineKeyboardButton(f"📁 {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
        bot.send_message(call.message.chat.id, "📊 <b>ᴀᴅᴅᴇᴅ ᴄʜᴀɴɴᴇʟs:</b>", reply_markup=markup, parse_mode="HTML")
    elif d[0] == 'select':
        ch_id, mins = int(d[1]), d[2]
        price = int(channels_col.find_one({"channel_id": ch_id})['plans'][mins])
        markup = InlineKeyboardMarkup()
        if rz_client:
            order = rz_client.order.create({"amount": price * 100, "currency": "INR", "payment_capture": 1})
            pay_url = f"https://api.razorpay.com/v1/checkout/hosted?key_id={RAZORPAY_KEY_ID}&order_id={order['id']}"
            transactions_col.insert_one({"order_id": order['id'], "user_id": call.from_user.id, "channel_id": ch_id, "minutes": int(mins), "status": "pending"})
            markup.add(InlineKeyboardButton("⚡ ᴀᴜᴛᴏᴍᴀᴛɪᴄ ᴘᴀʏ", url=pay_url))
        markup.add(InlineKeyboardButton("✏️ ᴍᴀɴᴜᴀʟ ᴘᴀʏ", callback_data=f"manual_{ch_id}_{mins}"))
        bot.send_message(call.message.chat.id, "✨ <b>sᴇʟᴇᴄᴛ ᴍᴇᴛʜᴏᴅ:</b>", reply_markup=markup, parse_mode="HTML")

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
    except: bot.send_message(ADMIN_ID, "❌ ғᴏʀᴍᴀᴛ ᴇʀʀᴏʀ.")

# --- SERVER & WEBHOOK ---
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

if __name__ == '__main__':
    # Flask Server start
    Thread(target=lambda: app.run(host='0.0.0.0', port=5000)).start()
    
    # Automatic Kick Scheduler start (Har 1 minute mein check karega)
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    
    # Bot Polling start
    bot.infinity_polling()
