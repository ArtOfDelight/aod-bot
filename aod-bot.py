# === Import modules ===
import os
import re
import math
import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request
from telegram import (
    Bot, Update, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler,
    CallbackQueryHandler, Filters, ConversationHandler
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === CONFIGURATION ===
BOT_TOKEN = "7571822429:AAFFBPQKzBwFWGkMC0R8UMJF6JrAgj8-5ZE"
WEBHOOK_URL = "https://aod-bot-t2ux.onrender.com"
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDS_FILE = "service_account.json"
SHEET_NAME = "AOD Master App"
TAB_NAME_ROSTER = "Roster"
TAB_NAME_OUTLETS = "Outlets"
TAB_NAME_EMP_REGISTER = "EmployeeRegister"
LOCATION_TOLERANCE_METERS = 50

# === Flask + Telegram Setup ===
app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=4)

# === States ===
ASK_ACTION, ASK_PHONE, ASK_LOCATION = range(3)

# === Utility Functions ===
def normalize_number(number):
    return re.sub(r"\D", "", number)[-10:]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(delta_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def get_phone_to_empid_map():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    sheet = gspread.authorize(creds).open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)
    records = sheet.get_all_records()
    return {
        re.sub(r"\D", "", str(row.get("Phone Number", "")))[-10:]: str(row.get("Employee ID", "")).strip()
        for row in records if row.get("Phone Number") and row.get("Employee ID")
    }

def get_outlet_row_by_emp_id(emp_id):
    now = datetime.datetime.now(ZoneInfo("Asia/Kolkata"))
    
    # If it's before 4 AM, use yesterday's date
    if now.hour < 4:
        target_date = (now - datetime.timedelta(days=1)).strftime("%d/%m/%Y")
    else:
        target_date = now.strftime("%d/%m/%Y")

    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    sheet = gspread.authorize(creds).open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
    records = sheet.get_all_records()

    for idx, row in enumerate(records, start=2):  # start=2 accounts for header row
        if str(row.get("Employee ID")).strip() == emp_id and str(row.get("Date")).strip() == target_date:
            return str(row.get("Outlet")).strip(), row.get("Sign-In Time"), row.get("Sign-Out Time"), idx, sheet

    return None, None, None, None, None

def get_outlet_coordinates(outlet_code):
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    sheet = gspread.authorize(creds).open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
    records = sheet.get_all_records()
    for row in records:
        if str(row.get("Outlet Code")).strip().lower() == outlet_code.lower():
            try:
                lat_str, lng_str = str(row.get("Outlet Location")).strip().split(",")
                return float(lat_str), float(lng_str)
            except:
                return None, None
    return None, None

def update_sheet(sheet, row, column_name, timestamp):
    col_index = sheet.row_values(1).index(column_name) + 1
    sheet.update_cell(row, col_index, timestamp)

# === Bot Handlers ===
def start(update: Update, context):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Sign In", callback_data="signin")],
        [InlineKeyboardButton("🔴 Sign Out", callback_data="signout")]
    ])
    update.message.reply_text("Welcome! What would you like to do today?", reply_markup=buttons)
    return ASK_ACTION

def action_selected(update: Update, context):
    query = update.callback_query
    query.answer()
    context.user_data["action"] = query.data
    contact_button = KeyboardButton("📱 Send Phone Number", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
    query.message.reply_text("Please verify your phone number:", reply_markup=markup)
    return ASK_PHONE

def handle_phone(update: Update, context):
    if not update.message.contact:
        update.message.reply_text("❌ Please send your phone number using the button.")
        return ASK_PHONE
    phone = normalize_number(update.message.contact.phone_number)
    emp_id = get_phone_to_empid_map().get(phone)
    if not emp_id:
        update.message.reply_text("❌ Number not registered.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    outlet, signin, signout, row, sheet = get_outlet_row_by_emp_id(emp_id)
    if not outlet:
        update.message.reply_text("❌ No outlet found for your ID or not scheduled today.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    action = context.user_data["action"]
    if action == "signin" and signin:
        update.message.reply_text("✅ Already signed in today.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if action == "signout":
        if not signin:
            update.message.reply_text("❌ You must sign in before signing out.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        if signout:
            update.message.reply_text("✅ Already signed out today.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
    context.user_data.update({"emp_id": emp_id, "outlet_code": outlet, "sheet": sheet, "row": row})
    loc_button = KeyboardButton("📍 Send Location", request_location=True)
    markup = ReplyKeyboardMarkup([[loc_button]], one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text(f"Your Outlet for today is: {outlet}. Please share your location:", reply_markup=markup)
    return ASK_LOCATION

def handle_location(update: Update, context):
    if not update.message.location:
        update.message.reply_text("❌ Please send your live location.")
        return ASK_LOCATION

    user_lat, user_lng = update.message.location.latitude, update.message.location.longitude
    outlet_lat, outlet_lng = get_outlet_coordinates(context.user_data["outlet_code"])

    if not outlet_lat:
        update.message.reply_text("❌ No coordinates set for this outlet.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    dist = haversine(user_lat, user_lng, outlet_lat, outlet_lng)
    if dist > LOCATION_TOLERANCE_METERS:
        update.message.reply_text(f"❌ You are too far from outlet ({int(dist)} meters).", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    now = datetime.datetime.now(ZoneInfo("Asia/Kolkata"))
    action = context.user_data["action"]
    column = "Sign-In Time" if action == "signin" else "Sign-Out Time"

    # ✅ Updated: Use current timestamp (not roster date) even after 4 AM logic
    if action == "signout":
        sign_in_str = context.user_data["sheet"].cell(
            context.user_data["row"], context.user_data["sheet"].row_values(1).index("Sign-In Time") + 1
        ).value
        try:
            sign_in_time = datetime.datetime.strptime(sign_in_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        except:
            update.message.reply_text("❌ Error reading Sign-In Time. Please contact admin.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        if now < (sign_in_time + datetime.timedelta(days=1, hours=5 - sign_in_time.hour)):
            timestamp = now.strftime("%Y-%m-%d %H:%M:%S")  # ✅ Use now, not sign_in_time date
        else:
            timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    else:
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    update_sheet(context.user_data["sheet"], context.user_data["row"], column, timestamp)

    update.message.reply_text(
        f"✅ {action.replace('sign', 'Sign ').title()} successful.\n📍 Distance: {int(dist)} meters.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def cancel(update: Update, context):
    update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def reset(update: Update, context):
    update.message.reply_text("🔁 Reset successful. You can now use /start again.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# === Dispatcher & Webhook ===

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

def setup_dispatcher():
    dispatcher.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_ACTION: [CallbackQueryHandler(action_selected)],
            ASK_PHONE: [MessageHandler(Filters.contact, handle_phone)],
            ASK_LOCATION: [MessageHandler(Filters.location, handle_location)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("reset", reset)
        ],
    ))
    dispatcher.add_handler(CommandHandler("reset", reset))

    # ✅ ONLY show /reset in Telegram menu
    bot.set_my_commands([
        ("reset", "Reset the conversation")
    ])

def set_webhook():
    bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
    print(f"✅ Webhook set at {WEBHOOK_URL}{WEBHOOK_PATH}")

# === Main Entry Point ===
setup_dispatcher()
set_webhook()
