import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta, timezone
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

# --- ADMIN LOGIC ---

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
                # Display Dynamic Plans
                for p_time, p_price in ch_data['plans'].items():
                    label = f"{p_time} Min" if int(p_time) < 60 else f"{int(p_time)//1440} Days"
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
                
                markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
                bot.send_message(
                    message.chat.id, 
                    f"Welcome!\n\nYou are joining: <b>{ch_data['name']}</b>.\n\nPlease select a subscription plan below:", 
                    reply_markup=markup, 
                    parse_mode="HTML"
                )
                return
        except Exception as e: 
            print(f"Error in deep link: {e}")

    # Admin Panel Greeting
    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ Admin Panel Active!\n\n/add - Add/Edit Channel & Prices\n/channels - Manage Existing Channels")
    else:
        bot.send_message(message.chat.id, "Welcome! To join a channel, please use the link provided by the Admin.")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    count = 0
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"Channel: {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
        count += 1
    
    markup.add(InlineKeyboardButton("➕ Add New Channel", callback_data="add_new"))
    
    if count == 0:
        bot.send_message(ADMIN_ID, "No channels found. Click below to add one.", reply_markup=markup)
    else:
        bot.send_message(ADMIN_ID, "Your Managed Channels:", reply_markup=markup)

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel_start(message):
    msg = bot.send_message(ADMIN_ID, "Please ensure the bot is an Admin in your channel, then FORWARD any message from that channel here.")
    bot.register_next_step_handler(msg, get_plans)

# Callback for Add New button
@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def cb_add_new(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(ADMIN_ID, "Please FORWARD any message from your channel here.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(
            ADMIN_ID, 
            f"Channel Detected: <b>{ch_name}</b>\n\nEnter plans in format (Minutes:Price):\n<code>Min:Price, Min:Price</code>\n\n"
            "Example:\n<code>1440:99, 43200:199</code> (1 Day and 30 Days)", 
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Message was not forwarded. Use /add to try again.")

def finalize_channel(message, ch_id, ch_name):
    try:
        raw_plans = message.text.split(',')
        plans_dict = {}
        for p in raw_plans:
            t, pr = p.strip().split(':')
            plans_dict[t] = pr
        
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans_dict, "admin_id": ADMIN_ID}}, upsert=True)
        bot_username = bot.get_me().username
        bot.send_message(
            ADMIN_ID, 
            f"✅ Setup Successful!\n\nInvite Link for users:\n<code>https://t.me/{bot_username}?start={ch_id}</code>", 
            parse_mode="HTML"
        )
    except:
        bot.send_message(ADMIN_ID, "❌ Invalid format. Please use `Min:Price, Min:Price`. Use /add to retry.")

# --- USER: PAYMENT FLOW ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    # Properly encode URL parameters to prevent issues with special characters
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    
    bot.send_photo(
        call.message.chat.id, 
        qr_url, 
        caption=f"Plan: {mins} Minutes\nPrice: ₹{price}\nUPI ID: <code>{UPI_ID}</code>\n\nPlease complete the payment and click 'I Have Paid'.", 
        reply_markup=markup, 
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def admin_notify(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    user = call.from_user
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    
    # HTML Parse Mode to avoid crash on special characters in user names or channel names
    bot.send_message(
        ADMIN_ID, 
        f"⚠️ <b>Payment Verification Required!</b>\n\n"
        f"<b>User:</b> {user.first_name}\n"
        f"<b>Channel:</b> {ch_data['name']}\n"
        f"<b>Plan:</b> {mins} Mins\n"
        f"<b>Price:</b> ₹{price}", 
        reply_markup=markup, 
        parse_mode="HTML"
    )
    
    u_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    bot.send_message(call.message.chat.id, "✅ Your payment request has been sent. Please wait for Admin approval.", reply_markup=u_markup)

# --- APPROVAL & EXPIRY ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    bot.answer_callback_query(call.id)
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    
    try:
        # Timezone standard safe conversion
        expiry_datetime = datetime.now(timezone.utc) + timedelta(minutes=mins)
        expiry_ts = int(expiry_datetime.timestamp())

        # Link expires when sub ends
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=expiry_ts)
        
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_datetime.timestamp()}}, upsert=True)
        
        bot.send_message(
            u_id, 
            f"🥳 <b>Payment Approved!</b>\n\n"
            f"Subscription: {mins} Minutes\n\n"
            f"Join Link: {link.invite_link}\n\n"
            f"⚠️ <b>Note:</b> This link and your access will expire in {mins} minutes.", 
            parse_mode="HTML"
        )
        bot.edit_message_text(f"✅ Approved user {u_id} for {mins} mins.", call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error while approving: {e}")

# Reject Callback Handler (Added missing feature to prevent hanging buttons)
@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def reject_now(call):
    bot.answer_callback_query(call.id)
    u_id = int(call.data.split('_')[1])
    try:
        bot.send_message(u_id, "❌ <b>Payment Rejected!</b>\n\nYour payment verification failed. If you think this is a mistake, please contact the admin.", parse_mode="HTML")
        bot.edit_message_text(f"❌ Rejected user {u_id} request.", call.message.chat.id, call.message.message_id)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error while rejecting: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_ch(call):
    bot.answer_callback_query(call.id)
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start={ch_id}"
    
    bot.edit_message_text(
        f"Settings for: <b>{ch_data['name']}</b>\n\nYour Link: <code>{link}</code>\n\nTo edit prices, use /add and forward a message from this channel again.", 
        call.message.chat.id, 
        call.message.message_id, 
        parse_mode="HTML"
    )

# Automate Kicking
def kick_expired_users():
    now = datetime.now(timezone.utc).timestamp()
    expired_users = users_col.find({"expiry": {"$lte": now}})
    bot_username = bot.get_me().username

    for user in expired_users:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            
            rejoin_url = f"https://t.me/{bot_username}?start={user['channel_id']}"
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔄 Re-join / Renew", url=rejoin_url))
            
            bot.send_message(user['user_id'], "⚠️ Your subscription has expired.\n\nTo join again or renew, please click the button below:", reply_markup=markup)
            users_col.delete_one({"_id": user['_id']})
        except Exception as e: 
            print(f"Error kicking user {user['user_id']}: {e}")

# --- STARTUP ---
if __name__ == '__main__':
    keep_alive()
    
    # Scheduler setup
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    
    # PEHLE SE ACTIVE WEBHOOK YA SESSIONS KO CLEAN KARNE KE LIYE
    print("Deleting webhooks and clearing old sessions...")
    bot.delete_webhook(drop_pending_updates=True) 
    
    print("Bot is running...")
    # non_stop=True aur interval=2 lagane se conflict hone par bot crash nahi hota balki retry karta hai
    bot.infinity_polling(timeout=20, long_polling_timeout=10, non_stop=True)
