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
TICKET_SHEET_ID = "1FYXr8Wz0ddN3mFi-0AQbI6J_noi2glPbJLh44CEMUnE"
TAB_NAME_ROSTER = "Roster"
TAB_NAME_OUTLETS = "Outlets"
TAB_NAME_EMP_REGISTER = "EmployeeRegister"
TAB_NAME_SHIFTS = "Shifts"
TAB_CHECKLIST = "ChecklistQuestions"
TAB_RESPONSES = "ChecklistResponses"
TAB_SUBMISSIONS = "ChecklistSubmissions"
TAB_TICKETS = "Tickets"
LOCATION_TOLERANCE_METERS = 50
IMAGE_FOLDER = "checklist"
TICKET_FOLDER = "tickets"

# UPDATED FOLDER IDs - Use the new ones from the shared drive
DRIVE_FOLDER_ID = "1FJuTky2XPUSNMAC41SOQ-TFKzSq9Wd7i"  # Checklist folder in shared drive
TICKET_DRIVE_FOLDER_ID = "1frXb-FRKRPPDql4l_VxUJ8r9xgdSfRj-"  # Tickets folder in shared drive
SHARED_DRIVE_ID = "0AEmGXk8Yd_pdUk9PVA"  # The shared drive root

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
        drive = GoogleDrive(gauth)
        # Check or create tickets folder in Shared Drive
        global TICKET_DRIVE_FOLDER_ID
        folder_query = f"'{TICKET_DRIVE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folder_list = drive.ListFile({
            'q': folder_query,
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True
        }).GetList()
        if folder_list:
            print(f"Found existing tickets folder with ID: {TICKET_DRIVE_FOLDER_ID}")
        else:
            folder_metadata = {
                'title': TICKET_FOLDER,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': TICKET_DRIVE_FOLDER_ID}],
                'supportsAllDrives': True
            }
            folder = drive.CreateFile(folder_metadata)
            folder.Upload(param={'supportsAllDrives': True})
            TICKET_DRIVE_FOLDER_ID = folder['id']
            print(f"Created tickets folder with ID: {TICKET_DRIVE_FOLDER_ID}")
        # Verify checklist folder accessibility
        try:
            drive.ListFile({
                'q': f"'{DRIVE_FOLDER_ID}' in parents",
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True,
                'maxResults': 1
            }).GetList()
            print(f"Checklist folder {DRIVE_FOLDER_ID} is accessible")
        except Exception as e:
            print(f"Warning: Checklist folder {DRIVE_FOLDER_ID} not accessible: {e}")
        return drive
    except Exception as e:
        print(f"Failed to setup Google Drive: {e}")
        raise

drive = setup_drive()

# === States ===
ASK_ACTION, ASK_PHONE, ASK_LOCATION = range(3)
CHECKLIST_ASK_CONTACT, CHECKLIST_ASK_SLOT, CHECKLIST_ASK_QUESTION, CHECKLIST_ASK_IMAGE = range(10, 14)
TICKET_ASK_CONTACT, TICKET_ASK_TYPE, TICKET_ASK_ISSUE = range(20, 23)  # Added TICKET_ASK_TYPE

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
        if mode == "full_yesterday":
            report_date = (now - datetime.timedelta(days=1)).strftime("%d/%m/%Y")
        else:
            report_date = (now - datetime.timedelta(days=1) if now.hour < 4 else now).strftime("%d/%m/%Y")

        gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE))
        roster_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
        emp_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)

        roster = roster_sheet.get_all_records()
        emp_register = emp_sheet.get_all_records()

        emp_id_to_name = {
            str(row.get("Employee ID")).strip(): row.get("Short Name", "Unnamed")
            for row in emp_register if row.get("Employee ID")
        }

        outlet_records = {}
        for row in roster:
            if str(row.get("Date", "")).strip() != report_date:
                continue

            emp_id = str(row.get("Employee ID", "")).strip()
            short_name = emp_id_to_name.get(emp_id, emp_id)
            outlet = row.get("Outlet", "").strip()
            
            if outlet.lower() == "wo":
                continue

            signin = str(row.get("Sign-In Time", "")).strip()
            signout = str(row.get("Sign-Out Time", "")).strip()
            start_time_str = str(row.get("Start Time", "")).strip() or "N/A"

            if mode == "signin_only" and not signin:
                if start_time_str != "N/A":
                    try:
                        start_time = datetime.datetime.strptime(start_time_str, "%H:%M:%S").time()
                        current_time = now.time()
                        if start_time <= current_time:
                            if outlet not in outlet_records:
                                outlet_records[outlet] = []
                            outlet_records[outlet].append((short_name, start_time_str, None, None))
                    except ValueError:
                        continue
                else:
                    continue
            elif mode == "full_yesterday":
                if not (signin and signout):
                    if outlet not in outlet_records:
                        outlet_records[outlet] = []
                    sign_in_status = "✅" if signin else "❌"
                    sign_out_status = "✅" if signout else "❌"
                    outlet_records[outlet].append((short_name, start_time_str, sign_in_status, sign_out_status))

        if not outlet_records:
            update.message.reply_text(f"No missing records for {mode.replace('_', ' ')}.")
            return

        header_date = "today" if mode == "signin_only" else report_date
        message = [f"Attendance Report for {header_date}", "```"]
        
        for outlet in sorted(outlet_records.keys()):
            message.append(f"Outlet: {outlet}")
            if mode == "signin_only":
                max_name_length = max(len(name) for name, _, _, _ in outlet_records[outlet])
                message.append(f"{'Name':<{max_name_length}}  {'Start Time':<10}  {'Status':<10}")
                message.append("-" * max_name_length + "  " + "-" * 10 + "  " + "-" * 10)
                for name, start_time, _, _ in sorted(outlet_records[outlet]):
                    message.append(f"{name:<{max_name_length}}  {start_time[:10]:<10}  {'Not Signed In':<10}")
            else:
                max_name_length = max(len(name) for name, _, _, _ in outlet_records[outlet])
                message.append(f"{'Name':<{max_name_length}}  {'Start Time':<10}  {'Sign In':<8}  {'Sign Out':<8}")
                message.append("-" * max_name_length + "  " + "-" * 10 + "  " + "-" * 8 + "  " + "-" * 8)
                for name, start_time, sign_in, sign_out in sorted(outlet_records[outlet]):
                    sign_in_display = "  " + sign_in if sign_in in ["✅", "❌"] else sign_in
                    sign_out_display = "  " + sign_out if sign_out in ["✅", "❌"] else sign_out
                    message.append(f"{name:<{max_name_length}}  {start_time[:10]:<10}  {sign_in_display:<8}  {sign_out_display:<8}")
            message.append("")

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
        fired_employees = ["Mon", "Ruth", "Tongminthang", "Sameer", "jenny"]
        
        gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE))
        roster_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
        outlet_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
        shift_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_SHIFTS)
        emp_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)

        roster = roster_sheet.get_all_records()
        outlet_records = outlet_sheet.get_all_records()
        shift_records = shift_sheet.get_all_records()
        emp_register = emp_sheet.get_all_records()

        emp_id_to_name = {
            str(row.get("Employee ID")).strip(): row.get("Short Name", "Unnamed")
            for row in emp_register if row.get("Employee ID")
        }

        outlet_code_to_name = {
            str(row.get("Outlet Code")).strip().lower(): str(row.get("Outlet Name")).strip()
            for row in outlet_records if row.get("Outlet Code") and row.get("Outlet Name")
        }

        shift_id_to_name = {
            str(row.get("Shift ID")).strip(): str(row.get("Shift Name")).strip()
            for row in shift_records if row.get("Shift ID") and row.get("Shift Name")
        }

        all_dates = []
        for row in roster:
            date_str = str(row.get("Date", "")).strip()
            if date_str:
                try:
                    date_obj = datetime.datetime.strptime(date_str, "%d/%m/%Y")
                    all_dates.append((date_obj, date_str))
                except ValueError:
                    continue
        
        if not all_dates:
            update.message.reply_text("No valid dates found in roster data.")
            return
        
        all_dates.sort(key=lambda x: x[0])
        latest_date_obj, target_date = all_dates[-1]
        
        outlet_groups = {}

        for row in roster:
            if str(row.get("Date", "")).strip() != target_date:
                continue

            emp_id = str(row.get("Employee ID", "")).strip()
            name = emp_id_to_name.get(emp_id, emp_id)
            
            if name in fired_employees:
                continue
                
            outlet_code = str(row.get("Outlet", "")).strip()
            shift_id = str(row.get("Shift", "")).strip()
            shift_name = shift_id_to_name.get(shift_id,'')

            if outlet_code.lower() == "wo":
                outlet_name = "Weekly Off"
            else:
                outlet_name = outlet_code_to_name.get(outlet_code.lower(), outlet_code)

            if outlet_name not in outlet_groups:
                outlet_groups[outlet_name] = []
            outlet_groups[outlet_name].append((name, shift_name))

        if not outlet_groups:
            update.message.reply_text(f"No roster records found for the latest date ({target_date}).")
            return

        day_of_week = latest_date_obj.strftime("%A")
        message = ["```"]
        message.append(f"*Roster for {day_of_week} ({target_date}):*")
        message.append("")

        for outlet_name in sorted(outlet_groups.keys()):
            message.append(f"*{outlet_name}*")
            for name, shift_name in sorted(outlet_groups[outlet_name]):
                if outlet_name == "Weekly Off":
                    message.append(f"{name}")
                else:
                    message.append(f"{name} - {shift_name}")
            message.append("")
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
    if now.hour < 4:
        target_date = (now - datetime.timedelta(days=1)).strftime("%d/%m/%Y")
    else:
        target_date = now.strftime("%d/%m/%Y")

    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    sheet = gspread.authorize(creds).open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
    records = sheet.get_all_records()

    for idx, row in enumerate(records, start=2):
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
    try:
        current_day = datetime.datetime.now(INDIA_TZ).strftime("%A")
        outlets_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
        outlets_records = outlets_sheet.get_all_records()
        applicable_checklist = None
        for row in outlets_records:
            if str(row.get("Outlet Code", "")).strip().lower() == outlet_code.lower():
                applicable_checklist = str(row.get("Applicable Checklist", "")).strip()
                break
        if not applicable_checklist:
            return []

        sheet = client.open(SHEET_NAME).worksheet(TAB_CHECKLIST)
        records = sheet.get_all_records()
        filtered_questions = []

        for row in records:
            row_slot = str(row.get("Time_Slot", "")).strip()
            outlet_value = str(row.get(applicable_checklist, "")).strip().lower()
            days_value = str(row.get("Days", "")).strip()
            
            if row_slot.upper() == slot.strip().upper() and outlet_value == "yes":
                if days_value and days_value.lower() != "all":
                    applicable_days = [day.strip() for day in days_value.split(",")]
                    if current_day not in applicable_days:
                        continue
                question_text = row.get("Question_Text", "").strip()
                if not question_text:
                    continue
                filtered_questions.append({
                    "question": question_text,
                    "image_required": str(row.get("Image Required", "")).strip().lower() == "yes"
                })
        if not filtered_questions:
            return []
        return filtered_questions
        
    except Exception as e:
        time.sleep(1)
        try:
            current_day = datetime.datetime.now(INDIA_TZ).strftime("%A")
            outlets_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
            outlets_records = outlets_sheet.get_all_records()
            applicable_checklist = None
            for row in outlets_records:
                if str(row.get("Outlet Code", "")).strip().lower() == outlet_code.lower():
                    applicable_checklist = str(row.get("Applicable Checklist", "")).strip()
                    break
            if not applicable_checklist:
                return []
            sheet = client.open(SHEET_NAME).worksheet(TAB_CHECKLIST)
            records = sheet.get_all_records()
            filtered_questions = []
            for row in records:
                row_slot = str(row.get("Time_Slot", "")).strip()
                outlet_value = str(row.get(applicable_checklist, "")).strip().lower()
                days_value = str(row.get("Days", "")).strip()
                if row_slot.upper() == slot.strip().upper() and outlet_value == "yes":
                    if days_value and days_value.lower() != "all":
                        applicable_days = [day.strip() for day in days_value.split(",")]
                        if current_day not in applicable_days:
                            continue
                    question_text = row.get("Question_Text", "").strip()
                    if not question_text:
                        continue
                    filtered_questions.append({
                        "question": question_text,
                        "image_required": str(row.get("Image Required", "")).strip().lower() == "yes"
                    })
            return filtered_questions
        except Exception:
            return []

# === Bot Handlers ===
def start(update: Update, context):
    print(f"Start command received from user: {update.message.from_user.id}, chat: {update.message.chat_id}")
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Sign In", callback_data="signin")],
        [InlineKeyboardButton("🔴 Sign Out", callback_data="signout")],
        [InlineKeyboardButton("📋 Fill Checklist", callback_data="checklist")],
        [InlineKeyboardButton("🎫 Raise Ticket", callback_data="ticket")]
    ])
    update.message.reply_text("Welcome! What would you like to do today?", reply_markup=buttons)
    return ASK_ACTION

def action_selected(update: Update, context):
    query = update.callback_query
    query.answer()
    context.user_data["action"] = query.data
    contact_button = KeyboardButton("📱 Send Phone Number", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
    if query.data == "checklist":
        query.message.reply_text("Please verify your phone number for the checklist:", reply_markup=markup)
        return CHECKLIST_ASK_CONTACT
    elif query.data == "ticket":
        query.message.reply_text("Please verify your phone number to raise a ticket:", reply_markup=markup)
        return TICKET_ASK_CONTACT
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

    emp_id = context.user_data["emp_id"]
    emp_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)
    emp_records = emp_sheet.get_all_records()
    emp_name = "Unknown"
    for row in emp_records:
        if str(row.get("Employee ID", "")).strip() == emp_id:
            emp_name = row.get("Short Name", "Unknown")
            break

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

        try:
            start_time_str = context.user_data["sheet"].cell(
                context.user_data["row"], context.user_data["sheet"].row_values(1).index("Start Time") + 1
            ).value
            if start_time_str and start_time_str != "N/A":
                try:
                    today_str = now.strftime("%Y-%m-%d")
                    start_datetime = datetime.datetime.strptime(f"{today_str} {start_time_str}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                    grace_period_end = start_datetime + datetime.timedelta(minutes=15)
                    if now > grace_period_end:
                        late_message = (
                            f"⚠️ Late Sign-In Alert\n"
                            f"Employee: {emp_name}\n"
                            f"Outlet: {context.user_data['outlet_code']}\n"
                            f"Scheduled Start: {start_time_str}\n"
                            f"Sign-In Time: {timestamp}\n"
                            f"Delay: {(now - start_datetime).total_seconds() / 60:.1f} minutes"
                        )
                        try:
                            bot.send_message(chat_id=-4806089418, text=late_message)
                            print(f"Sent late sign-in alert for {emp_name} to chat ID -4806089418")
                        except Exception as e:
                            print(f"Failed to send late sign-in alert: {e}")
                except ValueError as e:
                    print(f"Error parsing start time {start_time_str}: {e}")
                    bot.send_message(
                        chat_id=MANAGER_CHAT_ID,
                        text=f"⚠️ Invalid start time format '{start_time_str}' for {emp_name} at {context.user_data['outlet_code']}"
                    )
        except Exception as e:
            print(f"Error checking start time for late sign-in: {e}")

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
    
    progress_msg = None
    local_path = None
    gfile = None
    
    try:
        photo = update.message.photo[-1]
        print(f"Photo file_id: {photo.file_id}, file_size: {photo.file_size}")
        
        if photo.file_size > 10 * 1024 * 1024:
            update.message.reply_text("❌ Image too large (max 10MB allowed).")
            return CHECKLIST_ASK_IMAGE
        
        file = None
        for attempt in range(3):
            try:
                file = photo.get_file()
                print(f"File path: {file.file_path}, file_size: {file.file_size}")
                break
            except Exception as e:
                print(f"Error getting file info (attempt {attempt + 1}): {e}")
                if attempt == 2:
                    update.message.reply_text("❌ Error accessing image file. Please try uploading again.")
                    return CHECKLIST_ASK_IMAGE
                time.sleep(2)
        
        emp_name = context.user_data.get("emp_name", "User")
        q_num = context.user_data["current_q"] + 1
        current_date = datetime.datetime.now(INDIA_TZ).strftime("%Y-%m-%d")
        timestamp_suffix = int(time.time())
        
        safe_emp_name = sanitize_filename(emp_name)
        filename = f"checklist/{safe_emp_name}_Q{q_num}_{current_date}_{timestamp_suffix}.jpg"
        local_filename = f"{safe_emp_name}_Q{q_num}_{current_date}_{timestamp_suffix}.jpg"
        local_path = os.path.join("/tmp", local_filename)
        
        print(f"Downloading to: {local_path}")
        
        os.makedirs("/tmp", exist_ok=True)
        if os.path.exists(local_path):
            os.remove(local_path)
        
        download_success = False
        for attempt in range(3):
            try:
                print(f"Download attempt {attempt + 1}")
                file.download(custom_path=local_path)
                
                if os.path.exists(local_path):
                    file_size = os.path.getsize(local_path)
                    if file_size > 0:
                        print(f"Download successful on attempt {attempt + 1}. File size: {file_size} bytes")
                        download_success = True
                        break
                    else:
                        print(f"Download attempt {attempt + 1} failed: File is empty")
                        if os.path.exists(local_path):
                            os.remove(local_path)
                else:
                    print(f"Download attempt {attempt + 1} failed: File not created")
                    
                time.sleep(2 ** attempt)
                
            except Exception as e:
                print(f"Download attempt {attempt + 1} failed with error: {e}")
                if os.path.exists(local_path):
                    os.remove(local_path)
                time.sleep(2 ** attempt)
        
        if not download_success:
            update.message.reply_text("❌ Failed to download image after multiple attempts. Please try again.")
            return CHECKLIST_ASK_IMAGE
        
        try:
            hash_md5 = hashlib.md5()
            with open(local_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            image_hash = hash_md5.hexdigest()
            print(f"Image hash computed: {image_hash}")
        except Exception as e:
            print(f"Error computing hash: {e}")
            if os.path.exists(local_path):
                os.remove(local_path)
            update.message.reply_text("❌ Error processing image. Please try again.")
            return CHECKLIST_ASK_IMAGE
        
        try:
            submissions_sheet = client.open(SHEET_NAME).worksheet(TAB_SUBMISSIONS)
            records = submissions_sheet.get_all_records()
            for record in records:
                if (
                    str(record.get("Date", "")) == context.user_data["date"] and
                    str(record.get("Time Slot", "")) == context.user_data["slot"] and
                    str(record.get("Outlet", "")) == context.user_data["outlet"] and
                    str(record.get("Submitted By", "")) == context.user_data["emp_name"].replace("_", " ") and
                    str(record.get("Imagecode", "")) == image_hash
                ):
                    print("Duplicate image detected")
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    update.message.reply_text("❌ Duplicate image detected. Please retake the photo.")
                    return CHECKLIST_ASK_IMAGE
        except Exception as e:
            print(f"Error checking duplicates: {e}")
        
        progress_msg = update.message.reply_text("⏳ Uploading image to Google Drive...")
        
        upload_success = False
        image_url = None
        
        for attempt in range(3):
            try:
                print(f"Upload attempt {attempt + 1} to Google Drive")
                if attempt > 0:
                    global drive
                    try:
                        drive = setup_drive()
                    except Exception as drive_error:
                        print(f"Failed to recreate drive connection: {drive_error}")
                        continue
                
                if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
                    raise Exception("Local file is missing or empty")
                
                gfile = drive.CreateFile({
                    'title': filename,
                    'parents': [{'id': DRIVE_FOLDER_ID}],
                    'supportsAllDrives': True
                })
                
                gfile.SetContentFile(local_path)
                gfile.Upload(param={'supportsAllDrives': True,
                                   'supportsTeamDrives': True,
                                    'enforceSingleParent': True })
                print(f"Upload completed for attempt {attempt + 1}")
                
                if not gfile.get('id'):
                    raise Exception("Upload completed but no file ID received")
                
                try:
                    gfile.InsertPermission({
                        'type': 'anyone',
                        'value': 'anyone',
                        'role': 'reader'
                    })
                    print("Permissions set successfully")
                except Exception as perm_error:
                    print(f"Permission setting failed: {perm_error}")
                
                file_id = gfile.get('id')
                if not file_id:
                    raise Exception("No file ID available")
                
                url_candidates = []
                try:
                    if gfile.get('alternateLink'):
                        url_candidates.append(gfile['alternateLink'])
                except:
                    pass
                try:
                    if gfile.get('webViewLink'):
                        url_candidates.append(gfile['webViewLink'])
                except:
                    pass
                try:
                    if gfile.get('webContentLink'):
                        url_candidates.append(gfile['webContentLink'])
                except:
                    pass
                
                url_candidates.extend([
                    f"https://drive.google.com/file/d/{file_id}/view",
                    f"https://drive.google.com/open?id={file_id}"
                ])
                
                for url in url_candidates:
                    if url and url.startswith('http'):
                        image_url = url
                        break
                
                if image_url:
                    print(f"Upload successful! URL: {image_url}")
                    upload_success = True
                    break
                else:
                    raise Exception("No valid URL could be generated")
                    
            except Exception as e:
                print(f"Upload attempt {attempt + 1} failed: {e}")
                if gfile and gfile.get('id'):
                    try:
                        gfile.Delete()
                    except:
                        pass
                gfile = None
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
        
        if not upload_success or not image_url:
            cleanup_file_safely(local_path)
            try:
                progress_msg.edit_text("❌ Failed to upload image to Google Drive after multiple attempts.")
            except:
                update.message.reply_text("❌ Failed to upload image to Google Drive after multiple attempts.")
            return CHECKLIST_ASK_IMAGE
        
        context.user_data["answers"][-1]["image_link"] = image_url
        context.user_data["answers"][-1]["image_hash"] = image_hash
        
        submission_saved = False
        for attempt in range(3):
            try:
                submissions_sheet = client.open(SHEET_NAME).worksheet(TAB_SUBMISSIONS)
                submissions_sheet.append_row([
                    context.user_data["submission_id"],
                    context.user_data["date"],
                    context.user_data["slot"],
                    context.user_data["outlet"],
                    context.user_data["emp_name"].replace("_", " "),
                    context.user_data["timestamp"],
                    image_hash
                ])
                print("Successfully saved to ChecklistSubmissions")
                submission_saved = True
                break
            except Exception as e:
                print(f"Error saving to ChecklistSubmissions (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    time.sleep(2)
        
        if not submission_saved:
            print("Failed to save to ChecklistSubmissions, but image uploaded successfully")
        
        cleanup_file_safely(local_path)
        
        try:
            progress_msg.edit_text("✅ Image uploaded successfully!")
        except:
            update.message.reply_text("✅ Image uploaded successfully!")
        
    except Exception as e:
        print(f"Unexpected error in image upload: {e}")
        cleanup_file_safely(local_path)
        if gfile and gfile.get('id'):
            try:
                gfile.Delete()
            except:
                pass
        update.message.reply_text("❌ Unexpected error during image upload. Please contact admin if the issue persists.")
        return CHECKLIST_ASK_IMAGE
    
    context.user_data["current_q"] += 1
    return cl_ask_next_question(update, context)

# === NEW TICKET HANDLERS ===
def ticket_handle_contact(update: Update, context):
    print("Handling ticket contact verification")
    if not update.message.contact:
        print("No contact received")
        update.message.reply_text("❌ Please use the button to send your contact.")
        return TICKET_ASK_CONTACT
    phone = normalize_number(update.message.contact.phone_number)
    emp_name, outlet_code = get_employee_info(phone)
    if emp_name == "Unknown" or not outlet_code:
        print(f"Invalid employee info for phone {phone}")
        update.message.reply_text("❌ You're not rostered today or not registered.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    context.user_data.update({
        "emp_name": emp_name,
        "outlet": outlet_code,
        "ticket_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.datetime.now(INDIA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "date": datetime.datetime.now(INDIA_TZ).strftime("%Y-%m-%d")
    })
    print(f"Contact verified for ticket: emp_name={emp_name}, outlet_code={outlet_code}, ticket_id={context.user_data['ticket_id']}")
    
    # Ask for ticket type
    update.message.reply_text("📝 What type of ticket would you like to raise?",
                             reply_markup=ReplyKeyboardMarkup([["🔴 Raise a Complaint", "📦 Place an Order"]], 
                                                              one_time_keyboard=True, resize_keyboard=True))
    return TICKET_ASK_TYPE

def ticket_handle_type(update: Update, context):
    print("Handling ticket type selection")
    ticket_type = update.message.text
    if ticket_type not in ["🔴 Raise a Complaint", "📦 Place an Order"]:
        print(f"Invalid ticket type selected: {ticket_type}")
        update.message.reply_text("❌ Please select a valid option.")
        return TICKET_ASK_TYPE
    
    # Store the ticket type (clean version without emojis)
    if "Complaint" in ticket_type:
        context.user_data["ticket_type"] = "Complaint"
        prompt_text = "Please describe your complaint. You can send a text message or upload a photo with a caption."
    else:
        context.user_data["ticket_type"] = "Order"
        prompt_text = "Please describe your order details. You can send a text message or upload a photo with a caption."
    
    print(f"Ticket type selected: {context.user_data['ticket_type']}")
    update.message.reply_text(prompt_text, reply_markup=ReplyKeyboardRemove())
    return TICKET_ASK_ISSUE

def ticket_handle_issue(update: Update, context):
    print("Handling ticket issue submission")
    issue_text = update.message.text or update.message.caption or ""
    photo = update.message.photo[-1] if update.message.photo else None
    local_path = None
    gfile = None
    image_url = ""
    image_hash = ""

    if not issue_text and not photo:
        update.message.reply_text("❌ Please provide a description or upload a photo with a caption.")
        return TICKET_ASK_ISSUE

    progress_msg = None
    if photo:
        try:
            print(f"Photo file_id: {photo.file_id}, file_size: {photo.file_size}")
            if photo.file_size > 10 * 1024 * 1024:
                update.message.reply_text("❌ Image too large (max 10MB allowed).")
                return TICKET_ASK_ISSUE

            file = None
            for attempt in range(3):
                try:
                    file = photo.get_file()
                    print(f"File path: {file.file_path}, file_size: {file.file_size}")
                    break
                except Exception as e:
                    print(f"Error getting file info (attempt {attempt + 1}): {e}")
                    if attempt == 2:
                        update.message.reply_text("❌ Error accessing image file. Please try uploading again.")
                        return TICKET_ASK_ISSUE
                    time.sleep(2)

            emp_name = context.user_data.get("emp_name", "User")
            current_date = context.user_data["date"]
            timestamp_suffix = int(time.time())
            safe_emp_name = sanitize_filename(emp_name)
            filename = f"tickets/{safe_emp_name}_Ticket_{context.user_data['ticket_id']}_{current_date}_{timestamp_suffix}.jpg"
            local_filename = f"{safe_emp_name}_Ticket_{context.user_data['ticket_id']}_{current_date}_{timestamp_suffix}.jpg"
            local_path = os.path.join("/tmp", local_filename)

            print(f"Downloading to: {local_path}")
            os.makedirs("/tmp", exist_ok=True)
            if os.path.exists(local_path):
                os.remove(local_path)

            download_success = False
            for attempt in range(3):
                try:
                    print(f"Download attempt {attempt + 1}")
                    file.download(custom_path=local_path)
                    if os.path.exists(local_path):
                        file_size = os.path.getsize(local_path)
                        if file_size > 0:
                            print(f"Download successful on attempt {attempt + 1}. File size: {file_size} bytes")
                            download_success = True
                            break
                        else:
                            print(f"Download attempt {attempt + 1} failed: File is empty")
                            if os.path.exists(local_path):
                                os.remove(local_path)
                    else:
                        print(f"Download attempt {attempt + 1} failed: File not created")
                    time.sleep(2 ** attempt)
                except Exception as e:
                    print(f"Download attempt {attempt + 1} failed with error: {e}")
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    time.sleep(2 ** attempt)

            if not download_success:
                update.message.reply_text("❌ Failed to download image after multiple attempts. Please try again.")
                return TICKET_ASK_ISSUE

            try:
                hash_md5 = hashlib.md5()
                with open(local_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_md5.update(chunk)
                image_hash = hash_md5.hexdigest()
                print(f"Image hash computed: {image_hash}")
            except Exception as e:
                print(f"Error computing hash: {e}")
                if os.path.exists(local_path):
                    os.remove(local_path)
                update.message.reply_text("❌ Error processing image. Please try again.")
                return TICKET_ASK_ISSUE

            # Check for duplicates in Tickets sheet
            try:
                ticket_sheet = client.open_by_key(TICKET_SHEET_ID).worksheet(TAB_TICKETS)
                records = ticket_sheet.get_all_records()
                for record in records:
                    if (
                        str(record.get("Date", "")) == context.user_data["date"] and
                        str(record.get("Outlet", "")) == context.user_data["outlet"] and
                        str(record.get("Submitted By", "")) == context.user_data["emp_name"].replace("_", " ") and
                        str(record.get("Image Hash", "")) == image_hash
                    ):
                        print("Duplicate image detected")
                        if os.path.exists(local_path):
                            os.remove(local_path)
                        update.message.reply_text("❌ Duplicate image detected. Please retake the photo.")
                        return TICKET_ASK_ISSUE
            except Exception as e:
                print(f"Error checking duplicates in Tickets sheet: {e}")

            progress_msg = update.message.reply_text("⏳ Uploading image to Google Drive...")

            upload_success = False
            image_url = None

            for attempt in range(3):
                try:
                    print(f"Upload attempt {attempt + 1} to Google Drive")
                    if attempt > 0:
                        global drive
                        try:
                            drive = setup_drive()
                        except Exception as drive_error:
                            print(f"Failed to recreate drive connection: {drive_error}")
                            continue

                    if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
                        raise Exception("Local file is missing or empty")

                    gfile = drive.CreateFile({
                        'title': filename,
                        'parents': [{'id': TICKET_DRIVE_FOLDER_ID}],
                        'supportsAllDrives': True
                    })

                    gfile.SetContentFile(local_path)
                    gfile.Upload(param={
                        'supportsAllDrives': True,
                        'supportsTeamDrives': True
                    })
                    print(f"Upload completed for attempt {attempt + 1}")

                    if not gfile.get('id'):
                        raise Exception("Upload completed but no file ID received")

                    try:
                        gfile.InsertPermission({
                            'type': 'anyone',
                            'value': 'anyone',
                            'role': 'reader'
                        })
                        print("Permissions set successfully")
                    except Exception as perm_error:
                        print(f"Permission setting failed: {perm_error}")

                    file_id = gfile.get('id')
                    if not file_id:
                        raise Exception("No file ID available")

                    url_candidates = []
                    try:
                        if gfile.get('alternateLink'):
                            url_candidates.append(gfile['alternateLink'])
                    except:
                        pass
                    try:
                        if gfile.get('webViewLink'):
                            url_candidates.append(gfile['webViewLink'])
                    except:
                        pass
                    try:
                        if gfile.get('webContentLink'):
                            url_candidates.append(gfile['webContentLink'])
                    except:
                        pass

                    url_candidates.extend([
                        f"https://drive.google.com/file/d/{file_id}/view",
                        f"https://drive.google.com/open?id={file_id}"
                    ])

                    for url in url_candidates:
                        if url and url.startswith('http'):
                            image_url = url
                            break

                    if image_url:
                        print(f"Upload successful! URL: {image_url}")
                        upload_success = True
                        break
                    else:
                        raise Exception("No valid URL could be generated")

                except Exception as e:
                    print(f"Upload attempt {attempt + 1} failed: {e}")
                    if gfile and gfile.get('id'):
                        try:
                            gfile.Delete()
                        except:
                            pass
                    gfile = None
                    if attempt < 2:
                        time.sleep(3 * (attempt + 1))

            if not upload_success or not image_url:
                cleanup_file_safely(local_path)
                try:
                    progress_msg.edit_text("❌ Failed to upload image to Google Drive after multiple attempts.")
                except:
                    update.message.reply_text("❌ Failed to upload image to Google Drive after multiple attempts.")
                return TICKET_ASK_ISSUE

            cleanup_file_safely(local_path)

            try:
                progress_msg.edit_text("✅ Image uploaded successfully!")
            except:
                update.message.reply_text("✅ Image uploaded successfully!")

        except Exception as e:
            print(f"Unexpected error in ticket image upload: {e}")
            cleanup_file_safely(local_path)
            if gfile and gfile.get('id'):
                try:
                    gfile.Delete()
                except:
                    pass
            update.message.reply_text("❌ Unexpected error during image upload. Please contact admin if the issue persists.")
            return TICKET_ASK_ISSUE

    # Save ticket to Tickets tab with ticket type
    try:
        ticket_sheet = client.open_by_key(TICKET_SHEET_ID).worksheet(TAB_TICKETS)
        headers = ticket_sheet.row_values(1)
        if not headers:
            headers = ["Ticket ID", "Date", "Outlet", "Submitted By", "Issue Description", "Image Link", "Image Hash", "Status", "Assigned To", "Action Taken", "Type"]
            ticket_sheet.update('A1:K1', [headers])
        
        row_data = [
            context.user_data["ticket_id"],
            context.user_data["date"],
            context.user_data["outlet"],
            context.user_data["emp_name"].replace("_", " "),
            issue_text,
            image_url,
            image_hash,
            "Open",
            "",  # Assigned To (empty initially)
            "",  # Action Taken (empty initially)
            context.user_data["ticket_type"]  # Type as last column
        ]
        for attempt in range(3):
            try:
                ticket_sheet.append_row(row_data)
                print(f"Successfully saved ticket {context.user_data['ticket_id']} to Tickets tab")
                break
            except Exception as e:
                print(f"Error saving to Tickets tab (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    time.sleep(2)
                else:
                    update.message.reply_text("❌ Error saving ticket. Please contact admin.")
                    return ConversationHandler.END
    except Exception as e:
        print(f"Failed to save ticket: {e}")
        update.message.reply_text("❌ Error saving ticket. Please contact admin.")
        return ConversationHandler.END

    # Send confirmation with ticket type
    ticket_type_display = context.user_data["ticket_type"]
    update.message.reply_text(f"✅ {ticket_type_display} ticket {context.user_data['ticket_id']} raised successfully!", 
                             reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def cleanup_file_safely(file_path):
    """Safely delete a file with multiple attempts and proper error handling"""
    if not file_path or not os.path.exists(file_path):
        return
    
    for attempt in range(5):
        try:
            if attempt > 0:
                time.sleep(0.5 * attempt)
            
            import gc
            gc.collect()
            
            os.remove(file_path)
            print(f"Successfully cleaned up file: {file_path}")
            return
            
        except PermissionError as e:
            print(f"File cleanup attempt {attempt + 1} failed (permission): {e}")
            if attempt == 4:
                print(f"Warning: Could not clean up file {file_path}. It will be cleaned up later.")
        except Exception as e:
            print(f"File cleanup attempt {attempt + 1} failed: {e}")
            if attempt == 4:
                print(f"Warning: Could not clean up file {file_path}. It will be cleaned up later.")

def test_drive_connection():
    try:
        file_list = drive.ListFile({
            'q': f"'{DRIVE_FOLDER_ID}' in parents",
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True
        }).GetList()
        print(f"Drive connection successful. Found {len(file_list)} files in checklist folder.")
        file_list = drive.ListFile({
            'q': f"'{TICKET_DRIVE_FOLDER_ID}' in parents",
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True
        }).GetList()
        print(f"Drive connection successful. Found {len(file_list)} files in tickets folder.")
        return True
    except Exception as e:
        print(f"Drive connection test failed: {e}")
        return False

def cancel(update: Update, context):
    update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def reset(update: Update, context):
    update.message.reply_text("🔁 Reset successful. You can now use /start again.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# === Dispatcher & Webhook ===
@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        print(f"Received update: {update}")
        dispatcher.process_update(update)
        return "OK"
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return "Error", 500

@app.route("/", methods=["GET"])
def health_check():
    return "AOD Bot is running!"

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
            CHECKLIST_ASK_IMAGE: [MessageHandler(Filters.photo, cl_handle_image_upload)],
            TICKET_ASK_CONTACT: [MessageHandler(Filters.contact, ticket_handle_contact)],
            TICKET_ASK_TYPE: [MessageHandler(Filters.text & ~Filters.command, ticket_handle_type)],  # New state
            TICKET_ASK_ISSUE: [MessageHandler(Filters.text | Filters.photo, ticket_handle_issue)]
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
        print(f"getMe response: {response_data}")
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
    
