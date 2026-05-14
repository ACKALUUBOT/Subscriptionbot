import os
import json
import telebot
import razorpay
import urllib.parse
from flask import Flask, request, abort
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from threading import Thread

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

RZP_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RZP_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
RZP_WEBHOOK_SECRET = os.getenv('RZP_WEBHOOK_SECRET')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']
rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

app = Flask('')

# --- UTILS ---
def get_time_string(mins):
    mins = int(mins)
    if mins < 60: return f"{mins} Min"
    if mins < 1440: return f"{mins//60} Hours"
    return f"{mins//1440} Days"

def approve_user_logic(u_id, ch_id, mins, method="Automatic"):
    user_record = users_col.find_one({"user_id": u_id, "channel_id": ch_id})
    now = datetime.now()
    base_time = datetime.fromtimestamp(user_record['expiry']) if user_record and user_record['expiry'] > now.timestamp() else now
    new_expiry = base_time + timedelta(minutes=mins)

    try:
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=int(new_expiry.timestamp()))
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": new_expiry.timestamp()}}, upsert=True)
        
        msg_text = (
            f"🥳 <b>Subscription Activated!</b>\n\n"
            f"<b>Method:</b> {method}\n"
            f"<b>Plan:</b> {get_time_string(mins)}\n"
            f"<b>Expires:</b> {new_expiry.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"🔗 <b>Join Link:</b> {link.invite_link}"
        )
        bot.send_message(u_id, msg_text, parse_mode="HTML")
        bot.send_message(ADMIN_ID, f"✅ <b>Approved:</b> User <code>{u_id}</code> via {method}", parse_mode="HTML")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ <b>Approval Error:</b>\n<code>{str(e)}</code>", parse_mode="HTML")

# --- RAZORPAY WEBHOOK ---
@app.route('/razorpay_webhook', methods=['POST'])
def razorpay_webhook():
    webhook_signature = request.headers.get('X-Razorpay-Signature')
    payload = request.data
    try:
        rzp_client.utility.verify_webhook_signature(payload.decode('utf-8'), webhook_signature, RZP_WEBHOOK_SECRET)
        data = json.loads(payload)
        if data['event'] == 'payment.captured':
            notes = data['payload']['payment']['entity']['notes']
            approve_user_logic(int(notes['user_id']), int(notes['channel_id']), int(notes['mins']), "Razorpay")
    except: abort(400)
    return 'OK', 200

# --- BOT HANDLERS ---
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
                    markup.add(InlineKeyboardButton(f"💳 {get_time_string(p_time)} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
                if ch_data.get('demo_link'):
                    markup.add(InlineKeyboardButton("📺 View Demo", url=ch_data['demo_link']))
                bot.send_message(message.chat.id, f"💎 <b>Welcome to {ch_data['name']}</b>", reply_markup=markup, parse_mode="HTML")
                return
        except: pass

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 <b>Admin Panel</b>\n/add - Setup Channel\n/channels - Manage", parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def payment_choice(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⚡ Online (Instant)", callback_data=f"rzp_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📸 Manual UPI (Screenshot)", callback_data=f"man_{ch_id}_{mins}"))
    bot.edit_message_text("<b>Choose Payment Method:</b>", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rzp_'))
def rzp_pay(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = int(ch_data['plans'][mins]) * 100 
    order = rzp_client.order.create(data={'amount': price, 'currency': 'INR', 'payment_capture': 1, 'notes': {'user_id': str(call.from_user.id), 'channel_id': str(ch_id), 'mins': str(mins)}})
    pay_url = f"https://api.razorpay.com/v1/checkout/embedded?key_id={RZP_KEY_ID}&order_id={order['id']}"
    bot.send_message(call.message.chat.id, "⚡ <b>Secure Razorpay Link:</b>", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Pay Online", url=pay_url)), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('man_'))
def manual_pay(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    # FIXED QR LOGIC (Encoding UPI String)
    upi_string = f"upi://pay?pa={UPI_ID}&am={price}&cu=INR&tn=Subscription"
    encoded_upi = urllib.parse.quote(upi_string)
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_upi}"
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr_url, caption=f"💰 <b>Pay: ₹{price}</b>\nUPI ID: <code>{UPI_ID}</code>\n\nPay via QR and upload screenshot.", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def get_ss(call):
    _, ch_id, mins = call.data.split('_')
    msg = bot.send_message(call.message.chat.id, "📸 Send the payment screenshot now.")
    bot.register_next_step_handler(msg, process_manual_ss, ch_id, mins)

def process_manual_ss(message, ch_id, mins):
    if message.content_type != 'photo': return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{message.from_user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{message.from_user.id}"))
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"📸 <b>Manual Request</b>\nUser: <code>{message.from_user.id}</code>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def manual_approve(call):
    _, u_id, ch_id, mins = call.data.split('_')
    approve_user_logic(int(u_id), int(ch_id), int(mins), "Manual Admin Approval")
    bot.edit_message_caption("✅ Approved Successfully", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def manual_reject(call):
    u_id = int(call.data.split('_')[1])
    bot.send_message(u_id, "❌ <b>Rejected:</b> Payment not verified.", parse_mode="HTML")
    bot.edit_message_caption("❌ Rejected", call.message.chat.id, call.message.message_id)

# --- ADMIN SETUP ---
@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_start(message):
    msg = bot.send_message(ADMIN_ID, "Forward a message from the channel.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id, ch_name = message.forward_from_chat.id, message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"✅ {ch_name}\nEnter Plans: <code>Min:Price, Min:Price</code>", parse_mode="HTML")
        bot.register_next_step_handler(msg, get_demo, ch_id, ch_name)

def get_demo(message, ch_id, ch_name):
    plans = {p.split(':')[0].strip(): p.split(':')[1].strip() for p in message.text.split(',')}
    msg = bot.send_message(ADMIN_ID, "Enter Demo Link (or 'none')")
    bot.register_next_step_handler(msg, finalize, ch_id, ch_name, plans)

def finalize(message, ch_id, ch_name, plans):
    demo = None if message.text.lower() == 'none' else message.text
    channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans, "demo_link": demo, "admin_id": ADMIN_ID}}, upsert=True)
    bot.send_message(ADMIN_ID, "✅ <b>Setup Finished!</b>", parse_mode="HTML")

# --- BACKGROUND TASKS ---
def check_expiries():
    expired = users_col.find({"expiry": {"$lte": datetime.now().timestamp()}})
    for user in expired:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            users_col.delete_one({"_id": user['_id']})
        except: pass

@app.route('/')
def home(): return "Bot Running"

if __name__ == '__main__':
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))).start()
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_expiries, 'interval', minutes=1)
    scheduler.start()
    bot.infinity_polling()
