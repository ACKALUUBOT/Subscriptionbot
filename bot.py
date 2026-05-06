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

# --- CONFIGURATION (Environment Variables) ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')  # For manual payments
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

# Razorpay Details
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
    print("ℹ️ Razorpay keys not found/incomplete. Running in Manual UPI Only Mode.")

# --- HELPER FUNCTION: SMALL CAPS TIME FORMATTING ---
def format_small_caps_duration(minutes):
    """Converts raw minutes into beautiful, consistent Small Caps durations."""
    mins = int(minutes)
    if mins < 60:
        return f"{mins} ᴍɪɴ"
    elif mins >= 60 and mins < 1440:
        hours = mins // 60
        return f"{hours} ʜᴏᴜʀs" if hours > 1 else f"{hours} ʜᴏᴜʀ"
    else:
        days = mins // 1440
        return f"{days} ᴅᴀʏs" if days > 1 else f"{days} ᴅᴀʏ"

# --- RENDER KEEP-ALIVE & RAZORPAY WEBHOOK ---
app = Flask('')

@app.route('/')
def home(): 
    return "Bot is running and healthy!"

# Razorpay Automatic Webhook
@app.route('/razorpay_webhook', methods=['POST'])
def razorpay_webhook():
    if not rz_client or not RAZORPAY_WEBHOOK_SECRET:
        return jsonify({"status": "Razorpay not fully configured"}), 400

    payload = request.data
    signature = request.headers.get('X-Razorpay-Signature')

    try:
        rz_client.utility.verify_webhook_signature(
            payload.decode('utf-8'), 
            signature, 
            RAZORPAY_WEBHOOK_SECRET
        )
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

                users_col.update_one(
                    {"user_id": u_id, "channel_id": ch_id}, 
                    {"$set": {"expiry": expiry_datetime.timestamp()}}, 
                    upsert=True
                )

                readable_plan = format_small_caps_duration(mins)

                bot.send_message(
                    u_id, 
                    f"🥳 <b>ᴘᴀʏᴍᴇɴᴛ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴠᴇʀɪғɪᴇᴅ!</b>\n\n"
                    f"sᴜʙsᴄʀɪᴘᴛɪᴏɴ ᴀᴄᴛɪᴠᴇ: {readable_plan}\n\n"
                    f"👇 ᴊᴏɪɴ ᴜsɪɴɢ ᴛʜᴇ ʟɪɴᴋ ʙᴇʟᴏᴡ:\n{link.invite_link}\n\n"
                    f"⚠️ <b>ɴᴏᴛᴇ:</b> ᴀᴄᴄᴇss ʟɪɴᴋ ᴡɪʟʟ ᴇxᴘɪʀᴇ ɪɴ {readable_plan}.", 
                    parse_mode="HTML"
                )

                bot.send_message(
                    ADMIN_ID, 
                    f"✅ <b>ʀᴀᴢᴏʀᴘᴀʏ ᴀᴜᴛᴏ-ᴀᴘᴘʀᴏᴠᴇᴅ!</b>\n\n"
                    f"ᴜsᴇʀ: {u_id}\n"
                    f"ᴀᴍᴏᴜɴᴛ: ₹{tx['amount']}\n"
                    f"ᴘʟᴀɴ: {readable_plan}"
                )
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
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                for p_time, p_price in ch_data['plans'].items():
                    label = format_small_caps_duration(p_time)
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
                
                markup.add(InlineKeyboardButton("📞 ᴄᴏɴᴛᴀᴄᴛ ᴀᴅᴍɪɴ", url=f"https://t.me/{CONTACT_USERNAME}"))
                bot.send_message(
                    message.chat.id, 
                    f"ᴡᴇʟᴄᴏᴍᴇ!\n\nʏᴏᴜ ᴀʀᴇ ᴊᴏɪɴɪɴɢ: <b>{ch_data['name']}</b>.\n\nᴘʟᴇᴀsᴇ sᴇʟᴇᴄᴛ ᴀ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ᴘʟᴀɴ ʙᴇʟᴏᴡ:", 
                    reply_markup=markup, 
                    parse_mode="HTML"
                )
                return
        except Exception as e: 
            print(f"Error in deep link: {e}")

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ <b>ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ ᴀᴄᴛɪᴠᴇ!</b>\n\n/add - ᴀᴅᴅ/ᴇᴅɪᴛ ᴄʜᴀɴɴᴇʟ & ᴘʀɪᴄᴇs\n/channels - ᴍᴀɴᴀɢᴇ ᴇxɪsᴛɪɴɢ ᴄʜᴀɴɴᴇʟs", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "ᴡᴇʟᴄᴏᴍᴇ! ᴛᴏ ᴊᴏɪɴ ᴀ ᴄʜᴀɴɴᴇʟ, ᴘʟᴇᴀsᴇ ᴜsᴇ ᴛʜᴇ sᴘᴇᴄɪғɪᴄ ʟɪɴᴋ ᴘʀᴏᴠɪᴅᴇᴅ ʙʏ ᴛʜᴇ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ.")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    count = 0
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"ᴄʜᴀɴɴᴇʟ: {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
        count += 1
    
    markup.add(InlineKeyboardButton("➕ ᴀᴅᴅ ɴᴇᴡ ᴄʜᴀɴɴᴇʟ", callback_data="add_new"))
    
    if count == 0:
        bot.send_message(ADMIN_ID, "No channels found. Click below to add one.", reply_markup=markup)
    else:
        bot.send_message(ADMIN_ID, "Your Managed Channels:", reply_markup=markup)

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel_start(message):
    msg = bot.send_message(ADMIN_ID, "Please ensure the bot is an Admin in your channel, then FORWARD any message from that channel here.")
    bot.register_next_step_handler(msg, get_plans)

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

# --- USER: SELECT PAYMENT METHOD (HYBRID FLOW) ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = int(ch_data['plans'][mins])
    
    payment_page_url = None

    # Razorpay activation check
    if rz_client:
        try:
            rz_order = rz_client.order.create({
                "amount": price * 100, 
                "currency": "INR", 
                "receipt": f"rcpt_{call.from_user.id}_{ch_id}",
                "payment_capture": 1
            })
            order_id = rz_order['id']

            transactions_col.insert_one({
                "order_id": order_id,
                "user_id": call.from_user.id,
                "channel_id": int(ch_id),
                "minutes": int(mins),
                "amount": price,
                "status": "pending",
                "timestamp": datetime.now(timezone.utc)
            })

            payment_page_url = f"https://api.razorpay.com/v1/checkout/hosted?key_id={RAZORPAY_KEY_ID}&order_id={order_id}"
        except Exception as e:
            print(f"Razorpay Order Generation failed, falling back to manual: {e}")

    markup = InlineKeyboardMarkup()
    
    if payment_page_url:
        markup.add(InlineKeyboardButton("⚡ ᴀᴜᴛᴏᴍᴀᴛɪᴄ ᴘᴀʏ (ʀᴀᴢᴏʀᴘᴀʏ)", url=payment_page_url))
        payment_text = (
            f"🚀 <b>ᴀᴜᴛᴏᴍᴀᴛɪᴄ ᴘᴀʏ:</b> ᴘᴀʏ sᴇᴄᴜʀᴇʟʏ ᴠɪᴀ ʀᴀᴢᴏʀᴘᴀʏ, ᴀɴᴅ ʀᴇᴄᴇɪᴠᴇ ʏᴏᴜʀ ɪɴᴠɪᴛᴇ ʟɪɴᴋ ɪɴsᴛᴀɴᴛʟʏ.\n"
            f"🛠️ <b>ᴍᴀɴᴜᴀʟ ᴘᴀʏ:</b> sᴄᴀɴ ᴏᴜʀ ᴜᴘɪ ϙʀ ᴄᴏᴅᴇ, sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ, ᴀɴᴅ ᴡᴀɪᴛ ғᴏʀ ᴀᴅᴍɪɴ ᴀᴘᴘʀᴏᴠᴀʟ."
        )
    else:
        payment_text = f"🛠️ ᴘʟᴇᴀsᴇ ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴍᴀᴋᴇ ᴀ <b>ᴍᴀɴᴜᴀʟ ᴘᴀʏᴍᴇɴᴛ</b> ᴜsɪɴɢ ᴛʜᴇ ϙʀ ᴄᴏᴅᴇ."

    markup.add(InlineKeyboardButton("✏️ ᴍᴀɴᴜᴀʟ ᴘᴀʏ (ᴜᴘɪ ϙʀ)", callback_data=f"manual_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 ᴄᴏɴᴛᴀᴄᴛ ᴀᴅᴍɪɴ", url=f"https://t.me/{CONTACT_USERNAME}"))

    readable_plan = format_small_caps_duration(mins)

    bot.send_message(
        call.message.chat.id,
        f"🛒 <b>ᴄʜᴏᴏsᴇ ᴘᴀʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅ</b>\n\n"
        f"<b>ᴄʜᴀɴɴᴇʟ:</b> {ch_data['name']}\n"
        f"<b>ᴘ🇱🇦🇳:</b> {readable_plan}\n"
        f"<b>ᴘ🇷🇮🇨🇪:</b> ₹{price}\n\n"
        f"{payment_text}",
        reply_markup=markup,
        parse_mode="HTML"
    )

# --- MANUAL PAYMENT SUB-FLOW ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_'))
def manual_checkout(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ (ᴠᴇʀɪғʏ)", callback_data=f"paid_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 ᴄᴏɴᴛᴀᴄᴛ ᴀᴅᴍɪɴ", url=f"https://t.me/{CONTACT_USERNAME}"))
    
    readable_plan = format_small_caps_duration(mins)

    bot.send_photo(
        call.message.chat.id, 
        qr_url, 
        caption=f"📝 <b>ᴍᴀɴᴜᴀʟ ᴘᴀʏᴍᴇɴᴛ sᴇᴛᴜᴘ</b>\n\n"
                f"<b>ᴘ🇱🇦🇳:</b> {readable_plan}\n"
                f"<b>ᴘ🇷🇮🇨🇪:</b> ₹{price}\n"
                f"<b>ᴜᴘɪ ɪᴅ:</b> <code>{UPI_ID}</code>\n\n"
                f"ᴘʟᴇᴀsᴇ sᴄᴀɴ ᴛʜɪs ϙʀ ᴄᴏᴅᴇ, ᴄᴏᴍᴘﻠᴇᴛᴇ ʏᴏᴜʀ ᴛʀᴀɴsᴀᴄᴛɪᴏɴ, ᴀɴᴅ ᴛʜᴇɴ ᴄʟɪᴄᴋ **'ɪ ʜᴀᴠᴇ ᴘᴀɪᴅ'** ғᴏʀ ᴍᴀɴᴜᴀʟ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ.", 
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
    markup.add(InlineKeyboardButton("✅ ᴀᴘᴘʀᴏᴠᴇ", callback_data=f"app_{user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ ʀᴇᴊᴇᴄᴛ", callback_data=f"rej_{user.id}"))
    
    readable_plan = format_small_caps_duration(mins)

    bot.send_message(
        ADMIN_ID, 
        f"⚠️ <b>ᴍᴀɴᴜᴀʟ ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ʀᴇϙᴜɪʀᴇᴅ!</b>\n\n"
        f"<b>ᴜsᴇʀ:</b> {user.first_name}\n"
        f"<b>ᴄʜᴀɴɴᴇʟ:</b> {ch_data['name']}\n"
        f"<b>ᴘʟᴀɴ:</b> {readable_plan}\n"
        f"<b>ᴘ🇷🇮🇨🇪:</b> ₹{price}", 
        reply_markup=markup, 
        parse_mode="HTML"
    )
    
    u_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📞 ᴄᴏɴᴛᴀᴄᴛ ᴀᴅᴍɪɴ", url=f"https://t.me/{CONTACT_USERNAME}"))
    bot.send_message(call.message.chat.id, "✅ ʏᴏᴜʀ ᴍᴀɴᴜᴀʟ ᴘᴀʏᴍᴇɴᴛ ʀᴇϙᴜᴇsᴛ ʜᴀs ʙᴇᴇɴ sᴇɴᴛ ᴛᴏ ᴛʜᴇ ᴀᴅᴍɪɴ. ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ғᴏʀ ᴄᴏɴғɪʀᴍᴀᴛɪᴏɴ.", reply_markup=u_markup)

# --- APPROVAL & EXPIRY (Manual) ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    bot.answer_callback_query(call.id)
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    
    try:
        expiry_datetime = datetime.now(timezone.utc) + timedelta(minutes=mins)
        expiry_ts = int(expiry_datetime.timestamp())

        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=expiry_ts)
        
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_datetime.timestamp()}}, upsert=True)
        
        readable_plan = format_small_caps_duration(mins)

        bot.send_message(
            u_id, 
            f"🥳 <b>ᴘᴀʏᴍᴇɴᴛ ᴀᴘᴘʀᴏᴠᴇᴅ (ᴍᴀɴᴜᴀʟ)!</b>\n\n"
            f"sᴜʙsᴄʀɪᴘᴛɪᴏɴ: {readable_plan}\n\n"
            f"ᴊᴏɪɴ ʟɪɴᴋ: {link.invite_link}\n\n"
            f"⚠️ <b>ɴᴏᴛᴇ:</b> ᴀᴄᴄᴇss ʟɪɴᴋ ᴡɪʟʟ ᴇxᴘɪʀᴇ ɪɴ {readable_plan}.", 
            parse_mode="HTML"
        )
        bot.edit_message_text(f"✅ Approved user {u_id} for {readable_plan}.", call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error while approving: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def reject_now(call):
    bot.answer_callback_query(call.id)
    u_id = int(call.data.split('_')[1])
    try:
        bot.send_message(u_id, "❌ <b>ᴘᴀʏᴍᴇɴᴛ ʀᴇᴊᴇᴄᴛᴇᴅ!</b>\n\nʏᴏʀ ᴍᴀɴᴜᴀʟ ᴘᴀʏᴍᴇɴᴛ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ғᴀɪʟᴇᴅ. ᴘʟᴇᴀsᴇ ᴄᴏɴᴛᴀᴄᴛ ᴛʜᴇ ᴀᴅᴍɪɴ.", parse_mode="HTML")
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
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔄 ʀᴇ-ᴊᴏɪɴ / ʀᴇɴᴇᴡ", url=rejoin_url))
            
            bot.send_message(user['user_id'], "⚠️ ʏᴏᴜʀ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ʜᴀs ᴇxᴘɪʀᴇᴅ.\n\nᴛᴏ ᴊᴏɪɴ ᴀɢᴀɪɴ ᴏʀ ʀᴇɴᴇᴡ, ᴘʟᴇᴀsᴇ ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ:", reply_markup=markup)
            users_col.delete_one({"_id": user['_id']})
        except Exception as e: 
            print(f"Error kicking user {user['user_id']}: {e}")

# --- STARTUP ---
if __name__ == '__main__':
    keep_alive()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    
    # Forceful Webhook deletion on Telegram endpoint before starting long polling
    print("Clearing stuck telegram hook sessions...")
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Non-fatal bypass: {e}")
    
    print("Bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
