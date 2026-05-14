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

# Razorpay (Optional)
RZP_KEY_ID = os.getenv('')
RZP_KEY_SECRET = os.getenv('')
RZP_WEBHOOK_SECRET = os.getenv('')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

# Razorpay Setup (Condition check)
rzp_client = None
if RZP_KEY_ID and RZP_KEY_SECRET:
    rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET))

app = Flask('')

# --- CORE UTILS ---
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
            f"<b>Plan:</b> {get_time_string(mins)}\n"
            f"<b>Expires:</b> {new_expiry.strftime('%Y-%m-%d %H:%M')}\n"
            f"<b>Method:</b> {method}\n\n"
            f"🔗 <b>Join Link:</b> {link.invite_link}"
        )
        bot.send_message(u_id, msg_text, parse_mode="HTML")
        bot.send_message(ADMIN_ID, f"✅ <b>Approved:</b> User <code>{u_id}</code> via {method}", parse_mode="HTML")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ <b>Approval Error:</b> {str(e)}", parse_mode="HTML")

# --- WEBHOOK FOR RAZORPAY ---
@app.route('/razorpay_webhook', methods=['POST'])
def razorpay_webhook():
    if not RZP_WEBHOOK_SECRET: abort(400)
    webhook_signature = request.headers.get('X-Razorpay-Signature')
    payload = request.data
    try:
        rzp_client.utility.verify_webhook_signature(payload.decode('utf-8'), webhook_signature, RZP_WEBHOOK_SECRET)
        data = json.loads(payload)
        if data['event'] == 'payment.captured':
            notes = data['payload']['payment']['entity']['notes']
            approve_user_logic(int(notes['user_id']), int(notes['channel_id']), int(notes['mins']), "Razorpay Online")
    except: abort(400)
    return 'OK', 200

# --- USER COMMANDS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()

    # Deep Link Entry
    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                for p_time, p_price in ch_data['plans'].items():
                    markup.add(InlineKeyboardButton(f"💳 {get_time_string(p_time)} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
                if ch_data.get('demo_link'):
                    markup.add(InlineKeyboardButton("📺 View Quality Demo", url=ch_data['demo_link']))
                
                bot.send_message(message.chat.id, f"💎 <b>Premium Access: {ch_data['name']}</b>\n\nNiche diye gaye plans mein se ek select karein:", reply_markup=markup, parse_mode="HTML")
                return
        except: pass

    # Normal Start
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 My Plan", callback_data="my_plan"),
        InlineKeyboardButton("📞 Support", url=f"https://t.me/{CONTACT_USERNAME}")
    )
    
    bot.send_message(message.chat.id, f"👋 <b>Welcome {message.from_user.first_name}!</b>\nMain aapki premium subscriptions manage karta hoon. Join karne ke liye official link use karein.", reply_markup=markup, parse_mode="HTML")

    if user_id == ADMIN_ID:
        bot.send_message(ADMIN_ID, "🛠 <b>Admin Menu:</b>\n/add - Add Channel\n/channels - Manage\n/stats - Check Users", parse_mode="HTML")

@bot.message_handler(commands=['myplan'])
@bot.callback_query_handler(func=lambda call: call.data == "my_plan")
def my_plan(message):
    # Handle both command and callback
    u_id = message.from_user.id if hasattr(message, 'from_user') else message.message.chat.id
    subs = list(users_col.find({"user_id": u_id}))
    
    if not subs:
        bot.send_message(u_id, "❌ <b>Aapka koi active plan nahi hai.</b>", parse_mode="HTML")
        return

    res = "👤 <b>Aapka Dashboard</b>\n\n"
    for s in subs:
        ch = channels_col.find_one({"channel_id": s['channel_id']})
        name = ch['name'] if ch else "Unknown"
        expiry = datetime.fromtimestamp(s['expiry']).strftime('%d %b %Y')
        res += f"📺 <b>{name}</b>\n⌛ Valid Till: {expiry}\n\n"
    
    bot.send_message(u_id, res, parse_mode="HTML")

# --- ADMIN: REMOVE USER ACCESS ---
@bot.message_handler(commands=['remove'], func=lambda m: m.from_user.id == ADMIN_ID)
def remove_user_start(message):
    msg = bot.send_message(ADMIN_ID, "👤 <b>User ko remove karein:</b>\n\nUs user ki <b>ID</b> bhejein jiska access aap khatam karna chahte hain (ya /cancel):", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_remove_user)

def process_remove_user(message):
    if message.text == '/cancel':
        bot.send_message(ADMIN_ID, "❌ Action cancelled.")
        return
    
    try:
        u_id = int(message.text)
        # Database se user ke saare subscriptions delete karein
        result = users_col.delete_many({"user_id": u_id})
        
        if result.deleted_count > 0:
            bot.send_message(ADMIN_ID, f"✅ <b>Success!</b>\nUser <code>{u_id}</code> ke saare plans database se hata diye gaye hain.", parse_mode="HTML")
            try:
                bot.send_message(u_id, "⚠️ <b>Access Revoked:</b> Aapka subscription admin dwara khatam kar diya gaya hai.", parse_mode="HTML")
            except: pass
        else:
            bot.send_message(ADMIN_ID, "❓ Is ID ka koi active subscription nahi mila.")
    except ValueError:
        bot.send_message(ADMIN_ID, "❌ Invalid ID! Sirf numbers bhejein.")
        
# --- PAYMENT SELECTION ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def pay_choice(call):
    _, ch_id, mins = call.data.split('_')
    markup = InlineKeyboardMarkup()
    
    # Razorpay button only if ID exists
    if RZP_KEY_ID:
        markup.add(InlineKeyboardButton("⚡ Online Pay (Instant)", callback_data=f"rzp_{ch_id}_{mins}"))
    
    markup.add(InlineKeyboardButton("📸 Manual Pay (Screenshot)", callback_data=f"man_{ch_id}_{mins}"))
    
    bot.edit_message_text("<b>Payment Method Select Karein:</b>", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rzp_'))
def rzp_pay(call):
    if not rzp_client:
        bot.answer_callback_query(call.id, "Online payment currently disabled.")
        return
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = int(ch_data['plans'][mins]) * 100 
    
    order = rzp_client.order.create(data={
        'amount': price, 'currency': 'INR', 'payment_capture': 1,
        'notes': {'user_id': str(call.from_user.id), 'channel_id': str(ch_id), 'mins': str(mins)}
    })
    
    pay_url = f"https://api.razorpay.com/v1/checkout/embedded?key_id={RZP_KEY_ID}&order_id={order['id']}"
    bot.send_message(call.message.chat.id, "💳 <b>Secure Payment:</b>", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Pay Now", url=pay_url)), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('man_'))
def manual_pay(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    upi_string = f"upi://pay?pa={UPI_ID}&am={price}&cu=INR&tn=Sub"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(upi_string)}"
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{mins}"))
    bot.send_photo(call.message.chat.id, qr_url, caption=f"💰 <b>Pay: ₹{price}</b>\nUPI: <code>{UPI_ID}</code>\n\nScreenshot upload karein.", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def req_ss(call):
    msg = bot.send_message(call.message.chat.id, "📸 Payment screenshot bhejein.")
    bot.register_next_step_handler(msg, process_manual_ss, call.data.split('_')[1], call.data.split('_')[2])

def process_manual_ss(message, ch_id, mins):
    if message.content_type != 'photo': return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{message.from_user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{message.from_user.id}"))
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"📸 <b>Manual Request</b>\nUser: <code>{message.from_user.id}</code>", reply_markup=markup, parse_mode="HTML")

# --- ADMIN: MANAGEMENT FEATURE ---
@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    cursor = channels_col.find({"admin_id": ADMIN_ID})
    markup = InlineKeyboardMarkup()
    for ch in cursor:
        markup.add(InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"manage_{ch['channel_id']}"))
    bot.send_message(ADMIN_ID, "📑 <b>Managed Channels:</b>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_ch(call):
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    link = f"https://t.me/{bot.get_me().username}?start={ch_id}"
    text = (
        f"⚙️ <b>Settings:</b> {ch_data['name']}\n\n"
        f"🔗 <b>Invite Link:</b> <code>{link}</code>\n"
        f"📺 <b>Demo:</b> {ch_data.get('demo_link', 'None')}\n"
        f"💰 <b>Plans:</b> {ch_data['plans']}"
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")

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
    bot.send_message(ADMIN_ID, "✅ Setup Finished!", parse_mode="HTML")

# --- CALLBACKS FOR APPROVAL ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def manual_approve(call):
    _, u_id, ch_id, mins = call.data.split('_')
    approve_user_logic(int(u_id), int(ch_id), int(mins), "Manual Admin Approval")
    bot.edit_message_caption("✅ Approved", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def manual_reject(call):
    bot.send_message(int(call.data.split('_')[1]), "❌ <b>Payment Rejected.</b> Contact admin.", parse_mode="HTML")
    bot.edit_message_caption("❌ Rejected", call.message.chat.id, call.message.message_id)

# --- SYSTEM ---
def check_expiries():
    expired = users_col.find({"expiry": {"$lte": datetime.now().timestamp()}})
    for user in expired:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            users_col.delete_one({"_id": user['_id']})
        except: pass

@app.route('/')
def home(): return "Healthy"

if __name__ == '__main__':
    # Step 1: Purane updates ko clear karne ke liye drop_pending_updates=True yahan use karein
    try:
        print("Cleaning up old connections...")
        bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Cleanup error: {e}")
    
    # Step 2: Flask start karein
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))).start()
    
    # Step 3: Background Scheduler start karein
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_expiries, 'interval', minutes=1)
    scheduler.start()
    
    # Step 4: Bot polling (Yahan se error wala argument hata diya gaya hai)
    print("Bot is starting fresh...")
    bot.infinity_polling()
