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

# --- CONFIGURATION ---
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

# --- ADMIN: CHANNEL MANAGEMENT ---

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
                for p_time, p_price in ch_data['plans'].items():
                    label = f"{p_time} Min" if int(p_time) < 60 else f"{int(p_time)//1440} Days"
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
                
                markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
                bot.send_message(message.chat.id, 
                    f"Welcome!\n\nYou are joining: *{ch_data['name']}*.\n\nPlease select a subscription plan:", 
                    reply_markup=markup, parse_mode="Markdown")
                return
        except: pass

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ **Admin Panel Active**\n\n/add - Add New Channel\n/channels - List My Channels", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "Welcome! Please use an invite link to join a channel.")

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel_start(message):
    msg = bot.send_message(ADMIN_ID, "Forward any message from your channel here (Make sure Bot is Admin there).")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"Channel: *{ch_name}*\n\nEnter plans as `Min:Price, Min:Price` (e.g., `1440:99, 43200:299`)", parse_mode="Markdown")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Please forward a message from the channel.")

def finalize_channel(message, ch_id, ch_name):
    try:
        plans_dict = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans_dict, "admin_id": ADMIN_ID}}, upsert=True)
        bot.send_message(ADMIN_ID, f"✅ Channel Added!\nLink: `https://t.me/{bot.get_me().username}?start={ch_id}`", parse_mode="Markdown")
    except:
        bot.send_message(ADMIN_ID, "❌ Format Error. Use `Min:Price`.")

# --- USER: PAYMENT & SCREENSHOT FLOW ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    msg = bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"💰 **Payment Details**\n\nPlan: {mins} Mins\nPrice: ₹{price}\nUPI: `{UPI_ID}`\n\n👇 **MUST:** Send the Payment Screenshot here now.", 
                   parse_mode="Markdown")
    
    bot.register_next_step_handler(msg, process_screenshot, ch_id, mins)

def process_screenshot(message, ch_id, mins):
    if message.content_type != 'photo':
        bot.reply_to(message, "❌ Invalid! Please send a Photo/Screenshot of payment. Try /start again.")
        return

    user = message.from_user
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    
    # Notify Admin
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                   caption=f"🔔 **New Payment Request**\n\nUser: {user.first_name} (@{user.username})\nChannel: {ch_data['name']}\nPlan: {mins} Mins", 
                   reply_markup=markup, parse_mode="Markdown")
    
    bot.send_message(message.chat.id, "✅ Screenshot received! Please wait for Admin approval.")

# --- ADMIN: APPROVE / REJECT ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    
    try:
        expiry_date = datetime.utcnow() + timedelta(minutes=mins)
        # Create single-use link
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int((datetime.utcnow() + timedelta(days=1)).timestamp()))
        
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_date}}, upsert=True)
        
        bot.send_message(u_id, f"🥳 **Payment Approved!**\n\nYour Link: {link.invite_link}\nValid for: {mins} Minutes.", parse_mode="Markdown")
        bot.edit_message_caption("✅ User Approved & Link Sent.", call.message.chat.id, call.message.message_id)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def reject_now(call):
    u_id = int(call.data.split('_')[1])
    bot.send_message(u_id, "❌ **Payment Rejected!**\nYour screenshot was invalid or payment not received.", parse_mode="Markdown")
    bot.edit_message_caption("❌ Request Rejected.", call.message.chat.id, call.message.message_id)

# --- AUTO-KICK SYSTEM ---

def kick_expired_users():
    now = datetime.utcnow()
    expired = users_col.find({"expiry": {"$lte": now}})
    for user in expired:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            bot.send_message(user['user_id'], "⚠️ Your subscription has expired and you've been removed. Re-join via /start.")
            users_col.delete_one({"_id": user['_id']})
        except: pass

# --- STARTUP ---
if __name__ == '__main__':
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    print("Bot is starting...")
    bot.infinity_polling()
