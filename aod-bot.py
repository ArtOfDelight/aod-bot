import os
import re
import math
import datetime
import uuid
import hashlib
import time
from werkzeug.utils import secure_filename
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
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
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
TAB_CHECKLIST = "ChecklistQuestions"
TAB_RESPONSES = "ChecklistResponses"
TAB_SUBMISSIONS = "ChecklistSubmissions"
LOCATION_TOLERANCE_METERS = 50
IMAGE_FOLDER = "checklist"
DRIVE_FOLDER_ID = "0AEmGXk8Yd_pdUk9PVA"  # Replace with your Google Drive folder ID

# === Flask + Telegram Setup ===
app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=4)

# === Global Google Sheets Client ===
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    client = gspread.authorize(creds)
    print("Google Sheets client initialized successfully")
except Exception as e:
    print(f"Failed to initialize Google Sheets client: {e}")
    raise

# === Google Drive Setup ===
def setup_drive():
    try:
        gauth = GoogleAuth()
        gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
        return GoogleDrive(gauth)
    except Exception as e:
        print(f"Failed to setup Google Drive: {e}")
        raise

drive = setup_drive()

# === States ===
ASK_ACTION, ASK_PHONE, ASK_LOCATION = range(3)
CHECKLIST_ASK_CONTACT, CHECKLIST_ASK_SLOT, CHECKLIST_ASK_QUESTION, CHECKLIST_ASK_IMAGE = range(10, 14)

# === Utility Functions ===
def normalize_number(number):
    return re.sub(r"\D", "", number)[-10:]

def sanitize_filename(name):
    if not name:
        return "unknown"
    return re.sub(r"[^a-zA-Z0-9_/\\.]", "", name.replace(" ", "_"))

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
        # List of fired employees to exclude
        fired_employees = ["Mon", "Ruth", "Tongminthang", "Sameer", "jenny"]
        
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

        # Find the latest date in the roster data
        all_dates = []
        for row in roster:
            date_str = str(row.get("Date", "")).strip()
            if date_str:
                try:
                    # Parse the date and add to list
                    date_obj = datetime.datetime.strptime(date_str, "%d/%m/%Y")
                    all_dates.append((date_obj, date_str))
                except ValueError:
                    continue  # Skip invalid date formats
        
        if not all_dates:
            update.message.reply_text("No valid dates found in roster data.")
            return
        
        # Sort dates and get the latest one
        all_dates.sort(key=lambda x: x[0])  # Sort by datetime object
        latest_date_obj, target_date = all_dates[-1]  # Get the latest date
        
        # Process roster data for the latest date
        outlet_groups = {}

        for row in roster:
            if str(row.get("Date", "")).strip() != target_date:
                continue

            emp_id = str(row.get("Employee ID", "")).strip()
            name = emp_id_to_name.get(emp_id, emp_id)  # Use employee name or fallback to ID
            
            # Skip fired employees
            if name in fired_employees:
                continue
                
            outlet_code = str(row.get("Outlet", "")).strip()
            shift_id = str(row.get("Shift", "")).strip()
            shift_name = shift_id_to_name.get(shift_id,'')  # Map shift ID to shift name

            if outlet_code.lower() == "wo":
                outlet_name = "Weekly Off"
            else:
                outlet_name = outlet_code_to_name.get(outlet_code.lower(), outlet_code)

            if outlet_name not in outlet_groups:
                outlet_groups[outlet_name] = []
            outlet_groups[outlet_name].append((name, shift_name))

        # If no records found
        if not outlet_groups:
            update.message.reply_text(f"No roster records found for the latest date ({target_date}).")
            return

        # Get the day of the week for the latest date
        day_of_week = latest_date_obj.strftime("%A")
        
        # Build the message with code block formatting
        message = ["```"]
        message.append(f"*Roster for {day_of_week} ({target_date}):*")
        message.append("")  # Empty line after header

        for outlet_name in sorted(outlet_groups.keys()):
            # Add * around outlet names for emphasis
            message.append(f"*{outlet_name}*")
            
            for name, shift_name in sorted(outlet_groups[outlet_name]):
                # For Weekly Off only, show just the name without hyphens
                if outlet_name == "Weekly Off":
                    message.append(f"{name}")
                else:
                    message.append(f"{name} - {shift_name}")
            message.append("")  # Empty line between outlets

        if message[-1] == "":
            message.pop()
        message.append("```")

        update.message.reply_text("\n".join(message), parse_mode="Markdown")
        print(f"Roster report sent for latest date: {target_date}")

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

def get_employee_info(phone):
    try:
        phone = normalize_number(phone)
        emp_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)
        emp_records = emp_sheet.get_all_records()
        today = datetime.datetime.now(INDIA_TZ).strftime("%d/%m/%Y")
        for row in emp_records:
            row_phone = normalize_number(str(row.get("Phone Number", "")))
            if row_phone == phone:
                emp_name = sanitize_filename(str(row.get("Full Name", "Unknown")))
                emp_id = str(row.get("Employee ID", ""))
                roster_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
                roster_records = roster_sheet.get_all_records()
                for record in roster_records:
                    if record.get("Employee ID") == emp_id and record.get("Date") == today:
                        outlet_code = record.get("Outlet")
                        # Validate outlet code exists in Outlets sheet
                        outlets_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
                        outlets_records = outlets_sheet.get_all_records()
                        if not any(str(row.get("Outlet Code")).strip().upper() == outlet_code.upper() for row in outlets_records):
                            bot.send_message(chat_id=MANAGER_CHAT_ID, text=f"Invalid outlet code {outlet_code} in Roster for {emp_name}")
                            return "Unknown", ""
                        return emp_name, outlet_code
        return "Unknown", ""
    except Exception as e:
        print(f"Failed to fetch employee info: {e}")
        return "Unknown", ""

def get_applicable_checklist_for_outlet(outlet_code):
    """Get the Applicable Checklist for a given outlet code from Outlets tab"""
    try:
        sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
        records = sheet.get_all_records()
        for row in records:
            if str(row.get("Outlet Code")).strip().upper() == outlet_code.strip().upper():
                applicable_checklist = str(row.get("Applicable Checklist", "")).strip()
                if not applicable_checklist:
                    print(f"No Applicable Checklist for outlet code {outlet_code}, using default 'Generic'")
                    return "Generic"
                print(f"Found applicable checklist '{applicable_checklist}' for outlet code '{outlet_code}'")
                return applicable_checklist
        print(f"No matching outlet code {outlet_code} in Outlets, using default 'Generic'")
        bot.send_message(chat_id=MANAGER_CHAT_ID, text=f"No matching outlet code {outlet_code} in Outlets sheet")
        return "Generic"
    except Exception as e:
        print(f"Failed to fetch applicable checklist for outlet {outlet_code}: {e}")
        return "Generic"

def get_filtered_questions(outlet_code, slot):
    """Get questions filtered by applicable checklist and time slot with retry logic"""
    try:
        applicable_checklist = get_applicable_checklist_for_outlet(outlet_code)
        print(f"Fetching questions for outlet {outlet_code}, checklist '{applicable_checklist}', slot '{slot}'")
        sheet = client.open(SHEET_NAME).worksheet(TAB_CHECKLIST)
        records = sheet.get_all_records()
        filtered_questions = []
        for row in records:
            row_checklist = str(row.get("Applicable Checklist", "")).strip()
            row_slot = str(row.get("Time_Slot", "")).strip()
            if (row_checklist.upper() == applicable_checklist.upper() and 
                row_slot.upper() == slot.strip().upper()):
                question_text = row.get("Question_Text", "").strip()
                if not question_text:
                    print(f"Empty Question_Text in ChecklistQuestions for checklist {row_checklist}, slot {row_slot}")
                    continue
                filtered_questions.append({
                    "question": question_text,
                    "image_required": str(row.get("Image Required", "")).strip().lower() == "yes"
                })
        print(f"Found {len(filtered_questions)} questions for checklist '{applicable_checklist}', slot '{slot}'")
        if not filtered_questions:
            print(f"No questions found for checklist '{applicable_checklist}', slot '{slot}'")
        return filtered_questions
    except Exception as e:
        print(f"Failed to fetch questions: {e}. Retrying once...")
        time.sleep(1)  # Brief delay before retry
        try:
            sheet = client.open(SHEET_NAME).worksheet(TAB_CHECKLIST)
            records = sheet.get_all_records()
            filtered_questions = []
            for row in records:
                row_checklist = str(row.get("Applicable Checklist", "")).strip()
                row_slot = str(row.get("Time_Slot", "")).strip()
                if (row_checklist.upper() == applicable_checklist.upper() and 
                    row_slot.upper() == slot.strip().upper()):
                    question_text = row.get("Question_Text", "").strip()
                    if not question_text:
                        continue
                    filtered_questions.append({
                        "question": question_text,
                        "image_required": str(row.get("Image Required", "")).strip().lower() == "yes"
                    })
            print(f"Retry successful: Found {len(filtered_questions)} questions")
            return filtered_questions
        except Exception as e2:
            print(f"Retry failed: {e2}")
            return []

# === Bot Handlers ===
def start(update: Update, context):
    print(f"Start command received from user: {update.message.from_user.id}, chat: {update.message.chat_id}")  # Debug log
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Sign In", callback_data="signin")],
        [InlineKeyboardButton("🔴 Sign Out", callback_data="signout")],
        [InlineKeyboardButton("📋 Fill Checklist", callback_data="checklist")]
    ])
    update.message.reply_text("Welcome! What would you like to do today?", reply_markup=buttons)
    return ASK_ACTION

def action_selected(update: Update, context):
    query = update.callback_query
    query.answer()
    if query.data == "checklist":
        contact_button = KeyboardButton("📱 Send Phone Number", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
        query.message.reply_text("Please verify your phone number for the checklist:", reply_markup=markup)
        return CHECKLIST_ASK_CONTACT
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

def cl_handle_contact(update: Update, context):
    print("Handling checklist contact verification")
    if not update.message.contact:
        print("No contact received")
        update.message.reply_text("❌ Please use the button to send your contact.")
        return CHECKLIST_ASK_CONTACT
    phone = normalize_number(update.message.contact.phone_number)
    emp_name, outlet_code = get_employee_info(phone)
    if emp_name == "Unknown" or not outlet_code:
        print(f"Invalid employee info for phone {phone}")
        update.message.reply_text("❌ You're not rostered today or not registered.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    context.user_data.update({"emp_name": emp_name, "outlet": outlet_code})
    print(f"Contact verified: emp_name={emp_name}, outlet_code={outlet_code}")
    update.message.reply_text("⏰ Select time slot:",
                             reply_markup=ReplyKeyboardMarkup([["Morning", "Mid Day", "Closing"]], one_time_keyboard=True, resize_keyboard=True))
    return CHECKLIST_ASK_SLOT

def cl_load_questions(update: Update, context):
    print("Loading checklist questions for selected slot")
    slot = update.message.text
    if slot not in ["Morning", "Mid Day", "Closing"]:
        print(f"Invalid slot selected: {slot}")
        update.message.reply_text("❌ Invalid time slot. Please select Morning, Mid Day, or Closing.")
        return CHECKLIST_ASK_SLOT
    context.user_data["slot"] = slot
    context.user_data["submission_id"] = str(uuid.uuid4())[:8]
    context.user_data["timestamp"] = datetime.datetime.now(INDIA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    context.user_data["date"] = datetime.datetime.now(INDIA_TZ).strftime("%Y-%m-%d")
    questions = get_filtered_questions(context.user_data["outlet"], context.user_data["slot"])
    if not questions:
        print(f"No questions found for outlet {context.user_data['outlet']}, slot {slot}")
        update.message.reply_text("❌ No checklist questions found for this outlet and time slot.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    context.user_data.update({"questions": questions, "answers": [], "current_q": 0})
    print(f"Loaded {len(questions)} questions for outlet {context.user_data['outlet']}, slot {slot}")
    return cl_ask_next_question(update, context)

def cl_ask_next_question(update: Update, context):
    print(f"Asking checklist question {context.user_data['current_q'] + 1}")
    idx = context.user_data["current_q"]
    if idx >= len(context.user_data["questions"]):
        print("All checklist questions completed, saving responses")
        # Batch save all responses to ChecklistResponses
        try:
            responses_sheet = client.open(SHEET_NAME).worksheet(TAB_RESPONSES)
            for answer in context.user_data["answers"]:
                responses_sheet.append_row([
                    context.user_data["submission_id"],
                    answer["question"],
                    answer["answer"],
                    answer.get("image_link", ""),
                    answer.get("image_hash", "")
                ])
            print(f"Saved {len(context.user_data['answers'])} responses to ChecklistResponses")
        except Exception as e:
            print(f"Failed to batch save responses: {e}")
            update.message.reply_text("❌ Error saving checklist responses. Please contact admin.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        update.message.reply_text("✅ Checklist completed successfully.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    q_data = context.user_data["questions"][idx]
    if q_data["image_required"]:
        update.message.reply_text(f"📷 {q_data['question']}\n\nPlease upload an image for this step.", 
                                 reply_markup=ReplyKeyboardRemove())
        context.user_data.setdefault("answers", []).append({
            "question": q_data["question"], 
            "answer": "Image Required", 
            "image_link": "",
            "image_hash": ""
        })
        return CHECKLIST_ASK_IMAGE
    else:
        update.message.reply_text(f"❓ {q_data['question']}",
                                 reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True))
        return CHECKLIST_ASK_QUESTION

def cl_handle_answer(update: Update, context):
    print("Handling checklist answer")
    ans = update.message.text
    if ans not in ["Yes", "No"]:
        print(f"Invalid answer: {ans}")
        update.message.reply_text("❌ Please answer with Yes or No.")
        return CHECKLIST_ASK_QUESTION
    q_data = context.user_data["questions"][context.user_data["current_q"]]
    context.user_data["answers"].append({
        "question": q_data["question"],
        "answer": ans,
        "image_link": "",
        "image_hash": ""
    })
    context.user_data["current_q"] += 1
    return cl_ask_next_question(update, context)

def cl_handle_image_upload(update: Update, context):
    if not update.message.photo:
        update.message.reply_text("❌ Please upload a photo.")
        return CHECKLIST_ASK_IMAGE
    try:
        photo = update.message.photo[-1]
        file = photo.get_file()
        emp_name = context.user_data.get("emp_name", "User")
        q_num = context.user_data["current_q"] + 1
        current_date = datetime.datetime.now(INDIA_TZ).strftime("%Y-%m-%d")
        filename = f"checklist/{emp_name}_Q{q_num}_{current_date}.jpg"
        # Use /tmp for temporary storage (writable on Render and local)
        local_path = os.path.join("/tmp", secure_filename(f"{emp_name}_Q{q_num}_{current_date}.jpg"))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        file.download(custom_path=local_path)
        if not os.path.exists(local_path):
            raise Exception(f"File download failed: File not created at {local_path}")
        # Compute image hash
        hash_md5 = hashlib.md5()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        image_hash = hash_md5.hexdigest()
        # Check for duplicates
        submissions_sheet = client.open(SHEET_NAME).worksheet(TAB_SUBMISSIONS)
        records = submissions_sheet.get_all_records()
        for record in records:
            if (
                record.get("Date") == context.user_data["date"] and
                record.get("Time Slot") == context.user_data["slot"] and
                record.get("Outlet") == context.user_data["outlet"] and
                record.get("Submitted By") == context.user_data["emp_name"].replace("_", " ") and
                record.get("Imagecode") == image_hash
            ):
                update.message.reply_text("❌ Duplicate image detected. Please retake the photo.")
                os.remove(local_path)
                return CHECKLIST_ASK_IMAGE
        # Upload to Google Drive
        try:
            gfile = drive.CreateFile({
                'title': filename,
                'parents': [{'id': DRIVE_FOLDER_ID}],
                'supportsAllDrives': True
            })
            gfile.SetContentFile(local_path)
            gfile.Upload(param={'supportsAllDrives': True})
            image_link = filename
        except Exception as drive_err:
            image_link = f"local_only_{filename}"  # Fallback to local reference
        # Update answer with image link and hash
        context.user_data["answers"][-1]["image_link"] = image_link
        context.user_data["answers"][-1]["image_hash"] = image_hash
        # Save to ChecklistSubmissions
        submissions_sheet.append_row([
            context.user_data["submission_id"],
            context.user_data["date"],
            context.user_data["slot"],
            context.user_data["outlet"],
            context.user_data["emp_name"].replace("_", " "),
            context.user_data["timestamp"]
        ])
        update.message.reply_text("✅ Image uploaded successfully.")
        os.remove(local_path)  # Clean up temporary file
    except Exception as e:
        time.sleep(1)
        try:
            file.download(custom_path=local_path)
            if not os.path.exists(local_path):
                raise Exception(f"Retry file download failed: File not created at {local_path}")
            hash_md5 = hashlib.md5()
            with open(local_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            image_hash = hash_md5.hexdigest()
            records = submissions_sheet.get_all_records()
            for record in records:
                if (
                    record.get("Date") == context.user_data["date"] and
                    record.get("Time Slot") == context.user_data["slot"] and
                    record.get("Outlet") == context.user_data["outlet"] and
                    record.get("Submitted By") == context.user_data["emp_name"].replace("_", " ") and
                    record.get("Imagecode") == image_hash
                ):
                    update.message.reply_text("❌ Duplicate image detected.")
                    os.remove(local_path)
                    return CHECKLIST_ASK_IMAGE
            try:
                gfile = drive.CreateFile({
                    'title': filename,
                    'parents': [{'id': DRIVE_FOLDER_ID}],
                    'supportsAllDrives': True
                })
                gfile.SetContentFile(local_path)
                gfile.Upload(param={'supportsAllDrives': True})
                image_link = filename
            except Exception as drive_err:
                image_link = f"local_only_{filename}"
            context.user_data["answers"][-1]["image_link"] = image_link
            context.user_data["answers"][-1]["image_hash"] = image_hash
            submissions_sheet.append_row([
                context.user_data["submission_id"],
                context.user_data["date"],
                context.user_data["slot"],
                context.user_data["outlet"],
                context.user_data["emp_name"].replace("_", " "),
                context.user_data["timestamp"]
            ])
            update.message.reply_text("✅ Image uploaded successfully (retry).")
            os.remove(local_path)
        except Exception as e2:
            update.message.reply_text("❌ Error uploading image. Please try again.")
            if os.path.exists(local_path):
                os.remove(local_path)
            return CHECKLIST_ASK_IMAGE
    context.user_data["current_q"] += 1
    return cl_ask_next_question(update, context)

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
            CHECKLIST_ASK_CONTACT: [MessageHandler(Filters.contact, cl_handle_contact)],
            CHECKLIST_ASK_SLOT: [MessageHandler(Filters.text & ~Filters.command, cl_load_questions)],
            CHECKLIST_ASK_QUESTION: [MessageHandler(Filters.text & ~Filters.command, cl_handle_answer)],
            CHECKLIST_ASK_IMAGE: [MessageHandler(Filters.photo, cl_handle_image_upload)]
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
