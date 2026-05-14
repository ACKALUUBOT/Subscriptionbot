import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread

# --- RENDER KEEP-ALIVE SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running and healthy!"

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_web).start()

# --- CONFIGURATION (Environment Variables) ---
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

# --- GLOBAL CANCEL ---
@bot.message_handler(commands=['cancel'])
def cancel_all(message):
    bot.clear_step_handler_by_chat_id(chat_id=message.chat.id)
    bot.send_message(message.chat.id, "❌ Action cancelled. Current process stopped.")

# --- START & ADMIN PANEL ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()

    # User entry via Deep Link
    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                # Dynamic Plans
                for p_time, p_price in ch_data['plans'].items():
                    label = get_time_string(p_time)
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"buy_{ch_id}_{p_time}"))
                
                # Per-Channel Demo Link
                if ch_data.get('demo_link'):
                    markup.add(InlineKeyboardButton("📺 View Demo & Quality", url=ch_data['demo_link']))
                
                markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
                
                bot.send_message(message.chat.id, 
                    f"💎 *Welcome to {ch_data['name']}*\n\nPlease select a subscription plan below to get access:", 
                    reply_markup=markup, parse_mode="Markdown")
                return
        except: pass

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 *Admin Panel Active!*\n\n/add - Setup New Channel\n/channels - Manage List\n/cancel - Stop ongoing process")
    else:
        bot.send_message(message.chat.id, "Welcome! Please use the official invite link provided by the admin to subscribe.")

# --- ADMIN: ADD CHANNEL FLOW (3 STEPS) ---
@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_start(message):
    msg = bot.send_message(ADMIN_ID, "Step 1: Forward any message from the target channel here (or /cancel).")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.text == '/cancel': return
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"✅ Channel Detected: *{ch_name}*\n\nStep 2: Enter plans as `Min:Price, Min:Price`.\nExample: `1440:99, 43200:299` (1 Day & 30 Days)")
        bot.register_next_step_handler(msg, get_demo_link, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Message must be forwarded. Try /add again.")

def get_demo_link(message, ch_id, ch_name):
    if message.text == '/cancel': return
    try:
        # Validate plan format
        plans = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
        msg = bot.send_message(ADMIN_ID, f"Step 3: Enter the *Demo Link* for this channel.\n(Or type `none` if not needed)")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name, plans)
    except:
        bot.send_message(ADMIN_ID, "❌ Invalid Plan Format. Use `Min:Price, Min:Price`. Use /add to restart.")

def finalize_channel(message, ch_id, ch_name, plans):
    if message.text == '/cancel': return
    demo_url = None if message.text.lower() == 'none' else message.text
    
    channels_col.update_one(
        {"channel_id": ch_id}, 
        {"$set": {"name": ch_name, "plans": plans, "demo_link": demo_url, "admin_id": ADMIN_ID}}, 
        upsert=True
    )
    
    bot_username = bot.get_me().username
    bot.send_message(ADMIN_ID, f"✅ *Setup Successful!*\n\nChannel: {ch_name}\nLink for Users:\n`https://t.me/{bot_username}?start={ch_id}`", parse_mode="Markdown")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    markup = InlineKeyboardMarkup()
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
    
    bot.send_message(ADMIN_ID, "📑 *Your Managed Channels:*", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_ch(call):
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start={ch_id}"
    
    bot.edit_message_text(f"⚙️ *Settings for:* {ch_data['name']}\n\nUser Link: `{link}`\nDemo Link: {ch_data.get('demo_link', 'None')}\n\nTo update, use /add again.", 
                          call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# --- USER: PAYMENT FLOW ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def handle_buy(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid (Upload Screenshot)", callback_data=f"paid_{ch_id}_{mins}"))
    
    bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"💰 *Payment Details*\nPlan: {get_time_string(mins)}\nPrice: ₹{price}\nUPI ID: `{UPI_ID}`\n\n1. Pay the amount via UPI.\n2. Click the button below to upload your screenshot.", 
                   reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def request_screenshot(call):
    _, ch_id, mins = call.data.split('_')
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📸 Please upload your *Payment Screenshot* now (or /cancel).")
    bot.register_next_step_handler(msg, process_screenshot, ch_id, mins)

def process_screenshot(message, ch_id, mins):
    if message.text == '/cancel': return
    if message.content_type != 'photo':
        msg = bot.reply_to(message, "❌ Invalid format. Please send an image/screenshot.")
        bot.register_next_step_handler(msg, process_screenshot, ch_id, mins)
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{message.from_user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{message.from_user.id}"))
    
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                   caption=f"🔔 *New Payment Received*\nUser: {message.from_user.first_name}\nID: `{message.from_user.id}`\nPlan: {get_time_string(mins)}", 
                   reply_markup=markup, parse_mode="Markdown")
    
    bot.send_message(message.chat.id, "✅ Screenshot sent! Admin will verify and provide the link shortly.")

# --- APPROVAL & AUTO-KICK ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_user(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    
    user_record = users_col.find_one({"user_id": u_id, "channel_id": ch_id})
    now = datetime.now()
    
    # Subscription Stacking Logic
    if user_record and user_record['expiry'] > now.timestamp():
        base_time = datetime.fromtimestamp(user_record['expiry'])
    else:
        base_time = now

    new_expiry = base_time + timedelta(minutes=mins)
    
    try:
        # Create single-use invite link expiring with sub
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int(new_expiry.timestamp()))
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": new_expiry.timestamp()}}, upsert=True)
        
        bot.send_message(u_id, f"🥳 *Payment Approved!*\n\nPlan: {get_time_string(mins)}\nExpires: {new_expiry.strftime('%Y-%m-%d %H:%M')}\n\n🔗 *Join Link:* {link.invite_link}\n\n_Note: This link is unique and for you only._", parse_mode="Markdown")
        bot.edit_message_caption(f"✅ Approved for {get_time_string(mins)}", call.message.chat.id, call.message.message_id)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Link Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def reject_user(call):
    u_id = int(call.data.split('_')[1])
    bot.send_message(u_id, "❌ Your payment could not be verified. Please contact the admin if you have queries.")
    bot.edit_message_caption("❌ Rejected", call.message.chat.id, call.message.message_id)

def check_expiries():
    now = datetime.now().timestamp()
    expired = users_col.find({"expiry": {"$lte": now}})
    for user in expired:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id']) # Unban so they can rejoin later
            bot.send_message(user['user_id'], "⌛ Your subscription has expired. Use your start link to renew!")
            users_col.delete_one({"_id": user['_id']})
        except: pass

# --- STARTUP ---
if __name__ == '__main__':
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_expiries, 'interval', minutes=1)
    scheduler.start()
    print("Bot is starting...")
    bot.infinity_polling()
