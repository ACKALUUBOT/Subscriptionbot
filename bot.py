import os
import telebot
import urllib.parse
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from threading import Thread
from apscheduler.schedulers.background import BackgroundScheduler
import razorpay

# --- 1. CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = os.getenv('RAZORPAY_WEBHOOK_SECRET')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col, users_col, transactions_col = db['channels'], db['users'], db['transactions']

rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID else None

# --- 2. HELPERS & KEYBOARDS ---
def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    stories = list(channels_col.find())
    for i, s in enumerate(stories, start=1):
        markup.add(KeyboardButton(f"{i}. {s['name']} [ ₹{s['price']} ] Nᴇᴡ"))
    return markup

def process_approval(u_id, ch_id, mins):
    """User ko link bhejta hai aur database mein expiry set karta hai"""
    try:
        expiry_ts = int((datetime.now(timezone.utc) + timedelta(minutes=int(mins))).timestamp())
        link = bot.create_chat_invite_link(int(ch_id), member_limit=1, expire_date=expiry_ts)
        users_col.update_one({"user_id": int(u_id), "channel_id": int(ch_id)}, {"$set": {"expiry": expiry_ts}}, upsert=True)
        
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚀 Jᴏɪɴ Cʜᴀɴɴᴇʟ Nᴏᴡ", url=link.invite_link))
        bot.send_message(u_id, "✅ <b>Pᴀʏᴍᴇɴᴛ Vᴇʀɪғɪᴇᴅ!</b>\nYᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss ɪs ɴᴏᴡ ᴀᴄᴛɪᴠᴇ.", reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error in Approval: {e}")

# --- 3. ADMIN: DETAILED STORY ADDING (A-Z) ---
@bot.message_handler(commands=['add'])
def admin_add_start(message):
    if message.from_user.id != ADMIN_ID: return
    msg = bot.send_message(ADMIN_ID, "📤 <b>Sᴛᴇᴘ 1:</b> Fᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ᴛʜᴇ sᴛᴏʀʏ ᴄʜᴀɴɴᴇʟ:")
    bot.register_next_step_handler(msg, add_step_details)

def add_step_details(message):
    if not message.forward_from_chat:
        bot.send_message(ADMIN_ID, "❌ Pʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ғʀᴏᴍ ᴀ ᴄʜᴀɴɴᴇʟ!")
        return
    ch_id = message.forward_from_chat.id
    ch_name = message.forward_from_chat.title
    format_text = (
        "📝 <b>Sᴛᴇᴘ 2: Sᴇɴᴅ Dᴇᴛᴀɪʟs</b>\n\n"
        "Fᴏʀᴍᴀᴛ:\n<code>PhotoURL | Platform | Genre | Eps | OldPrice | NewPrice | Mins | Desc</code>"
    )
    msg = bot.send_message(ADMIN_ID, format_text, parse_mode="HTML")
    bot.register_next_step_handler(msg, save_final_story, ch_id, ch_name)

def save_final_story(message, ch_id, ch_name):
    try:
        p = [i.strip() for i in message.text.split('|')]
        photo, platform, genre, eps, old_p, new_p, mins, desc = p
        discount = int(((float(old_p) - float(new_p)) / float(old_p)) * 100)

        channels_col.update_one({"channel_id": ch_id}, {"$set": {
            "name": ch_name, "photo": photo, "platform": platform, "genre": genre,
            "eps": eps, "old_price": old_p, "price": new_p, "discount": discount,
            "mins": mins, "desc": desc
        }}, upsert=True)
        bot.send_message(ADMIN_ID, "✅ <b>Pʀᴇᴍɪᴜᴍ Sᴛᴏʀʏ Aᴅᴅᴇᴅ!</b>", reply_markup=get_main_menu(), parse_mode="HTML")
    except:
        bot.send_message(ADMIN_ID, "❌ <b>Fᴏʀᴍᴀᴛ Eʀʀᴏʀ!</b> Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")

# --- 4. USER SIDE: PREMIUM INTERFACE ---
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "👋 <b>Wᴇʟᴄᴏᴍᴇ! Sᴇʟᴇᴄᴛ ᴀ sᴛᴏʀʏ ᴛᴏ ʙᴇɢɪɴ:</b>", reply_markup=get_main_menu(), parse_mode="HTML")

@bot.message_handler(func=lambda message: " [ ₹" in message.text)
def handle_selection(message):
    try:
        name = message.text.split('. ')[1].split(' [')[0].strip()
        ch = channels_col.find_one({"name": name})
        if ch:
            caption = (
                f"🌟 <b>Sᴛᴏʀʏ: {ch['name']}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🎬 <b>Pʟᴀᴛғᴏʀᴍ:</b> {ch['platform']}\n🎭 <b>Gᴇɴʀᴇ:</b> {ch['genre']}\n"
                f"💰 <b>Pʀɪᴄᴇ:</b> <s>₹{ch['old_price']}</s> <b>₹{ch['price']}</b> ({ch['discount']}% OFF)\n"
                f"🔢 <b>Eᴘɪsᴏᴅᴇs:</b> {ch['eps']}\n⏳ <b>Vᴀʟɪᴅɪᴛʏ:</b> {ch['mins']} Mɪɴs\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📖 <b>Dᴇsᴄʀɪᴘᴛɪᴏɴ:</b>\n{ch['desc']}"
            )
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ CONFIRM", callback_data=f"conf_{ch['channel_id']}"))
            bot.send_photo(message.chat.id, ch['photo'], caption=caption, reply_markup=markup, parse_mode="HTML")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('conf_'))
def terms_page(call):
    ch_id = call.data.split('_')[1]
    ch = channels_col.find_one({"channel_id": int(ch_id)})
    text = (f"👤 <b>Usᴇʀ:</b> {call.from_user.first_name}\n🆔 <b>ID:</b> <code>{call.from_user.id}</code>\n"
            f"📖 <b>Sᴛᴏʀʏ: {ch['name']}</b>\n\n<b>Tᴇʀᴍs & Cᴏɴᴅɪᴛɪᴏɴs:</b>\n- No refunds.\n- 100% Quality.")
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("I Aᴄᴄᴇᴘᴛ", callback_data=f"paymeth_{ch_id}"))
    bot.edit_message_caption(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paymeth_'))
def payment_choice(call):
    ch_id = call.data.split('_')[1]
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("⚡ UPI QR (Mᴀɴᴜᴀʟ)", callback_data=f"uqr_{ch_id}"),
        InlineKeyboardButton("💳 Rᴀᴢᴏʀᴘᴀʏ (Aᴜᴛᴏ)", callback_data=f"arz_{ch_id}")
    )
    bot.edit_message_caption("🛠 <b>Sᴇʟᴇᴄᴛ Pᴀʏᴍᴇɴᴛ Mᴇᴛʜᴏᴅ:</b>", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

# --- 5. PAYMENT HANDLERS (RAZORPAY & MANUAL) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('arz_'))
def razor_pay(call):
    ch_id = int(call.data.split('_')[1])
    ch = channels_col.find_one({"channel_id": ch_id})
    if not rz_client: return
    
    order = rz_client.order.create({"amount": int(float(ch['price'])*100), "currency": "INR", "payment_capture": "1"})
    transactions_col.insert_one({"order_id": order['id'], "user_id": call.from_user.id, "channel_id": ch_id, "mins": ch['mins']})
    
    pay_url = f"https://api.razorpay.com/v1/checkout/embedded?key_id={RAZORPAY_KEY_ID}&order_id={order['id']}"
    bot.send_message(call.message.chat.id, f"💳 <b>Cᴏᴍᴘʟᴇᴛᴇ Pᴀʏᴍᴇɴᴛ:</b>\n[<a href='{pay_url}'>Cʟɪᴄᴋ Hᴇʀᴇ ᴛᴏ Pᴀʏ</a>]", parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('uqr_'))
def manual_qr(call):
    ch_id = int(call.data.split('_')[1])
    ch = channels_col.find_one({"channel_id": ch_id})
    upi = f"upi://pay?pa={UPI_ID}&pn=PremiumStore&am={ch['price']}&cu=INR"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(upi)}"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📤 UPLOAD PROOF", callback_data=f"up_{ch_id}"))
    bot.send_photo(call.message.chat.id, qr_url, caption=f"💰 Pᴀʏ ₹{ch['price']} ᴛᴏ <code>{UPI_ID}</code>", reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('up_'))
def ask_photo(call):
    msg = bot.send_message(call.message.chat.id, "📸 Sᴇɴᴅ sᴄʀᴇᴇɴsʜᴏᴛ ɴᴏᴡ:")
    bot.register_next_step_handler(msg, send_to_admin, call.data.split('_')[1])

def send_to_admin(message, ch_id):
    if not message.photo: return
    ch = channels_col.find_one({"channel_id": int(ch_id)})
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ APPROVE", callback_data=f"adm_app_{message.from_user.id}_{ch_id}_{ch['mins']}"),
        InlineKeyboardButton("❌ REJECT", callback_data=f"adm_rej_{message.from_user.id}")
    )
    bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"📑 Pʀᴏᴏғ\nUsᴇʀ: {message.from_user.id}\nSᴛᴏʀʏ: {ch['name']}", reply_markup=markup)
    bot.send_message(message.chat.id, "⌛ Wᴀɪᴛɪɴɢ ғᴏʀ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ...")

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_action(call):
    d = call.data.split('_')
    if d[1] == "app":
        process_approval(d[2], d[3], d[4])
        bot.edit_message_caption("✅ Approved", call.message.chat.id, call.message.message_id)
    else:
        bot.send_message(int(d[2]), "❌ Pᴀʏᴍᴇɴᴛ Rᴇᴊᴇᴄᴛᴇᴅ!")
        bot.edit_message_caption("❌ Rejected", call.message.chat.id, call.message.message_id)

# --- 6. AUTO-KICK & WEBHOOK ---
app = Flask('')
@app.route('/razorpay_webhook', methods=['POST'])
def webhook():
    data = request.json
    if data.get('event') == "payment.captured":
        order_id = data['payload']['payment']['entity']['order_id']
        tx = transactions_col.find_one({"order_id": order_id})
        if tx: process_approval(tx['user_id'], tx['channel_id'], tx['mins'])
    return jsonify({"status": "ok"}), 200

def auto_kick():
    now = datetime.now(timezone.utc).timestamp()
    for u in users_col.find({"expiry": {"$lte": now}}):
        try:
            bot.ban_chat_member(u['channel_id'], u['user_id'])
            bot.unban_chat_member(u['channel_id'], u['user_id'])
            users_col.delete_one({"_id": u['_id']})
        except: pass

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_kick, 'interval', minutes=1)
    scheduler.start()
    Thread(target=lambda: app.run(host='0.0.0.0', port=5000)).start()
    bot.infinity_polling()
