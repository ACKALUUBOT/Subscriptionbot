import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread

# --- RENDER KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_web).start()

# --- CONFIG ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

# --- UTILS ---
def get_time_string(mins):
    mins = int(mins)
    if mins < 60: return f"{mins} Min"
    if mins < 1440: return f"{mins//60} Hours"
    return f"{mins//1440} Days"

# --- ADMIN LOGIC ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()

    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                for p_time, p_price in ch_data['plans'].items():
                    label = get_time_string(p_time)
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"buy_{ch_id}_{p_time}"))
                
                markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
                bot.send_message(message.chat.id, f"💎 *Welcome to {ch_data['name']}*\nSelect a plan to continue:", reply_markup=markup, parse_mode="Markdown")
                return
        except: pass

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 *Admin Panel*\n\n/add - Add New Channel\n/channels - Manage Existing Channels", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "Welcome! Please use an invite link to subscribe.")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    markup = InlineKeyboardMarkup()
    count = 0
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
        count += 1
    
    if count == 0:
        bot.send_message(ADMIN_ID, "No channels found. Use /add to start.")
    else:
        bot.send_message(ADMIN_ID, "📑 *Your Managed Channels:*", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_ch(call):
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start={ch_id}"
    
    text = (f"⚙️ *Settings for:* {ch_data['name']}\n\n"
            f"🔗 *Invite Link:* `{link}`\n\n"
            f"To update prices, simply use /add and forward a message from this channel again.")
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel_start(message):
    msg = bot.send_message(ADMIN_ID, "Step 1: Forward any message from the channel here.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"✅ Channel: {ch_name}\n\nEnter plans as `Mins:Price, Mins:Price` (e.g., `1440:99, 43200:299`)")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Not a forwarded message.")

def finalize_channel(message, ch_id, ch_name):
    try:
        plans = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans, "admin_id": ADMIN_ID}}, upsert=True)
        bot.send_message(ADMIN_ID, f"✅ Setup Successful!\nLink: `https://t.me/{bot.get_me().username}?start={ch_id}`", parse_mode="Markdown")
    except:
        bot.send_message(ADMIN_ID, "❌ Format error. Try /add again.")

# --- USER: PAYMENT FLOW ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def handle_buy(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    msg = bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"💰 *Payment Details*\nPlan: {get_time_string(mins)}\nPrice: ₹{price}\n\n👉 Send the payment screenshot as a reply to this photo.", 
                   parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_screenshot, ch_id, mins, price)

def process_screenshot(message, ch_id, mins, price):
    if message.content_type != 'photo':
        msg = bot.reply_to(message, "❌ Please send an image (screenshot).")
        bot.register_next_step_handler(msg, process_screenshot, ch_id, mins, price)
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{message.from_user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{message.from_user.id}"))
    
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                   caption=f"🔔 *New Request*\nUser: {message.from_user.first_name}\nPlan: {mins}m\nPrice: ₹{price}", 
                   reply_markup=markup, parse_mode="Markdown")
    bot.send_message(message.chat.id, "✅ Received! Admin is checking your payment.")

# --- APPROVAL & EXPIRY ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_user(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    
    user_record = users_col.find_one({"user_id": u_id, "channel_id": ch_id})
    now = datetime.now()
    base_time = datetime.fromtimestamp(user_record['expiry']) if user_record and user_record['expiry'] > now.timestamp() else now
    new_expiry = base_time + timedelta(minutes=mins)
    
    try:
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int(new_expiry.timestamp()))
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": new_expiry.timestamp()}}, upsert=True)
        bot.send_message(u_id, f"🥳 *Approved!*\n\n🔗 Join: {link.invite_link}", parse_mode="Markdown")
        bot.edit_message_caption(f"✅ Approved for {u_id}", call.message.chat.id, call.message.message_id)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Link Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def reject_user(call):
    u_id = int(call.data.split('_')[1])
    bot.send_message(u_id, "❌ Your payment was rejected.")
    bot.edit_message_caption("❌ Rejected", call.message.chat.id, call.message.message_id)

def check_expiries():
    expired = users_col.find({"expiry": {"$lte": datetime.now().timestamp()}})
    for user in expired:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            users_col.delete_one({"_id": user['_id']})
        except: pass

if __name__ == '__main__':
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_expiries, 'interval', minutes=1)
    scheduler.start()
    bot.infinity_polling()
