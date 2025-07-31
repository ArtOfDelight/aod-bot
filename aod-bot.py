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
import requests

MANAGER_CHAT_ID = 1225343546  # Replace with the actual Telegram chat ID
INDIA_TZ = ZoneInfo("Asia/Kolkata")

# === CONFIGURATION ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://aod-bot-t2ux.onrender.com"
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDS_FILE = "service_account.json"
SHEET_NAME = "AOD Master App"
TAB_NAME_ROSTER = "Roster"
TAB_NAME_OUTLETS = "Outlets"
TAB_NAME_EMP_REGISTER = "EmployeeRegister"
TAB_NAME_SHIFTS = "Shifts"
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

def send_attendance_report(update: Update, context, mode="signin_only"):
    try:
        now = datetime.datetime.now(INDIA_TZ)

        # Determine the report date
        if mode == "full_yesterday":
            report_date = (now - datetime.timedelta(days=1)).strftime("%d/%m/%Y")
        else:
            # For today's sign-in checks (before 4 AM, use yesterday)
            report_date = (now - datetime.timedelta(days=1) if now.hour < 4 else now).strftime("%d/%m/%Y")

        gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE))
        roster_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
        emp_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)

        roster = roster_sheet.get_all_records()
        emp_register = emp_sheet.get_all_records()

        # Map Employee ID → Short Name
        emp_id_to_name = {
            str(row.get("Employee ID")).strip(): row.get("Short Name", "Unnamed")
            for row in emp_register if row.get("Employee ID")
        }

        # Group records by outlet, excluding "WO"
        outlet_records = {}
        for row in roster:
            if str(row.get("Date", "")).strip() != report_date:
                continue

            emp_id = str(row.get("Employee ID", "")).strip()
            short_name = emp_id_to_name.get(emp_id, emp_id)
            outlet = row.get("Outlet", "").strip()
            
            # Skip records for outlet "WO" (case-insensitive)
            if outlet.lower() == "wo":
                continue

            signin = str(row.get("Sign-In Time", "")).strip()
            signout = str(row.get("Sign-Out Time", "")).strip()
            start_time_str = str(row.get("Start Time", "")).strip() or "N/A"

            if mode == "signin_only" and not signin:
                # Parse start time and compare with current time
                if start_time_str != "N/A":
                    try:
                        # Convert start time string (e.g., "09:30:00") to datetime for comparison
                        start_time = datetime.datetime.strptime(start_time_str, "%H:%M:%S").time()
                        current_time = now.time()  # Get only the time portion of now
                        if start_time <= current_time:  # Include if start time has passed
                            if outlet not in outlet_records:
                                outlet_records[outlet] = []
                            outlet_records[outlet].append((short_name, start_time_str, None, None))
                    except ValueError:
                        # Skip if start time format is invalid
                        continue
                else:
                    # Skip if no start time is provided
                    continue
            elif mode == "full_yesterday":
                # Only include employees who haven't completed both sign-in and sign-out
                if not (signin and signout):
                    if outlet not in outlet_records:
                        outlet_records[outlet] = []
                    sign_in_status = "✅" if signin else "❌"
                    sign_out_status = "✅" if signout else "❌"
                    outlet_records[outlet].append((short_name, start_time_str, sign_in_status, sign_out_status))

        # No issues? Skip report
        if not outlet_records:
            update.message.reply_text(f"No missing records for {mode.replace('_', ' ')}.")
            return

        # Build the message with Markdown code blocks
        header_date = "today" if mode == "signin_only" else report_date
        message = [f"Attendance Report for {header_date}", "```"]
        
        # Sort outlets alphabetically
        for outlet in sorted(outlet_records.keys()):
            message.append(f"Outlet: {outlet}")
            if mode == "signin_only":
                # Determine the maximum name length for this outlet
                max_name_length = max(len(name) for name, _, _, _ in outlet_records[outlet])
                message.append(f"{'Name':<{max_name_length}}  {'Start Time':<10}  {'Status':<10}")
                message.append("-" * max_name_length + "  " + "-" * 10 + "  " + "-" * 10)
                for name, start_time, _, _ in sorted(outlet_records[outlet]):  # Sort by name
                    message.append(f"{name:<{max_name_length}}  {start_time[:10]:<10}  {'Not Signed In':<10}")
            else:
                # Determine the maximum name length for this outlet
                max_name_length = max(len(name) for name, _, _, _ in outlet_records[outlet])
                message.append(f"{'Name':<{max_name_length}}  {'Start Time':<10}  {'Sign In':<8}  {'Sign Out':<8}")
                message.append("-" * max_name_length + "  " + "-" * 10 + "  " + "-" * 8 + "  " + "-" * 8)
                for name, start_time, sign_in, sign_out in sorted(outlet_records[outlet]):  # Sort by name
                    # Add two spaces before the symbols to shift them right for centering
                    sign_in_display = "  " + sign_in if sign_in in ["✅", "❌"] else sign_in
                    sign_out_display = "  " + sign_out if sign_out in ["✅", "❌"] else sign_out
                    message.append(f"{name:<{max_name_length}}  {start_time[:10]:<10}  {sign_in_display:<8}  {sign_out_display:<8}")
            message.append("")  # Empty line between outlets

        # Add summary
        total_records = sum(len(records) for records in outlet_records.values())
        message.append(f"Total Missing Records: {total_records}")
        message.append("```")

        update.message.reply_text("\n".join(message).strip(), parse_mode="Markdown")
        print(f"Attendance report sent for {mode}")

    except Exception as e:
        update.message.reply_text(f"Error generating report: {e}")
        print(f"Error sending report: {e}")

def statustoday(update: Update, context):
    send_attendance_report(update, context, mode="signin_only")

def statusyesterday(update: Update, context):
    send_attendance_report(update, context, mode="full_yesterday")

def getroster(update: Update, context):
    try:
        # Fetch data from Google Sheet
        gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE))
        roster_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
        outlet_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
        shift_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_SHIFTS)
        emp_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)

        roster = roster_sheet.get_all_records()
        outlet_records = outlet_sheet.get_all_records()
        shift_records = shift_sheet.get_all_records()
        emp_register = emp_sheet.get_all_records()

        # Map Employee ID → Short Name
        emp_id_to_name = {
            str(row.get("Employee ID")).strip(): row.get("Short Name", "Unnamed")
            for row in emp_register if row.get("Employee ID")
        }

        # Map outlet codes to outlet names (case-insensitive)
        outlet_code_to_name = {
            str(row.get("Outlet Code")).strip().lower(): str(row.get("Outlet Name")).strip()
            for row in outlet_records if row.get("Outlet Code") and row.get("Outlet Name")
        }

        # Map shift IDs to shift names
        shift_id_to_name = {
            str(row.get("Shift ID")).strip(): str(row.get("Shift Name")).strip()
            for row in shift_records if row.get("Shift ID") and row.get("Shift Name")
        }

        # Process roster data for today
        now = datetime.datetime.now(INDIA_TZ)
        target_date = now.strftime("%d/%m/%Y")  # 31/07/2025
        outlet_groups = {}

        for row in roster:
            if str(row.get("Date", "")).strip() != target_date:
                continue

            emp_id = str(row.get("Employee ID", "")).strip()
            name = emp_id_to_name.get(emp_id, emp_id)  # Use employee name or fallback to ID
            outlet_code = str(row.get("Outlet", "")).strip()
            if outlet_code.lower() == "wo":
                continue  # Skip WO outlets
            outlet_name = outlet_code_to_name.get(outlet_code.lower(), outlet_code)  # Get full outlet name
            shift_id = str(row.get("Shift", "")).strip()
            shift_name = shift_id_to_name.get(shift_id, "")  # Map shift ID to shift name

            if outlet_name not in outlet_groups:
                outlet_groups[outlet_name] = []
            outlet_groups[outlet_name].append((name, shift_name))

        # If no records found
        if not outlet_groups:
            update.message.reply_text(f"No roster records found for today ({target_date}).")
            return

        # Build the message with code block formatting
        message = ["```"]
        message.append(f"*Roster for Today ({target_date}):*")
        message.append("")  # Empty line after header
        
        for outlet_name in sorted(outlet_groups.keys()):  # Sort outlets alphabetically
            message.append(f"*Outlet: {outlet_name}*")
            message.append("-" * (len(outlet_name) + 8))  # Underline for outlet name
            for name, shift_name in sorted(outlet_groups[outlet_name]):  # Sort employees by name
                message.append(f"{name} - {shift_name}")
            message.append("")  # Empty line between outlets
        
        # Remove last empty line and close code block
        if message[-1] == "":
            message.pop()
        message.append("```")

        update.message.reply_text("\n".join(message), parse_mode="Markdown")
        print(f"Roster report sent for {target_date}")

    except Exception as e:
        update.message.reply_text(f"Error generating roster: {e}")
        print(f"Error sending roster: {e}")

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
    print(f"Start command received from user: {update.message.from_user.id}, chat: {update.message.chat_id}")  # Debug log
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
            timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
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
    print(f"Received update: {update}")  # Debug log
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
        ]
    ))
    dispatcher.add_handler(CommandHandler("reset", reset))
    dispatcher.add_handler(CommandHandler("statustoday", statustoday))
    dispatcher.add_handler(CommandHandler("statusyesterday", statusyesterday))
    dispatcher.add_handler(CommandHandler("getroster", getroster))

    try:
        bot.set_my_commands([
            ("start", "Start the bot"),
            ("reset", "Reset the conversation"),
            ("statustoday", "Show today's sign-in status"),
            ("statusyesterday", "Show yesterday's full attendance report"),
            ("getroster", "Show today's roster")
        ])
        print("Bot commands set successfully.")
    except Exception as e:
        print(f"Failed to set bot commands: {e}")

def set_webhook():
    try:
        response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
        response_data = response.json()
        print(f"getMe response: {response_data}")  # Debug log
        if isinstance(response_data, dict) and response_data.get("ok"):
            bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
            print(f"Webhook set at {WEBHOOK_URL}{WEBHOOK_PATH}")
        else:
            print(f"Invalid BOT_TOKEN or API error: {response_data}")
    except Exception as e:
        print(f"Error setting webhook: {e}")

# === Main Entry Point ===
setup_dispatcher()
set_webhook()