import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, request, jsonify
from threading import Thread
import razorpay

# --- CONFIGURATION (Environment Variables) ---
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

# --- RAZORPAY SMART INITIALIZATION ---
rz_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        print("✅ Razorpay Client successfully initialized!")
    except Exception as e:
        print(f"⚠️ Razorpay Initialization Error: {e}")
else:
    print("ℹ️ Razorpay keys not found. Running in Manual UPI Only Mode.")

# --- HELPER FUNCTION: NO-FLAG TIME FORMATTING ---
def format_clean_duration(minutes):
    mins = int(minutes)
    if mins < 60:
        return f"{mins} MIN"
    elif mins >= 60 and mins < 1440:
        hours = mins // 60
        return f"{hours} HOUR" if hours == 1 else f"{hours} HOURS"
    else:
        days = mins // 1440
        return f"{days} DAY" if days == 1 else f"{days} DAYS"

# --- RENDER KEEP-ALIVE & RAZORPAY WEBHOOK ---
app = Flask('')

@app.route('/')
def home(): 
    return "Bot is running and healthy!"

@app.route('/razorpay_webhook', methods=['POST'])
def razorpay_webhook():
    if not rz_client or not RAZORPAY_WEBHOOK_SECRET:
        return jsonify({"status": "Razorpay not fully configured"}), 400
    payload = request.data
    signature = request.headers.get('X-Razorpay-Signature')
    try:
        rz_client.utility.verify_webhook_signature(payload.decode('utf-8'), signature, RAZORPAY_WEBHOOK_SECRET)
    except Exception as e:
        print(f"❌ Webhook Verification Failed: {e}")
        return jsonify({"status": "failed"}), 400
    data = request.json
    event = data.get("event")
    if event == "payment.captured":
        payment_entity = data['payload']['payment']['entity']
        order_id = payment_entity.get('order_id')
        tx = transactions_col.find_one({"order_id": order_id, "status": "pending"})
        if tx:
            u_id = tx['user_id']
            ch_id = tx['channel_id']
            mins = tx['minutes']
            transactions_col.update_one({"order_id": order_id}, {"$set": {"status": "success", "payment_id": payment_entity.get('id')}})
            try:
                expiry_datetime = datetime.now(timezone.utc) + timedelta(minutes=mins)
                expiry_ts = int(expiry_datetime.timestamp())
                link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=expiry_ts)
                users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_datetime.timestamp()}}, upsert=True)
                readable_plan = format_clean_duration(mins)
                
                # AUTO JOIN BUTTON FOR RAZORPAY
                join_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 JOIN CHANNEL NOW", url=link.invite_link))
                
                bot.send_message(u_id, f"🥳 <b>PAYMENT AUTOMATICALLY VERIFIED!</b>\n\nSUBSCRIPTION ACTIVE: {readable_plan}\n\n👇 Click the button below to join:", reply_markup=join_markup, parse_mode="HTML")
                bot.send_message(ADMIN_ID, f"✅ <b>RAZORPAY AUTO-APPROVED!</b>\n\nUSER: {u_id}\nPLAN: {readable_plan}")
            except Exception as e:
                print(f"Error sending auto-link: {e}")
    return jsonify({"status": "ok"}), 200

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_web).start()

# --- ADMIN LOGIC ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()
    if len(text) > 1:
        try:
            ch_id = int(text[1])
            show_plans_menu(message.chat.id, ch_id)
            return
        except Exception as e: 
            print(f"Error in deep link: {e}")
    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ <b>ADMIN PANEL ACTIVE!</b>\n\n/add - ADD/EDIT CHANNEL\n/channels - MANAGE", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "WELCOME! Please use the link provided by the admin.")

def show_plans_menu(chat_id, ch_id):
    ch_data = channels_col.find_one({"channel_id": ch_id})
    if ch_data:
        markup = InlineKeyboardMarkup()
        for p_time, p_price in ch_data['plans'].items():
            label = format_clean_duration(p_time)
            markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
        
        # --- NEW DEMO BUTTON ---
        if 'demo_link' in ch_data:
            markup.add(InlineKeyboardButton("📺 WATCH DEMO / EPISODES", url=ch_data['demo_link']))
            
        markup.add(InlineKeyboardButton("📞 CONTACT ADMIN", url=f"https://t.me/{CONTACT_USERNAME}"))
        bot.send_message(chat_id, f"WELCOME!\n\nYOU ARE JOINING: <b>{ch_data['name']}</b>.\n\nPLEASE SELECT A PLAN:", reply_markup=markup, parse_mode="HTML")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    count = 0
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"CHANNEL: {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
        count += 1
    markup.add(InlineKeyboardButton("➕ ADD NEW CHANNEL", callback_data="add_new"))
    bot.send_message(ADMIN_ID, "Your Managed Channels:", reply_markup=markup)

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel_start(message):
    msg = bot.send_message(ADMIN_ID, "Forward any message from your channel here.")
    bot.register_next_step_handler(msg, get_plans)

@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def cb_add_new(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(ADMIN_ID, "Forward any message from your channel here.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"Channel: <b>{ch_name}</b>\nEnter plans (Min:Price, Min:Price):", parse_mode="HTML")
        bot.register_next_step_handler(msg, get_demo_input, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Forward a message.")

# --- NEW STEP TO GET DEMO LINK ---
def get_demo_input(message, ch_id, ch_name):
    try:
        raw_plans = message.text.split(',')
        plans_dict = {p.strip().split(':')[0]: p.strip().split(':')[1] for p in raw_plans}
        msg = bot.send_message(ADMIN_ID, "🔗 Now send the **Demo/Episodes Link** for this channel (or type 'none'):")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name, plans_dict)
    except:
        bot.send_message(ADMIN_ID, "❌ Invalid format. Use /add to retry.")

def finalize_channel(message, ch_id, ch_name, plans_dict):
    demo_url = message.text.strip()
    data = {"name": ch_name, "plans": plans_dict, "admin_id": ADMIN_ID}
    if demo_url.lower() != 'none':
        data["demo_link"] = demo_url
        
    channels_col.update_one({"channel_id": ch_id}, {"$set": data}, upsert=True)
    bot.send_message(ADMIN_ID, f"✅ Setup Successful!\nLink: <code>https://t.me/{bot.get_me().username}?start={ch_id}</code>", parse_mode="HTML")

# --- USER: SELECT PAYMENT METHOD ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = int(ch_data['plans'][mins])
    payment_page_url = None
    if rz_client:
        try:
            rz_order = rz_client.order.create({"amount": price * 100, "currency": "INR", "receipt": f"rcpt_{call.from_user.id}_{ch_id}", "payment_capture": 1})
            order_id = rz_order['id']
            transactions_col.insert_one({"order_id": order_id, "user_id": call.from_user.id, "channel_id": int(ch_id), "minutes": int(mins), "amount": price, "status": "pending", "timestamp": datetime.now(timezone.utc)})
            payment_page_url = f"https://api.razorpay.com/v1/checkout/hosted?key_id={RAZORPAY_KEY_ID}&order_id={order_id}"
        except Exception as e: print(f"RZ Error: {e}")

    markup = InlineKeyboardMarkup()
    if payment_page_url:
        markup.add(InlineKeyboardButton("⚡ AUTOMATIC PAY (RAZORPAY)", url=payment_page_url))
    markup.add(InlineKeyboardButton("✏️ MANUAL PAY (UPI QR)", callback_data=f"manual_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 CONTACT ADMIN", url=f"https://t.me/{CONTACT_USERNAME}"))
    bot.send_message(call.message.chat.id, f"🛒 <b>CHOOSE PAYMENT METHOD</b>\n\nPLAN: {format_clean_duration(mins)}\nPRICE: ₹{price}", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_'))
def manual_checkout(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ I HAVE PAID (VERIFY)", callback_data=f"paid_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr_url, caption=f"📝 <b>MANUAL PAY</b>\nPrice: ₹{price}\nUPI: <code>{UPI_ID}</code>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def ask_for_screenshot(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    msg = bot.send_message(call.message.chat.id, "📷 Send the screenshot receipt now.")
    bot.register_next_step_handler(msg, receive_screenshot, int(ch_id), int(mins))

def receive_screenshot(message, ch_id, mins):
    if not message.photo:
        bot.send_message(message.chat.id, "❌ No photo detected. Try again.")
        return
    user = message.from_user
    ch_data = channels_col.find_one({"channel_id": ch_id})
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ APPROVE", callback_data=f"app_{user.id}_{ch_id}_{mins}"), InlineKeyboardButton("❌ REJECT", callback_data=f"rej_{user.id}"))
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"Proof from {user.first_name}\nChannel: {ch_data['name']}\nPlan: {mins} min", reply_markup=markup)
    bot.send_message(message.chat.id, "✅ Proof sent! Please wait for admin approval.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    bot.answer_callback_query(call.id)
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    try:
        expiry_ts = int((datetime.now(timezone.utc) + timedelta(minutes=mins)).timestamp())
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=expiry_ts)
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_ts}}, upsert=True)
        
        # JOIN BUTTON FOR MANUAL APPROVAL
        join_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 JOIN CHANNEL NOW", url=link.invite_link))
        
        bot.send_message(u_id, f"🥳 <b>PAYMENT APPROVED!</b>\n\nClick below to join:", reply_markup=join_markup, parse_mode="HTML")
        bot.edit_message_caption(f"✅ Approved user {u_id}.", call.message.chat.id, call.message.message_id)
    except Exception as e: bot.send_message(ADMIN_ID, f"Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def reject_now(call):
    bot.answer_callback_query(call.id)
    u_id = int(call.data.split('_')[1])
    bot.send_message(u_id, "❌ <b>PAYMENT REJECTED!</b> Contact admin.")
    bot.edit_message_caption(f"❌ Rejected user {u_id}.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_ch(call):
    bot.answer_callback_query(call.id)
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    bot.edit_message_text(f"Settings for: <b>{ch_data['name']}</b>\nLink: <code>https://t.me/{bot.get_me().username}?start={ch_id}</code>", call.message.chat.id, call.message.message_id, parse_mode="HTML")

def kick_expired_users():
    now = datetime.now(timezone.utc).timestamp()
    expired_users = users_col.find({"expiry": {"$lte": now}})
    for user in expired_users:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            rejoin_url = f"https://t.me/{bot.get_me().username}?start={user['channel_id']}"
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔄 RENEW SUBSCRIPTION", url=rejoin_url))
            bot.send_message(user['user_id'], "⚠️ <b>EXPIRED!</b>\nRenew your plan to continue:", reply_markup=markup, parse_mode="HTML")
            users_col.delete_one({"_id": user['_id']})
        except:
            pass


# --- STARTUP ---
if __name__ == '__main__':
    keep_alive()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    
    print("Clearing stuck telegram hook sessions...")
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Non-fatal bypass: {e}")
    
    print("Bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
