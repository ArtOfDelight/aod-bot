import re
import math
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ConversationHandler, ContextTypes
)

# === States ===
ASK_ACTION, ASK_PHONE, ASK_LOCATION = range(3)

# === Phone to Employee ID Mapping ===
PHONE_TO_EMP_ID = {
    "9739506907": "AOD000",
    "9886036350": "AOD001",
    "8217353561": "AOD002",
    "9362425804": "AOD003",
    "9148864983": "AOD004",
    "7795716831": "AOD005",
    "9362271551": "AOD006",
    "9362333165": "AOD007",
    "8766986995": "AOD008",
    "9863209553": "AOD009",
    "9366497128": "AOD011",
    "6009256086": "AOD012",
    "6363827367": "AOD013",
    "8837079426": "AOD014", 
    "9609258507": "AOD015",
    "8798300484": "AOD016",
    "9362086831": "AOD017",
    "8770662766": "AOD018"
}

# === Google Sheets Setup ===
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDS_FILE = "service_account.json"
SHEET_NAME = "AOD Master App"
TAB_NAME_ROSTER = "Roster"
TAB_NAME_OUTLETS = "Outlets"

# === Location Tolerance ===
LOCATION_TOLERANCE_METERS = 50

# === Helpers ===

def normalize_number(number):
    return re.sub(r"\D", "", number)[-10:]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_outlet_row_by_emp_id(emp_id):
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
    records = sheet.get_all_records()

    for idx, row in enumerate(records, start=2):
        if str(row.get("Employee ID")).strip() == emp_id:
            outlet = str(row.get("Outlet")).strip()
            signin = row.get("Sign-In Time")
            signout = row.get("Sign-Out Time")
            return outlet, signin, signout, idx, sheet
    return None, None, None, None, None

def get_outlet_coordinates(outlet_code):
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
    records = sheet.get_all_records()

    for row in records:
        if str(row.get("Outlet Code")).strip().lower() == outlet_code.lower():
            loc = str(row.get("Outlet Location")).strip()
            try:
                lat_str, lng_str = loc.split(",")
                return float(lat_str), float(lng_str)
            except:
                return None, None
    return None, None

def update_sheet(sheet, row, column_name, timestamp):
    col_index = sheet.row_values(1).index(column_name) + 1
    sheet.update_cell(row, col_index, timestamp)

# === Bot Handlers ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["message_ids"] = [update.message.message_id]  # Track /start command
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Sign In", callback_data="signin")],
        [InlineKeyboardButton("🔴 Sign Out", callback_data="signout")]
    ])
    msg = await update.message.reply_text("Welcome! What would you like to do today?", reply_markup=buttons)
    context.user_data["message_ids"].append(msg.message_id)
    return ASK_ACTION

async def action_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    context.user_data["action"] = action
    context.user_data["message_ids"].append(query.message.message_id)

    contact_button = KeyboardButton("📱 Send Phone Number", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
    msg = await query.message.reply_text("Please verify your phone number:", reply_markup=markup)
    context.user_data["message_ids"].append(msg.message_id)
    return ASK_PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["message_ids"].append(update.message.message_id)
    if not update.message.contact:
        msg = await update.message.reply_text("❌ Please send your phone number using the button.")
        context.user_data["message_ids"].append(msg.message_id)
        return ASK_PHONE

    phone = normalize_number(update.message.contact.phone_number)
    emp_id = PHONE_TO_EMP_ID.get(phone)
    if not emp_id:
        msg = await update.message.reply_text("❌ Number not registered.", reply_markup=ReplyKeyboardRemove())
        context.user_data["message_ids"].append(msg.message_id)
        return ConversationHandler.END

    outlet, signin, signout, row, sheet = get_outlet_row_by_emp_id(emp_id)
    if not outlet:
        msg = await update.message.reply_text("❌ No outlet found for your ID.", reply_markup=ReplyKeyboardRemove())
        context.user_data["message_ids"].append(msg.message_id)
        return ConversationHandler.END

    action = context.user_data.get("action")
    if action == "signin" and signin:
        msg = await update.message.reply_text("✅ You have already signed in today.", reply_markup=ReplyKeyboardRemove())
        context.user_data["message_ids"].append(msg.message_id)
        return ConversationHandler.END
    if action == "signout":
        if not signin:
            msg = await update.message.reply_text("❌ You must sign in before signing out.", reply_markup=ReplyKeyboardRemove())
            context.user_data["message_ids"].append(msg.message_id)
            return ConversationHandler.END
        if signout:
            msg = await update.message.reply_text("✅ You have already signed out today.", reply_markup=ReplyKeyboardRemove())
            context.user_data["message_ids"].append(msg.message_id)
            return ConversationHandler.END

    context.user_data.update({
        "emp_id": emp_id,
        "outlet_code": outlet,
        "sheet": sheet,
        "row": row,
    })

    location_button = KeyboardButton("📍 Send Location", request_location=True)
    markup = ReplyKeyboardMarkup([[location_button]], one_time_keyboard=True, resize_keyboard=True)
    msg = await update.message.reply_text(f"Your Outlet for today is: {outlet}. Please share your current location:", reply_markup=markup)
    context.user_data["message_ids"].append(msg.message_id)
    return ASK_LOCATION

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["message_ids"].append(update.message.message_id)
    if not update.message.location:
        msg = await update.message.reply_text("❌ Please send your live location.")
        context.user_data["message_ids"].append(msg.message_id)
        return ASK_LOCATION

    user_lat = update.message.location.latitude
    user_lng = update.message.location.longitude
    outlet_code = context.user_data.get("outlet_code")
    sheet = context.user_data.get("sheet")
    row = context.user_data.get("row")

    outlet_lat, outlet_lng = get_outlet_coordinates(outlet_code)
    if not outlet_lat:
        msg = await update.message.reply_text("❌ No coordinates set for your outlet.", reply_markup=ReplyKeyboardRemove())
        context.user_data["message_ids"].append(msg.message_id)
        return ConversationHandler.END

    dist = haversine(user_lat, user_lng, outlet_lat, outlet_lng)
    if dist > LOCATION_TOLERANCE_METERS:
        msg = await update.message.reply_text(
            f"❌ You are too far from outlet ({int(dist)} meters).",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["message_ids"].append(msg.message_id)
        return ConversationHandler.END

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    action = context.user_data.get("action")
    col = "Sign-In Time" if action == "signin" else "Sign-Out Time"
    update_sheet(sheet, row, col, timestamp)

    # Send confirmation message
    msg = await update.message.reply_text(
        f"✅ {action.replace('sign', 'Sign ').title()} successful.\n📍 Distance from outlet: {int(dist)} meters.",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["message_ids"].append(msg.message_id)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["message_ids"].append(update.message.message_id)
    msg = await update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    context.user_data["message_ids"].append(msg.message_id)
    return ConversationHandler.END

# === Main ===

def main():
    app = ApplicationBuilder().token("7571822429:AAFFBPQKzBwFWGkMC0R8UMJF6JrAgj8-5ZE").build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_ACTION: [CallbackQueryHandler(action_selected)],
            ASK_PHONE: [MessageHandler(filters.CONTACT, handle_phone)],
            ASK_LOCATION: [MessageHandler(filters.LOCATION, handle_location)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()