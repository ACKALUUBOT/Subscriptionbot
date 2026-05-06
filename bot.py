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

                readable_plan = format_clean_duration(mins)

                bot.send_message(
                    u_id, 
                    f"🥳 <b>PAYMENT AUTOMATICALLY VERIFIED!</b>\n\n"
                    f"SUBSCRIPTION ACTIVE: {readable_plan}\n\n"
                    f"👇 JOIN USING THE LINK BELOW:\n{link.invite_link}\n\n"
                    f"⚠️ <b>NOTE:</b> ACCESS LINK WILL EXPIRE IN {readable_plan}.", 
                    parse_mode="HTML"
                )

                bot.send_message(
                    ADMIN_ID, 
                    f"✅ <b>RAZORPAY AUTO-APPROVED!</b>\n\n"
                    f"USER: {u_id}\n"
                    f"AMOUNT: ₹{tx['amount']}\n"
                    f"PLAN: {readable_plan}"
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
            show_plans_menu(message.chat.id, ch_id)
            return
        except Exception as e: 
            print(f"Error in deep link: {e}")

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ <b>ADMIN PANEL ACTIVE!</b>\n\n/add - ADD/EDIT CHANNEL & PRICES\n/channels - MANAGE EXISTING CHANNELS", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "WELCOME! TO JOIN A CHANNEL, PLEASE USE THE SPECIFIC LINK PROVIDED BY THE ADMINISTRATOR.")

def show_plans_menu(chat_id, ch_id):
    ch_data = channels_col.find_one({"channel_id": ch_id})
    if ch_data:
        markup = InlineKeyboardMarkup()
        for p_time, p_price in ch_data['plans'].items():
            label = format_clean_duration(p_time)
            markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
        
        markup.add(InlineKeyboardButton("📞 CONTACT ADMIN", url=f"https://t.me/{CONTACT_USERNAME}"))
        bot.send_message(
            chat_id, 
            f"WELCOME!\n\nYOU ARE JOINING: <b>{ch_data['name']}</b>.\n\nPLEASE SELECT A SUBSCRIPTION PLAN BELOW:", 
            reply_markup=markup, 
            parse_mode="HTML"
        )

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    count = 0
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"CHANNEL: {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
        count += 1
    
    markup.add(InlineKeyboardButton("➕ ADD NEW CHANNEL", callback_data="add_new"))
    
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
            print(f"Razorpay Order Generation failed: {e}")

    markup = InlineKeyboardMarkup()
    
    if payment_page_url:
        markup.add(InlineKeyboardButton("⚡ AUTOMATIC PAY (RAZORPAY)", url=payment_page_url))
        payment_text = (
            f"🚀 <b>AUTOMATIC PAY:</b> Pay securely via Razorpay, and receive your invite link instantly.\n"
            f"🛠️ <b>MANUAL PAY:</b> Scan our UPI QR code, send payment, and wait for admin approval."
        )
    else:
        payment_text = f"🛠️ Please click the button below to make a <b>MANUAL PAYMENT</b> using the QR code."

    markup.add(InlineKeyboardButton("✏️ MANUAL PAY (UPI QR)", callback_data=f"manual_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 CONTACT ADMIN", url=f"https://t.me/{CONTACT_USERNAME}"))

    readable_plan = format_clean_duration(mins)

    bot.send_message(
        call.message.chat.id,
        f"🛒 <b>CHOOSE PAYMENT METHOD</b>\n\n"
        f"<b>CHANNEL:</b> {ch_data['name']}\n"
        f"<b>PLAN:</b> {readable_plan}\n"
        f"<b>PRICE:</b> ₹{price}\n\n"
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
    markup.add(InlineKeyboardButton("✅ I HAVE PAID (VERIFY)", callback_data=f"paid_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 CONTACT ADMIN", url=f"https://t.me/{CONTACT_USERNAME}"))
    
    readable_plan = format_clean_duration(mins)

    bot.send_photo(
        call.message.chat.id, 
        qr_url, 
        caption=f"📝 <b>MANUAL PAYMENT SETUP</b>\n\n"
                f"<b>PLAN:</b> {readable_plan}\n"
                f"<b>PRICE:</b> ₹{price}\n"
                f"<b>UPI ID:</b> <code>{UPI_ID}</code>\n\n"
                f"Please scan this QR code, complete your transaction, and then click **'I HAVE PAID (VERIFY)'** to send your proof.", 
        reply_markup=markup, 
        parse_mode="HTML"
    )

# --- SCREENSHOT SOLICITATION ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def ask_for_screenshot(call):
    bot.answer_callback_query(call.id)
    _, ch_id, mins = call.data.split('_')
    
    # User inline keyboard for direct Cancel action
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ CANCEL", callback_data=f"cancel_upload_{ch_id}"))

    msg = bot.send_message(
        call.message.chat.id, 
        "📷 <b>SUBMIT SCREENSHOT PROOF</b>\n\n"
        "Please **send/upload the screenshot receipt** of your payment here to complete verification.\n\n"
        "<i>Make sure the transaction ID/UTR is visible on the screenshot.</i>\n\n"
        "👉 <i>If you want to cancel, please click the Cancel button below or type 'cancel'.</i>", 
        reply_markup=markup,
        parse_mode="HTML"
    )
    # Register next step handler to receive image
    bot.register_next_step_handler(msg, receive_screenshot, int(ch_id), int(mins))

# --- CANCEL CALLBACK HANDLER ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_upload_'))
def cancel_upload_receipt(call):
    bot.answer_callback_query(call.id)
    ch_id = int(call.data.split('_')[2])
    
    # Clear active step listeners immediately
    bot.clear_step_handlers_by_chat_id(chat_id=call.message.chat.id)
    
    bot.send_message(
        call.message.chat.id, 
        "❌ <b>Process Cancelled.</b>\nYour payment submission request has been discarded. Going back to plans menu...",
        parse_mode="HTML"
    )
    # Return user back to plans list
    show_plans_menu(call.message.chat.id, ch_id)

# --- RESOLVED STEP HANDLER & CANCEL BYPASS ---

def receive_screenshot(message, ch_id, mins):
    # Bypass 1: If user manually typing 'cancel' or '/cancel'
    if message.text and message.text.lower() in ['/cancel', 'cancel']:
        bot.clear_step_handlers_by_chat_id(chat_id=message.chat.id)
        bot.send_message(message.chat.id, "❌ <b>Process Cancelled.</b> Going back to plans menu...", parse_mode="HTML")
        show_plans_menu(message.chat.id, ch_id)
        return

    # Check if user sent a photo
    if not message.photo:
        # Create a retry screen with Cancel option
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("❌ CANCEL", callback_data=f"cancel_upload_{ch_id}"))
        
        msg = bot.send_message(
            message.chat.id, 
            "❌ <b>Error:</b> You didn't send a photo. Please send a valid receipt image or click **CANCEL** below.",
            reply_markup=markup,
            parse_mode="HTML"
        )
        # Re-register so next message triggers this same receiver
        bot.register_next_step_handler(msg, receive_screenshot, ch_id, mins)
        return

    user = message.from_user
    ch_data = channels_col.find_one({"channel_id": ch_id})
    price = ch_data['plans'][str(mins)]
    readable_plan = format_clean_duration(mins)

    # Admin Control Buttons
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ APPROVE", callback_data=f"app_{user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ REJECT", callback_data=f"rej_{user.id}"))

    file_id = message.photo[-1].file_id

    # Send receipt details to admin
    bot.send_photo(
        ADMIN_ID,
        file_id,
        caption=f"⚠️ <b>MANUAL PAYMENT VERIFICATION REQUIRED!</b>\n\n"
                f"<b>USER:</b> {user.first_name} (@{user.username if user.username else 'No_Username'})\n"
                f"<b>CHANNEL:</b> {ch_data['name']}\n"
                f"<b>PLAN:</b> {readable_plan}\n"
                f"<b>PRICE:</b> ₹{price}\n\n"
                f"<i>Please verify the attached receipt screenshot and take action below:</i>",
        reply_markup=markup,
        parse_mode="HTML"
    )

    u_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📞 CONTACT ADMIN", url=f"https://t.me/{CONTACT_USERNAME}"))
    bot.send_message(
        message.chat.id, 
        "✅ <b>Screenshot Receipt Received!</b>\n\n"
        "Your payment proof is successfully sent to the Admin. Please wait while we verify your transaction.", 
        reply_markup=u_markup,
        parse_mode="HTML"
    )

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
        
        readable_plan = format_clean_duration(mins)

        bot.send_message(
            u_id, 
            f"🥳 <b>PAYMENT APPROVED (MANUAL)!</b>\n\n"
            f"SUBSCRIPTION: {readable_plan}\n\n"
            f"JOIN LINK: {link.invite_link}\n\n"
            f"⚠️ <b>NOTE:</b> ACCESS LINK WILL EXPIRE IN {readable_plan}.", 
            parse_mode="HTML"
        )
        bot.edit_message_caption(f"✅ Approved user {u_id} for {readable_plan}.", call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error while approving: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def reject_now(call):
    bot.answer_callback_query(call.id)
    u_id = int(call.data.split('_')[1])
    try:
        bot.send_message(u_id, "❌ <b>PAYMENT REJECTED!</b>\n\nYour manual payment verification failed. Please contact the admin.", parse_mode="HTML")
        bot.edit_message_caption(f"❌ Rejected user {u_id} request.", call.message.chat.id, call.message.message_id)
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
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔄 RE-JOIN / RENEW", url=rejoin_url))
            
            bot.send_message(user['user_id'], "⚠️ YOUR SUBSCRIPTION HAS EXPIRED.\n\nTO JOIN AGAIN OR RENEW, PLEASE CLICK THE BUTTON BELOW:", reply_markup=markup)
            users_col.delete_one({"_id": user['_id']})
        except Exception as e: 
            print(f"Error kicking user {user['user_id']}: {e}")

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
