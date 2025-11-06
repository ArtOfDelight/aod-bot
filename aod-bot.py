import os
import re
import math
import datetime
import uuid
import hashlib
import time
import threading
import google.generativeai as genai
import json
from PIL import Image
import io
from werkzeug.utils import secure_filename
from zoneinfo import ZoneInfo
from flask import Flask, request
from google.cloud import vision
from google.oauth2 import service_account
import gspread.exceptions
from telegram import (
    Bot, Update, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.error import BadRequest
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler,
    CallbackQueryHandler, Filters, ConversationHandler
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
import requests
from io import BytesIO

MANAGER_CHAT_ID = 1225343546  # Replace with the actual Telegram chat ID
INDIA_TZ = ZoneInfo("Asia/Kolkata")

# === CHECKLIST REMINDER GROUP CHAT IDS ===
CHECKLIST_REMINDER_GROUPS = {
    "Indiranagar": -1002948281335,
    "Rajajinagar": -1003066421667,
    "Kalyanagar": -1002759362664,
    "Residency road": -1002790081068,
    "Arekere": -1002841341154,
    "Bellandur": -1002994210052,
    "Sahakarnagar": -1002997273776,
    "Whitefield": -1002946796668,
    "Jayanagar": -1002940144068,
    "Koramangala": -1003065122566,
    "HSR": -1003082789513
}

# === CONFIGURATION ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://aod-bot-t2ux.onrender.com"
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDS_FILE = "service_account.json"
SHEET_NAME = "AOD Master App"
TICKET_SHEET_ID = "1FYXr8Wz0ddN3mFi-0AQbI6J_noi2glPbJLh44CEMUnE"
ALLOWANCE_SHEET_ID = "1XmKondedSs_c6PZflanfB8OFUsGxVoqi5pUPvscT8cs"
TRAVEL_SHEET_ID = "1FYXr8Wz0ddN3mFi-0AQbI6J_noi2glPbJLh44CEMUnE"  # Travel Allowance sheet
POWER_STATUS_SHEET_ID = "1LWUBiFNKWXMKAGvUFfyoxFpR42LcRr2Zsl9JYgMIKPs"
TAB_POWER_STATUS = "Form responses 1"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # Add this after ALLOWANCE_SHEET_ID
TAB_NAME_TRAVEL = "Travel Allowance"
TAB_NAME_ROSTER = "Roster"
TAB_NAME_OUTLETS = "Outlets"
TAB_NAME_EMP_REGISTER = "EmployeeRegister"
TAB_NAME_SHIFTS = "Shifts"
TAB_CHECKLIST = "ChecklistQuestions"
TAB_RESPONSES = "ChecklistResponses"
TAB_SUBMISSIONS = "ChecklistSubmissions"
TAB_TICKETS = "Tickets"
TAB_NAME_ALLOWANCE = "allowance"
LOCATION_TOLERANCE_METERS = 50
IMAGE_FOLDER = "checklist"
TICKET_FOLDER = "tickets"
ACTIVITY_TRACKER_SHEET_ID = "1lQYE49QXPw4al7rSZMnaMKUytGckYYd85nico-D_weE"
TAB_NAME_ACTIVITY = "Activity"
TAB_NAME_ACTIVITY_BACKEND = "Activity Backend"

# UPDATED FOLDER IDs - Use the new ones from the shared drive
DRIVE_FOLDER_ID = "1FJuTky2XPUSNMAC41SOQ-TFKzSq9Wd7i"  # Checklist folder in shared drive
TICKET_DRIVE_FOLDER_ID = "1frXb-FRKRPPDql4l_VxUJ8r9xgdSfRj-"  # Tickets folder in shared drive
SHARED_DRIVE_ID = "0AEmGXk8Yd_pdUk9PVA"  # The shared drive root

# === Employee Chat ID Mapping ===
EMPLOYEE_CHAT_IDS = {
    "jimmy": 7723630977,
    "prajesha": 7548733205,
    "jonathan": 5661711252,
    "henry khongsai": 7983568192,
    "kai": 5911348182,
    "mang khogin haokip": 7956138483,
    "jangnu": 8400579657,
    "chong": 7640224130,
    "thaimei": 6803230292,
    "thangboi": 7433782718,
    "minthang": 7846028575,
    "jin": 7653545568,
    "zansung": 8090423149,
    "guang": 7166706276,
    "pau": 5395582583,
    "jangminlun": 6544050111,
    "risat": 5071738315,
    "obed": 7968852570,
    "sailo": 8137803384,
    "len kipgen": 8043563257,
    "william": 7639147592,
    "lamgouhao": 8063801577,
    "ismael": 8274977654,
    "margaret": 7396448359,
    "boikho": 7275588643,
    "hoi": 8324448967,
    "jona": 8195835325,
    "biraj bhai": 8170226011,
    "kaiku": 7879774728,
    "henry kom": 7834312007,
    "mimin": 7570430343,
    "puia": 6924505764,
    "sang": 7271784467,
    "mangboi": 5797297006,
    "mary": 8203671511
}

# Global variables for reminder tracking
reminder_status = {}  # Format: {emp_id: {"last_reminder": datetime, "reminders_sent": count}}
reminder_lock = threading.Lock()

# Global variables for checklist reminder tracking
checklist_reminder_status = {}  # Format: {slot: {"last_reminder": datetime}}
checklist_reminder_lock = threading.Lock()

# Global variables for power status reminder tracking
power_status_reminders = {}  # Format: {outlet: {"user_chat_id": id, "emp_name": name, "off_time": datetime, "last_reminder": datetime}}
power_status_lock = threading.Lock()

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

# === Google Vision API Setup ===
try:
    vision_creds = service_account.Credentials.from_service_account_file(CREDS_FILE)
    vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
    print("Google Vision API client initialized successfully")
except Exception as e:
    print(f"Warning: Google Vision API not initialized: {e}")
    vision_client = None

# === Google Gemini AI Setup ===
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-2.5-flash')
        print("Google Gemini AI initialized successfully")
    else:
        print("Warning: GEMINI_API_KEY not found. AI parsing will not be available.")
        gemini_model = None
except Exception as e:
    print(f"Warning: Google Gemini AI not initialized: {e}")
    gemini_model = None    

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
TICKET_ASK_CONTACT, TICKET_ASK_TYPE, TICKET_ASK_SUBTYPE, TICKET_ASK_ISSUE = range(20, 24)
ALLOWANCE_ASK_CONTACT, ALLOWANCE_ASK_TRIP_TYPE, ALLOWANCE_ASK_IMAGE = range(30, 33)
POWER_ASK_CONTACT, POWER_ASK_STATUS = range(40, 42)
VIEW_CHECKLIST_ASK_DATE, VIEW_CHECKLIST_ASK_OUTLET, VIEW_CHECKLIST_SHOW_DETAILS = range(200, 203)
CHECKLIST_STATUS_ASK_DATE = 210
KITCHEN_ASK_CONTACT = 100
KITCHEN_ASK_ACTION = 101
KITCHEN_ASK_ACTIVITY = 102 # Added TICKET_ASK_SUBTYPE

# === Checklist Reminder Functions ===
def send_checklist_reminder_to_groups(slot):
    """Send checklist reminders to all outlet groups"""
    try:
        current_time = datetime.datetime.now(INDIA_TZ).strftime("%H:%M")
        current_date = datetime.datetime.now(INDIA_TZ).strftime("%d/%m/%Y")
        
        # Create reminder message based on slot
        slot_emojis = {
            "Morning": "🌅",
            "Mid Day": "🌞", 
            "Closing": "🌙"
        }
        
        emoji = slot_emojis.get(slot, "📋")
        message = (
            f"{emoji} CHECKLIST REMINDER {emoji}\n\n"
            f"📋 Don't forget to fill the {slot} checklist!\n"
            f"📅 Date: {current_date}\n"
            f"⏰ Time: {current_time}\n\n"
            f"Use https://t.me/attaodbot to access the bot and fill your checklist.\n"
            f"⚠️ Please ensure all staff complete their checklist on time."
        )
        
        successful_sends = 0
        failed_sends = 0
        
        for outlet_name, chat_id in CHECKLIST_REMINDER_GROUPS.items():
            try:
                bot.send_message(chat_id=chat_id, text=message)
                print(f"Sent {slot} checklist reminder to {outlet_name} (Chat ID: {chat_id})")
                successful_sends += 1
                time.sleep(0.1)  # Small delay to avoid rate limiting
            except Exception as e:
                print(f"Failed to send {slot} checklist reminder to {outlet_name} (Chat ID: {chat_id}): {e}")
                failed_sends += 1
        
        print(f"Checklist reminder summary: {successful_sends} successful, {failed_sends} failed")
        
        # Send summary to manager
        try:
            summary_message = (
                f"📊 {slot} Checklist Reminder Summary\n"
                f"✅ Successful: {successful_sends}\n"
                f"❌ Failed: {failed_sends}\n"
                f"⏰ Sent at: {current_time}"
            )
            bot.send_message(chat_id=MANAGER_CHAT_ID, text=summary_message)
        except Exception as e:
            print(f"Failed to send summary to manager: {e}")
            
    except Exception as e:
        print(f"Error in send_checklist_reminder_to_groups: {e}")

def check_and_send_checklist_reminders():
    """Check if it's time to send checklist reminders"""
    try:
        now = datetime.datetime.now(INDIA_TZ)
        current_time = now.time()
        current_date = now.strftime("%Y-%m-%d")
        
        # Define reminder times (start of each slot)
        morning_start = datetime.time(9, 0)   # 9:00 AM
        midday_start = datetime.time(16, 0)   # 4:00 PM  
        closing_start = datetime.time(23, 0)  # 11:00 PM
        
        # Check which slot we should remind for
        slot_to_remind = None
        
        # Check Morning slot (send reminder at 9:00 AM)
        if current_time.hour == morning_start.hour and current_time.minute == morning_start.minute:
            slot_to_remind = "Morning"
        
        # Check Mid Day slot (send reminder at 4:00 PM)
        elif current_time.hour == midday_start.hour and current_time.minute == midday_start.minute:
            slot_to_remind = "Mid Day"
        
        # Check Closing slot (send reminder at 11:00 PM)
        elif current_time.hour == closing_start.hour and current_time.minute == closing_start.minute:
            slot_to_remind = "Closing"
        
        if slot_to_remind:
            with checklist_reminder_lock:
                # Check if we already sent reminder for this slot today
                reminder_key = f"{slot_to_remind}_{current_date}"
                last_reminder = checklist_reminder_status.get(reminder_key)
                
                # Only send if we haven't sent today or it's been more than 23 hours
                should_send = (
                    last_reminder is None or 
                    now - last_reminder >= datetime.timedelta(hours=23)
                )
                
                if should_send:
                    send_checklist_reminder_to_groups(slot_to_remind)
                    checklist_reminder_status[reminder_key] = now
                    print(f"Sent {slot_to_remind} checklist reminders")
                    
    except Exception as e:
        print(f"Error in check_and_send_checklist_reminders: {e}")

def check_and_send_power_reminders():
    """Check if any outlets need power ON reminders (every 30 minutes after OFF)"""
    try:
        now = datetime.datetime.now(INDIA_TZ)
        
        with power_status_lock:
            outlets_to_remove = []
            
            for outlet, reminder_data in power_status_reminders.items():
                off_time = reminder_data.get("off_time")
                last_reminder = reminder_data.get("last_reminder")
                user_chat_id = reminder_data.get("user_chat_id")
                emp_name = reminder_data.get("emp_name")
                
                # Calculate time since power was turned off
                time_since_off = now - off_time
                
                # Check if it's been at least 30 minutes since last reminder (or since OFF if first reminder)
                time_since_last = now - (last_reminder if last_reminder else off_time)
                
                if time_since_last >= datetime.timedelta(seconds=30):
                    # Send reminder
                    try:
                        minutes_off = int(time_since_off.total_seconds() / 60)
                        message = (
                            f"⚡ POWER REMINDER ⚡\n\n"
                            f"Hello {emp_name}!\n"
                            f"🏢 Outlet: {outlet}\n"
                            f"⏰ Power has been OFF for {minutes_off} minutes\n\n"
                            f"Please turn the power back ON using /start → 💡 Power Status"
                        )
                        
                        bot.send_message(chat_id=user_chat_id, text=message)
                        
                        # Update last reminder time
                        power_status_reminders[outlet]["last_reminder"] = now
                        
                        print(f"Sent power ON reminder to {emp_name} for outlet {outlet} (OFF for {minutes_off} mins)")
                        
                    except Exception as e:
                        print(f"Failed to send power reminder to {emp_name} ({user_chat_id}): {e}")
                
    except Exception as e:
        print(f"Error in check_and_send_power_reminders: {e}")

def save_power_status(emp_id, emp_name, outlet, outlet_name, status, reason=""):
    """Save power status to Google Sheet"""
    try:
        sheet = client.open_by_key(POWER_STATUS_SHEET_ID).worksheet(TAB_POWER_STATUS)
        
        # Verify headers (column 3 empty, outlet name in column 4)
        headers = sheet.row_values(1)
        expected_headers = ["Timestamp", "Status", "", "Outlet Name"]
        
        if not headers or headers != expected_headers:
            print("Setting up Power Status sheet headers")
            sheet.update('A1:D1', [expected_headers])
        
        # Create timestamp as string
        now = datetime.datetime.now(INDIA_TZ)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        
        row_data = [
            timestamp,      # Column 1: Timestamp
            status,         # Column 2: Status (Power On/Power Off)
            "",             # Column 3: Empty
            outlet_name     # Column 4: Outlet Name
        ]
        
        # Append the row
        sheet.append_row(row_data, value_input_option='USER_ENTERED')
        print(f"Saved power status: {outlet} - {status} at {timestamp}")
        return True
        
    except Exception as e:
        print(f"Error saving power status: {e}")
        import traceback
        traceback.print_exc()
        return False
    
def get_outlet_name(outlet_code):
    """Get full outlet name from outlet code"""
    try:
        sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_OUTLETS)
        records = sheet.get_all_records()
        for row in records:
            if str(row.get("Outlet Code")).strip().upper() == outlet_code.strip().upper():
                return str(row.get("Outlet Name", "")).strip()
        return outlet_code  # Return code if name not found
    except:
        return outlet_code

# Add these handlers after the allowance handlers (around line 1100)
def power_handle_contact(update: Update, context):
    """Handle contact verification for power status"""
    print("Handling power status contact verification")
    if not update.message.contact:
        update.message.reply_text("❌ Please use the button to send your contact.")
        return POWER_ASK_CONTACT
    
    phone = normalize_number(update.message.contact.phone_number)
    emp_name, outlet_code = get_employee_info(phone)
    
    if emp_name == "Unknown" or not outlet_code:
        update.message.reply_text(
            "❌ You're not rostered today or not registered.\n"
            "Please contact your manager.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    # Get employee ID
    emp_id = ""
    short_name = emp_name
    try:
        emp_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)
        emp_records = emp_sheet.get_all_records()
        for row in emp_records:
            row_phone = normalize_number(str(row.get("Phone Number", "")))
            if row_phone == phone:
                emp_id = str(row.get("Employee ID", ""))
                short_name = str(row.get("Short Name", ""))
                break
    except:
        pass
    
    # Get full outlet name
    outlet_name = get_outlet_name(outlet_code)
    
    # Get user's chat ID
    user_chat_id = update.message.from_user.id
    
    context.user_data.update({
        "emp_name": emp_name,
        "emp_id": emp_id,
        "short_name": short_name,
        "outlet": outlet_code,
        "outlet_name": outlet_name,
        "user_chat_id": user_chat_id
    })
    
    print(f"Power status contact verified: {short_name} at {outlet_code}")
    
    # Show ON/OFF options
    keyboard = [
        ["🟢 Turn Power ON", "🔴 Turn Power OFF"]
    ]
    update.message.reply_text(
        f"✅ Verified: {short_name}\n"
        f"🏢 Outlet: {outlet_name}\n\n"
        f"⚡ What would you like to do?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    
    return POWER_ASK_STATUS

def power_handle_status(update: Update, context):
    """Handle power status selection"""
    status_text = update.message.text
    
    if "ON" in status_text or "🟢" in status_text:
        status = "Power ON"
    elif "OFF" in status_text or "🔴" in status_text:
        status = "Power OFF"
    else:
        update.message.reply_text("❌ Please select a valid option.")
        return POWER_ASK_STATUS
    
    # Save immediately for both ON and OFF (no reason needed)
    success = save_power_status(
        context.user_data["emp_id"],
        context.user_data["emp_name"],
        context.user_data["outlet"],
        context.user_data["outlet_name"],
        status,
        ""  # Empty reason
    )
    
    if success:
        if status == "Power ON":
            # Stop reminders for this outlet
            outlet_code = context.user_data["outlet"]
            with power_status_lock:
                if outlet_code in power_status_reminders:
                    del power_status_reminders[outlet_code]
                    print(f"Stopped power reminders for outlet {outlet_code}")
        else:  # OFF
            # Start reminders for this outlet
            outlet_code = context.user_data["outlet"]
            now = datetime.datetime.now(INDIA_TZ)
            
            with power_status_lock:
                power_status_reminders[outlet_code] = {
                    "user_chat_id": context.user_data["user_chat_id"],
                    "emp_name": context.user_data["short_name"],
                    "off_time": now,
                    "last_reminder": None
                }
            
            print(f"Started power reminders for outlet {outlet_code}")
        
        update.message.reply_text(
            f"✅ Power turned {status} successfully!\n\n"
            f"🏢 Outlet: {context.user_data['outlet_name']}\n"
            f"⚡ Status: {status}\n"
            f"📅 Time: {datetime.datetime.now(INDIA_TZ).strftime('%d/%m/%Y %H:%M:%S')}\n\n"
            f"{'⏰ You will receive reminders every 30 minutes to turn the power back ON.' if status == 'OFF' else ''}\n"
            f"Use /start for other options.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        update.message.reply_text(
            "❌ Error saving status. Please try again or contact admin.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    return ConversationHandler.END

def kitchen_start(update: Update, context):
    """Start Kitchen Activity Tracker - Ask for phone number"""
    user_name = update.callback_query.from_user.first_name
    
    contact_button = KeyboardButton("📱 Share Phone Number", request_contact=True)
    keyboard = [[contact_button]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    update.callback_query.message.reply_text(
        f"👋 Hi {user_name}!\n\n"
        "📱 Please share your phone number to access Kitchen Activity Tracker.",
        reply_markup=reply_markup
    )
    
    return KITCHEN_ASK_CONTACT

def kitchen_handle_contact(update: Update, context):
    """Handle phone number and check if user is in Kitchen department"""
    try:
        phone = update.message.contact.phone_number
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Normalize phone number: extract last 10 digits
        normalized_phone = re.sub(r'\D', '', phone)[-10:]
        print(f"Original phone: {phone}, Normalized: {normalized_phone}")
        
        # ADMIN BYPASS: Skip all checks for phone number 8770662766 (AOD019)
        if normalized_phone == '8770662766':
            context.user_data['kitchen_employee_name'] = 'Admin'
            context.user_data['kitchen_employee_code'] = 'AOD019'
            context.user_data['kitchen_phone'] = phone
            
            # Always go directly to activity list (simplified flow)
            return show_kitchen_activities(update, context)
        
        # Get employee data from EmployeeRegister sheet
        sheet = client.open_by_key(TICKET_SHEET_ID).worksheet(TAB_NAME_EMP_REGISTER)
        all_data = sheet.get_all_records()
        
        # Find employee by phone number with flexible matching
        employee = None
        for row in all_data:
            emp_phone = str(row.get('Phone Number', '')).strip()
            
            # Normalize employee phone number (extract last 10 digits)
            emp_phone_normalized = re.sub(r'\D', '', emp_phone)[-10:]
            
            print(f"Comparing: {normalized_phone} with {emp_phone_normalized} (from {emp_phone})")
            
            # Match by last 10 digits
            if emp_phone_normalized == normalized_phone:
                employee = row
                print(f"✅ Match found: {row.get('Short Name', 'Unknown')}")
                break
        
        if not employee:
            print(f"❌ No employee found for phone: {phone} (normalized: {normalized_phone})")
            update.message.reply_text(
                "❌ Phone number not found in employee register.\n"
                "Please contact admin.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        # Check if employee is in Kitchen department
        department = str(employee.get('Department', '')).strip().lower()
        if department != 'kitchen':
            update.message.reply_text(
                "❌ Kitchen Activity Tracker is only available for Kitchen department employees.\n"
                f"Your department: {employee.get('Department', 'Unknown')}",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        # Store employee info in context
        context.user_data['kitchen_employee_name'] = employee.get('Short Name', '')
        context.user_data['kitchen_employee_code'] = employee.get('Employee ID', '')
        context.user_data['kitchen_phone'] = phone
        
        print(f"✅ Employee verified: {context.user_data['kitchen_employee_name']} ({context.user_data['kitchen_employee_code']})")
        
        # Always go directly to activity list (simplified flow)
        return show_kitchen_activities(update, context)
        
    except Exception as e:
        print(f"Error in kitchen_handle_contact: {e}")
        import traceback
        traceback.print_exc()
        update.message.reply_text(
            f"❌ Error: {str(e)}",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END


def show_kitchen_activities(update: Update, context):
    """Load and show available activities for the employee with inline active activity info"""
    try:
        employee_name = context.user_data['kitchen_employee_name']
        employee_code = context.user_data['kitchen_employee_code']
        
        # Check if there's an active activity first
        active_activity = get_active_kitchen_activity(employee_code, employee_name)
        
        # Get activities from Activity sheet
        sheet = client.open_by_key(ACTIVITY_TRACKER_SHEET_ID).worksheet(TAB_NAME_ACTIVITY)
        all_data = sheet.get_all_records()
        
        # Find activities where employee has "Yes"
        # Try by Employee Code first, then by Employee Name
        activities = []
        for row in all_data:
            activity_name = row.get('Activity', '').strip()
            
            # Try Employee Code first
            employee_value = str(row.get(employee_code, '')).strip().lower()
            
            # If not found by code, try by name
            if not employee_value:
                employee_value = str(row.get(employee_name, '')).strip().lower()
            
            if activity_name and employee_value == 'yes':
                activities.append(activity_name)
        
        if not activities:
            update.message.reply_text(
                f"❌ No activities assigned to {employee_name} ({employee_code}).\n"
                "Please contact admin.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        # Create keyboard with activities
        keyboard = [[KeyboardButton(activity)] for activity in activities]
        
        # Add "✅ Finished" button ONLY if there's an active activity
        if active_activity:
            keyboard.append([KeyboardButton("✅ Finished")])
        
        # Always add Cancel button at the end
        keyboard.append([KeyboardButton("❌ Cancel")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        
        # Build message with active activity info shown inline
        if active_activity:
            # Calculate running duration
            start_time = active_activity['start_time']
            duration = calculate_running_duration(start_time)
            
            message = (
                f"🟢 *Active Activity*\n\n"
                f"Activity: *{active_activity['activity']}*\n"
                f"Started: {active_activity['date']} at {start_time}\n"
                f"Duration: {duration}\n\n"
                f"━━━━━━━━━━━━━━━━━\n\n"
                f"👨‍🍳 *Select Next Activity*\n"
                f"Employee: {employee_name} ({employee_code})\n\n"
                f"💡 Tip: Starting a new activity will automatically stop the current one."
            )
        else:
            message = (
                f"👨‍🍳 *Kitchen Activity Tracker*\n\n"
                f"Employee: {employee_name} ({employee_code})\n\n"
                f"📋 Select an activity to start:"
            )
        
        update.message.reply_text(
            message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
        return KITCHEN_ASK_ACTIVITY
        
    except Exception as e:
        print(f"Error in show_kitchen_activities: {e}")
        import traceback
        traceback.print_exc()
        update.message.reply_text(
            f"❌ Error loading activities: {str(e)}",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

def kitchen_handle_action(update: Update, context):
    """Handle action when activity is already running - simplified to just show activities"""
    action = update.message.text.strip()
    
    if action == "❌ Cancel":
        update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    # This function is no longer needed since we removed the intermediate screen
    # Just redirect to show_kitchen_activities which handles everything
    # This is kept for backward compatibility but shouldn't be reached
    return show_kitchen_activities(update, context)


def kitchen_handle_activity_selection(update: Update, context):
    """Handle activity selection and start tracking"""
    selected_activity = update.message.text.strip()
    
    if selected_activity == "❌ Cancel":
        update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    try:
        employee_name = context.user_data['kitchen_employee_name']
        employee_code = context.user_data['kitchen_employee_code']
        
        # Check if user clicked "✅ Finished" button
        if selected_activity == "✅ Finished":
            return kitchen_stop_activity(update, context)
        
        # Get the activity backend sheet
        sheet = client.open_by_key(ACTIVITY_TRACKER_SHEET_ID).worksheet(TAB_NAME_ACTIVITY_BACKEND)
        all_data = sheet.get_all_values()
        
        # Check for active activity
        headers = all_data[0]
        
        # Try to find 'Employee Code' column first, fallback to 'Name'
        try:
            emp_code_idx = headers.index('Employee Code')
            use_code = True
        except ValueError:
            emp_code_idx = headers.index('Name')
            use_code = False
        
        end_time_idx = headers.index('End Time')
        activity_idx = headers.index('Activity')
        start_time_idx = headers.index('Start Time')
        duration_idx = headers.index('Duration')
        
        # Find active activity (no end time)
        active_row_number = None
        active_activity_name = None
        
        for i in range(len(all_data) - 1, 0, -1):  # Start from bottom (most recent)
            row = all_data[i]
            
            # Check by employee code first, then by name
            if use_code:
                match = row[emp_code_idx] == employee_code
            else:
                match = row[emp_code_idx] == employee_name
            
            if match and not row[end_time_idx]:
                active_row_number = i + 1  # 1-based indexing
                active_activity_name = row[activity_idx]
                break
        
        # AUTO-STOP: If there's an active activity, stop it before starting new one
        if active_row_number:
            print(f"🔄 Auto-stopping current activity: {active_activity_name}")
            
            now = datetime.datetime.now(INDIA_TZ)
            end_time = now.strftime('%H:%M:%S')
            
            # Get start time and calculate duration
            start_time_str = all_data[active_row_number - 1][start_time_idx].replace("'", "")
            duration = calculate_duration(start_time_str, end_time)
            
            # Stop the previous activity using batch_update
            from gspread.utils import rowcol_to_a1
            end_time_cell = rowcol_to_a1(active_row_number, end_time_idx + 1)
            duration_cell = rowcol_to_a1(active_row_number, duration_idx + 1)
            sheet.batch_update([
                {'range': end_time_cell, 'values': [[end_time]]},
                {'range': duration_cell, 'values': [[duration]]}
            ], value_input_option='USER_ENTERED')
            
            print(f"✅ Stopped previous activity: {active_activity_name} (Duration: {duration})")
        
        # Start new activity
        now = datetime.datetime.now(INDIA_TZ)
        date = now.strftime('%Y-%m-%d')
        start_time = now.strftime('%H:%M:%S')
        
        # Append new row - store Employee Code if column exists
        if use_code:
            new_row = [
                employee_code,  # Store Employee Code
                date,
                start_time,
                '',  # End time (empty)
                selected_activity,
                ''  # Duration (empty until stopped)
            ]
        else:
            new_row = [
                employee_name,  # Fallback to name
                date,
                start_time,
                '',
                selected_activity,
                ''
            ]
        
        # ⭐ CRITICAL: Use USER_ENTERED to let Google Sheets format date/time properly
        sheet.append_row(new_row, value_input_option='USER_ENTERED')
        
        # Build success message
        success_message = [f"✅ *Activity Started!*\n"]
        
        # If we auto-stopped a previous activity, mention it
        if active_row_number:
            success_message.append(f"⏹️ Stopped: {active_activity_name} ({duration})\n")
        
        success_message.extend([
            f"👤 Employee: {employee_name} ({employee_code})",
            f"📋 New Activity: {selected_activity}",
            f"📅 Date: {date}",
            f"⏰ Start Time: {start_time}\n",
            f"Use /start → Kitchen to finish this activity when done."
        ])
        
        update.message.reply_text(
            "\n".join(success_message),
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
        )
        
        return ConversationHandler.END
        
    except Exception as e:
        print(f"Error in kitchen_handle_activity_selection: {e}")
        import traceback
        traceback.print_exc()
        update.message.reply_text(
            f"❌ Error starting activity: {str(e)}",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END


# ============================================================================
# ALSO UPDATE: kitchen_stop_activity function
# Use batch_update with value_input_option for better performance
# ============================================================================

def kitchen_stop_activity(update: Update, context):
    """Stop the active activity and calculate duration"""
    try:
        employee_name = context.user_data['kitchen_employee_name']
        employee_code = context.user_data['kitchen_employee_code']
        
        sheet = client.open_by_key(ACTIVITY_TRACKER_SHEET_ID).worksheet(TAB_NAME_ACTIVITY_BACKEND)
        all_data = sheet.get_all_values()
        
        headers = all_data[0]
        
        # Try to find 'Employee Code' column first, fallback to 'Name'
        try:
            emp_code_idx = headers.index('Employee Code')
            use_code = True
        except ValueError:
            emp_code_idx = headers.index('Name')
            use_code = False
        
        start_time_idx = headers.index('Start Time')
        end_time_idx = headers.index('End Time')
        activity_idx = headers.index('Activity')
        duration_idx = headers.index('Duration')
        
        # Find active activity (most recent row without end time)
        row_to_update = None
        row_number = None
        
        for i in range(len(all_data) - 1, 0, -1):  # Start from bottom
            row = all_data[i]
            
            # Check by employee code first, then by name
            if use_code:
                match = row[emp_code_idx] == employee_code
            else:
                match = row[emp_code_idx] == employee_name
            
            if match and not row[end_time_idx]:
                row_to_update = row
                row_number = i + 1  # +1 for 1-based indexing
                break
        
        if not row_to_update:
            update.message.reply_text(
                "❌ No active activity found to stop.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        # Calculate end time and duration
        now = datetime.datetime.now(INDIA_TZ)
        end_time = now.strftime('%H:%M:%S')
        
        # Get start time and calculate duration
        start_time_str = row_to_update[start_time_idx].replace("'", "")  # Remove apostrophe if present (legacy data)
        duration = calculate_duration(start_time_str, end_time)
        
        # ⭐ FIXED: Use batch_update with USER_ENTERED instead of update_cell
        # This properly formats the time values in Google Sheets
        from gspread.utils import rowcol_to_a1
        end_time_cell = rowcol_to_a1(row_number, end_time_idx + 1)
        duration_cell = rowcol_to_a1(row_number, duration_idx + 1)
        sheet.batch_update([
            {'range': end_time_cell, 'values': [[end_time]]},
            {'range': duration_cell, 'values': [[duration]]}
        ], value_input_option='USER_ENTERED')
        
        activity_name = row_to_update[activity_idx]
        
        update.message.reply_text(
            f"✅ *Activity Stopped!*\n\n"
            f"👤 Employee: {employee_name} ({employee_code})\n"
            f"📋 Activity: {activity_name}\n"
            f"⏰ End Time: {end_time}\n"
            f"⏱️ Duration: {duration}\n\n"
            f"Great work! 👏",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
        )
        
        return ConversationHandler.END
        
    except Exception as e:
        print(f"Error in kitchen_stop_activity: {e}")
        import traceback
        traceback.print_exc()
        update.message.reply_text(
            f"❌ Error stopping activity: {str(e)}",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END


# ============================================================================
# REPLACEMENT 5: get_active_kitchen_activity function
# ============================================================================

def get_active_kitchen_activity(employee_code, employee_name):
    """
    Check if employee has an active activity
    Tries to match by Employee Code first, then falls back to Employee Name
    
    Args:
        employee_code: Employee ID/Code (e.g., 'AOD019')
        employee_name: Employee Short Name (e.g., 'Admin')
    """
    try:
        sheet = client.open_by_key(ACTIVITY_TRACKER_SHEET_ID).worksheet(TAB_NAME_ACTIVITY_BACKEND)
        all_data = sheet.get_all_values()
        
        if len(all_data) < 2:
            return None
        
        headers = all_data[0]
        
        # Try to find 'Employee Code' column first, fallback to 'Name'
        try:
            emp_code_idx = headers.index('Employee Code')
            use_code = True
            print(f"Using 'Employee Code' column for lookup")
        except ValueError:
            emp_code_idx = headers.index('Name')
            use_code = False
            print(f"Using 'Name' column for lookup (Employee Code column not found)")
        
        date_idx = headers.index('Date')
        start_time_idx = headers.index('Start Time')
        end_time_idx = headers.index('End Time')
        activity_idx = headers.index('Activity')
        
        # Find most recent active activity
        for row in reversed(all_data[1:]):
            # Check by employee code first, then by name
            if use_code:
                match = row[emp_code_idx] == employee_code
            else:
                match = row[emp_code_idx] == employee_name
            
            if match and not row[end_time_idx]:
                # Clean up apostrophes if present (legacy data)
                date = row[date_idx].replace("'", "")
                start_time = row[start_time_idx].replace("'", "")
                
                print(f"Found active activity for {employee_code}/{employee_name}: {row[activity_idx]}")
                
                return {
                    'activity': row[activity_idx],
                    'date': date,
                    'start_time': start_time
                }
        
        print(f"No active activity found for {employee_code}/{employee_name}")
        return None
        
    except Exception as e:
        print(f"Error in get_active_kitchen_activity: {e}")
        import traceback
        traceback.print_exc()
        return None


def calculate_duration(start_time_str, end_time_str):
    """Calculate duration between start and end time - Returns total minutes only"""
    try:
        # Parse times
        start = datetime.datetime.strptime(start_time_str, '%H:%M:%S')
        end = datetime.datetime.strptime(end_time_str, '%H:%M:%S')
        
        # Calculate difference
        diff = end - start
        
        # Handle negative duration (overnight shift)
        if diff.total_seconds() < 0:
            diff = diff + datetime.timedelta(days=1)
        
        # Convert to total minutes only (no units)
        total_minutes = int(diff.total_seconds() / 60)
        
        return str(total_minutes)
        
    except Exception as e:
        print(f"Error calculating duration: {e}")
        return "0"


def calculate_running_duration(start_time_str):
    """Calculate running duration from start time to now - Returns total minutes only"""
    try:
        now = datetime.datetime.now(INDIA_TZ)
        
        # Parse start time
        start = datetime.datetime.strptime(start_time_str, '%H:%M:%S')
        start = start.replace(year=now.year, month=now.month, day=now.day)
        
        # Calculate difference
        diff = now - start
        
        # Convert to total minutes only (no units)
        total_minutes = int(diff.total_seconds() / 60)
        
        return str(total_minutes)
        
    except Exception as e:
        print(f"Error calculating running duration: {e}")
        return "0"

# === Sign-In Reminder Functions ===
def get_employee_chat_id(emp_id, short_name):
    """Get chat ID for an employee using both emp_id and short_name"""
    if not short_name:
        return None
    
    # First try to match by short name (case insensitive)
    short_name_lower = short_name.lower().strip()
    if short_name_lower in EMPLOYEE_CHAT_IDS:
        return EMPLOYEE_CHAT_IDS[short_name_lower]
    
    # Try partial matches for names with spaces or variations
    for name in EMPLOYEE_CHAT_IDS:
        if short_name_lower in name or name in short_name_lower:
            return EMPLOYEE_CHAT_IDS[name]
    
    print(f"No chat ID found for employee: {emp_id} ({short_name})")
    return None

def check_and_send_reminders():
    """Check if any employee needs a sign-in reminder and send it"""
    try:
        now = datetime.datetime.now(INDIA_TZ)
        current_time = now.time()
        current_date = now.strftime("%d/%m/%Y")
        
        # Get today's roster
        gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE))
        roster_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
        emp_sheet = gc.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)
        
        roster_records = roster_sheet.get_all_records()
        emp_records = emp_sheet.get_all_records()
        
        # Create employee ID to name mapping
        emp_id_to_name = {
            str(row.get("Employee ID")).strip(): row.get("Short Name", "")
            for row in emp_records if row.get("Employee ID")
        }
        
        for row in roster_records:
            if str(row.get("Date", "")).strip() != current_date:
                continue
                
            emp_id = str(row.get("Employee ID", "")).strip()
            short_name = emp_id_to_name.get(emp_id, "")
            outlet = str(row.get("Outlet", "")).strip()
            start_time_str = str(row.get("Start Time", "")).strip()
            signin_time = str(row.get("Sign-In Time", "")).strip()
            
            # Skip if no start time, weekly off, or already signed in
            if not start_time_str or start_time_str == "N/A" or outlet.lower() == "wo" or signin_time:
                continue
                
            try:
                start_time = datetime.datetime.strptime(start_time_str, "%H:%M:%S").time()
            except ValueError:
                print(f"Invalid start time format for {emp_id}: {start_time_str}")
                continue
            
            # Calculate reminder time (start time + 10 minutes)
            start_datetime = datetime.datetime.combine(now.date(), start_time)
            reminder_time = (start_datetime + datetime.timedelta(minutes=10)).time()
            
            # Check if it's time for a reminder (within 1 minute window)
            if current_time >= reminder_time:
                # Check if we need to send a reminder
                with reminder_lock:
                    emp_status = reminder_status.get(emp_id, {})
                    last_reminder = emp_status.get("last_reminder")
                    reminders_sent = emp_status.get("reminders_sent", 0)
                    
                    # Send reminder if:
                    # 1. Never sent before, OR
                    # 2. Last reminder was more than 10 minutes ago
                    should_send = (
                        last_reminder is None or 
                        now - last_reminder >= datetime.timedelta(minutes=10)
                    )
                    
                    # Stop sending after 6 reminders (1 hour)
                    if reminders_sent >= 6:
                        should_send = False
                    
                    if should_send:
                        chat_id = get_employee_chat_id(emp_id, short_name)
                        if chat_id:
                            send_signin_reminder(chat_id, short_name, outlet, start_time_str)
                            
                            # Update reminder status
                            reminder_status[emp_id] = {
                                "last_reminder": now,
                                "reminders_sent": reminders_sent + 1
                            }
                            
                            print(f"Sent reminder {reminders_sent + 1} to {short_name} ({emp_id})")
                        
    except Exception as e:
        print(f"Error in check_and_send_reminders: {e}")

def send_signin_reminder(chat_id, emp_name, outlet, start_time):
    """Send sign-in reminder to an employee"""
    try:
        current_time = datetime.datetime.now(INDIA_TZ).strftime("%H:%M")
        message = (
            f"🚨 SIGN-IN REMINDER 🚨\n\n"
            f"Hello {emp_name}!\n"
            f"⏰ Your shift started at {start_time}\n"
            f"🏢 Outlet: {outlet}\n"
            f"⌚ Current time: {current_time}\n\n"
            f"Please sign in immediately using https://t.me/attaodbot"
        )
        
        bot.send_message(chat_id=chat_id, text=message)
        print(f"Reminder sent to {emp_name} (Chat ID: {chat_id})")
        
    except Exception as e:
        print(f"Failed to send reminder to {emp_name} (Chat ID: {chat_id}): {e}")

def reminder_worker():
    """Background worker that runs reminder checks every minute"""
    print("Sign-in, checklist, and power status reminder service started")
    while True:
        try:
            # Check sign-in reminders
            check_and_send_reminders()
            
            # Check checklist reminders
            check_and_send_checklist_reminders()
            
            # Check power status reminders
            check_and_send_power_reminders()
            
            time.sleep(60)  # Check every minute
        except Exception as e:
            print(f"Error in reminder_worker: {e}")
            time.sleep(60)


# Start the reminder worker thread
reminder_thread = threading.Thread(target=reminder_worker, daemon=True)
reminder_thread.start()

# === Allowance Functions ===
def extract_text_from_image(image_bytes):
    """Extract text from image using Google Vision API"""
    try:
        if vision_client is None:
            print("Vision API not initialized")
            return ""
        
        image = vision.Image(content=image_bytes)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        
        if texts:
            full_text = texts[0].description
            print(f"Extracted text: {full_text}")
            return full_text
        else:
            print("No text found in image")
            return ""
            
    except Exception as e:
        print(f"Error extracting text from image: {e}")
        import traceback
        traceback.print_exc()
        return ""

def extract_amount_from_text(text):
    """Extract monetary amount from text - Smart context-aware extraction"""
    try:
        print(f"\n=== EXTRACTING AMOUNT ===")
        print(f"Full text received:\n{text}\n")
        
        # PRIORITY 1: Look for amounts that start with ₹ symbol
        rupee_pattern = r'₹\s*(\d+(?:,\d+)*(?:\.\d+)?)'
        rupee_matches = list(re.finditer(rupee_pattern, text))
        
        if rupee_matches:
            rupee_amounts = []
            for match in rupee_matches:
                amount_str = match.group(1).replace(',', '')
                try:
                    amount = float(amount_str)
                    rupee_amounts.append(amount)
                    print(f"Found ₹ amount: {amount}")
                except ValueError:
                    continue
            
            if rupee_amounts:
                max_amount = max(rupee_amounts)
                print(f"✅ All ₹ amounts found: {rupee_amounts}")
                print(f"✅ Returning largest ₹ amount: {max_amount}")
                return max_amount
        
        print("⚠️ No ₹ symbol found, using context-aware extraction...")
        
        # PRIORITY 2: Context-aware extraction
        lines = text.split('\n')
        context_keywords = [
            'oneway', 'one way', 'auto', 'ride', 'fare', 'total', 'pay', 
            'paid', 'booking', 'amount', 'charge', 'cost'
        ]
        
        candidates = []
        
        for i, line in enumerate(lines):
            line_lower = line.lower().strip()
            
            if not line_lower:
                continue
            
            has_context = any(keyword in line_lower for keyword in context_keywords)
            
            if i > 0:
                prev_line_lower = lines[i-1].lower().strip()
                has_context = has_context or any(keyword in prev_line_lower for keyword in context_keywords)
            if i < len(lines) - 1:
                next_line_lower = lines[i+1].lower().strip()
                has_context = has_context or any(keyword in next_line_lower for keyword in context_keywords)
            
            if has_context or i < 10:
                number_pattern = r'\b(\d{1,5}(?:\.\d{1,2})?)\b'
                matches = re.finditer(number_pattern, line)
                
                for match in matches:
                    num_str = match.group(1)
                    surrounding = line[max(0, match.start()-5):min(len(line), match.end()+5)]
                    
                    if ':' in surrounding or 'am' in surrounding.lower() or 'pm' in surrounding.lower():
                        continue
                    if any(month in line_lower for month in ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']):
                        continue
                    if 'km' in line_lower or 'meter' in line_lower or 'm' in surrounding.lower():
                        continue
                    
                    try:
                        num_val = float(num_str)
                        if num_val < 10 or num_val > 10000:
                            continue
                        
                        candidates.append({
                            'amount': num_val,
                            'line': line.strip(),
                            'line_num': i,
                            'has_keyword': has_context
                        })
                        print(f"Candidate found: ₹{num_val} in line {i}: '{line.strip()}'")
                    except ValueError:
                        continue
        
        if not candidates:
            print("❌ No candidate amounts found")
            return None
        
        keyword_candidates = [c for c in candidates if c['has_keyword']]
        early_line_candidates = [c for c in candidates if c['line_num'] < 10]
        
        if keyword_candidates:
            best = max(keyword_candidates, key=lambda x: x['amount'])
            print(f"✅ Selected amount with keyword context: ₹{best['amount']}")
            return best['amount']
        elif early_line_candidates:
            best = max(early_line_candidates, key=lambda x: x['amount'])
            print(f"✅ Selected amount from early lines: ₹{best['amount']}")
            return best['amount']
        elif candidates:
            best = max(candidates, key=lambda x: x['amount'])
            print(f"✅ Selected largest candidate amount: ₹{best['amount']}")
            return best['amount']
        
        print("❌ No valid amounts found")
        return None
            
    except Exception as e:
        print(f"Error extracting amount: {e}")
        import traceback
        traceback.print_exc()
        return None

def validate_ai_amount_with_ocr(ai_amount, ocr_text):
    """
    Strictly validate AI extracted amount against OCR text
    Returns: (is_valid, confidence, actual_amount_from_ocr)
    """
    print(f"\n=== STRICT VALIDATION ===")
    print(f"AI Amount: ₹{ai_amount}")
    print(f"OCR Text length: {len(ocr_text)} chars")
    
    if not ocr_text:
        print("⚠️ No OCR text for validation")
        return (True, "medium", ai_amount)  # Allow if no OCR
    
    # Extract all numbers from OCR text that could be amounts
    amount_pattern = r'₹\s*(\d+(?:\.\d{1,2})?)'
    ocr_amounts = []
    
    for match in re.finditer(amount_pattern, ocr_text):
        try:
            amt = float(match.group(1))
            if 10 <= amt <= 50000:  # Reasonable range
                ocr_amounts.append(amt)
                print(f"Found ₹ amount in OCR: {amt}")
        except:
            continue
    
    # Check if AI amount matches any OCR amount EXACTLY
    ai_amount_rounded = round(ai_amount, 2)
    
    for ocr_amt in ocr_amounts:
        if abs(ocr_amt - ai_amount_rounded) < 0.01:  # Exact match
            print(f"✅ EXACT MATCH: AI ₹{ai_amount} matches OCR ₹{ocr_amt}")
            return (True, "high", ai_amount)
    
    # If no exact match, check if AI amount is close to any OCR amount
    for ocr_amt in ocr_amounts:
        diff_percent = abs(ocr_amt - ai_amount) / ai_amount * 100
        if diff_percent <= 5:  # Within 5%
            print(f"⚠️ CLOSE MATCH: AI ₹{ai_amount} vs OCR ₹{ocr_amt} (diff: {diff_percent:.1f}%)")
            # Use OCR amount instead of AI amount since they're close
            return (True, "medium", ocr_amt)
    
    # Check if any OCR amount is significantly different
    if ocr_amounts:
        print(f"❌ MISMATCH: AI says ₹{ai_amount} but OCR shows: {ocr_amounts}")
        # Return the most reasonable OCR amount
        largest_ocr = max(ocr_amounts)
        return (False, "low", largest_ocr)
    
    # No ₹ amounts found in OCR, try finding plain numbers
    plain_pattern = r'\b(\d{2,5})\b'
    plain_numbers = []
    
    for match in re.finditer(plain_pattern, ocr_text):
        try:
            num = float(match.group(1))
            if 10 <= num <= 50000:
                plain_numbers.append(num)
        except:
            continue
    
    # Check if AI amount appears as plain number
    ai_int = int(ai_amount) if ai_amount == int(ai_amount) else ai_amount
    if ai_int in plain_numbers:
        print(f"✅ Found AI amount {ai_int} as plain number in OCR")
        return (True, "medium", ai_amount)
    
    # Check for close plain numbers
    for num in plain_numbers:
        if abs(num - ai_amount) < 0.01:
            print(f"✅ EXACT MATCH with plain number: {num}")
            return (True, "high", ai_amount)
    
    print(f"⚠️ Could not validate AI amount ₹{ai_amount} in OCR text")
    print(f"Plain numbers found: {plain_numbers}")
    
    if plain_numbers:
        # Use the number closest to AI amount
        closest = min(plain_numbers, key=lambda x: abs(x - ai_amount))
        return (False, "low", closest)
    
    return (False, "low", ai_amount)


def extract_order_details_with_ai(image_bytes, order_type="Blinkit", skip_validation=False):
    """
    Use Google Gemini AI to extract order details from image with optional validation
    Returns: dict with 'total_amount', 'items', 'confidence'
    """
    try:
        if not gemini_model:
            print("⚠️ Gemini AI not available, falling back to regex extraction")
            return extract_order_details_fallback(image_bytes, order_type)
        
        print(f"\n=== AI EXTRACTION STARTED ({order_type}) ===")
        
        # STEP 1: Extract text using Vision API for validation (only if validation enabled)
        ocr_text = ""
        if not skip_validation:
            print("Step 1: Extracting text with Vision API for validation...")
            ocr_text = extract_text_from_image(image_bytes)
            
            if not ocr_text:
                print("⚠️ Vision API couldn't extract text, proceeding with AI only")
            else:
                print(f"Vision API extracted {len(ocr_text)} characters")
        else:
            print("Step 1: Validation skipped for Blinkit/Instamart orders")
        
        # Convert bytes to PIL Image
        image = Image.open(io.BytesIO(image_bytes))
        
        # Create prompt based on order type
        if order_type == "Blinkit":
            prompt = """
You are analyzing a food delivery or grocery order screenshot (Blinkit, Instamart, Swiggy, etc.).

CRITICAL: Extract ONLY the information that is CLEARLY VISIBLE in the image. DO NOT guess or make up any numbers.

Please extract the following information and return it as a JSON object:

{
  "total_amount": <final total amount in rupees as a number>,
  "items": [
    {
      "name": "<item name>",
      "quantity": "<quantity with unit, e.g., '8 x 500g' or '4'>",
      "price": <final price in rupees as a number>
    }
  ]
}

STRICT Rules:
1. For total_amount: Extract the EXACT FINAL/GRAND TOTAL amount shown (not item total, MRP, or subtotal)
2. DO NOT round numbers - extract EXACTLY as shown (e.g., if it says 94, return 94, NOT 100)
3. For items: Extract ALL ordered items with their EXACT quantities and EXACT FINAL prices (after discounts)
4. Skip delivery fees, handling charges, or other non-item charges
5. If quantity has units (g, kg, ml, etc.), include them exactly as shown
6. Clean up item names (remove checkmarks, extra symbols)
7. Return ONLY valid JSON, no additional text
8. If you're unsure about any number, return an error instead of guessing

If you cannot extract the information with certainty, return:
{"error": "Could not extract order details"}
"""
        else:  # Travel/Going/Coming
            prompt = """
You are analyzing a payment receipt screenshot (auto, cab, UPI payment, etc.).

CRITICAL: Extract ONLY the information that is CLEARLY VISIBLE in the image. DO NOT guess or make up any numbers.

Please extract the payment amount and return it as a JSON object:

{
  "total_amount": <payment amount in rupees as a number>
}

STRICT Rules:
1. Extract the EXACT main payment/fare amount shown
2. DO NOT round numbers - extract EXACTLY as shown (e.g., if it says 94, return 94, NOT 100)
3. Look for keywords like: fare, total, paid, amount, charge
4. Return the largest meaningful amount if multiple amounts are present
5. Return ONLY valid JSON, no additional text
6. If you're unsure about the amount, return an error instead of guessing

If you cannot extract the amount with certainty, return:
{"error": "Could not extract amount"}
"""
        
        # STEP 2: Generate content with AI
        print("Step 2: Extracting with Gemini AI...")
        response = gemini_model.generate_content([prompt, image])
        
        print(f"AI Response received")
        print(f"Response text: {response.text[:500]}")
        
        # Parse JSON response
        response_text = response.text.strip()
        
        # Remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text.replace("```json", "").replace("```", "").strip()
        elif response_text.startswith("```"):
            response_text = response_text.replace("```", "").strip()
        
        result = json.loads(response_text)
        
        if "error" in result:
            print(f"❌ AI could not extract data: {result['error']}")
            return None
        
        # Validate and format result
        if "total_amount" not in result:
            print("❌ No total_amount in AI response")
            return None
        
        ai_amount = result["total_amount"]
        
        # STEP 3: Validation (only if not skipped)
        if not skip_validation:
            print(f"Step 3: STRICT validation of AI amount (₹{ai_amount})...")
            
            is_valid, confidence, corrected_amount = validate_ai_amount_with_ocr(ai_amount, ocr_text)
            
            # If validation failed or found different amount, use corrected amount
            if not is_valid or abs(corrected_amount - ai_amount) > 0.01:
                print(f"⚠️ Amount corrected: ₹{ai_amount} → ₹{corrected_amount}")
                result["total_amount"] = corrected_amount
                result["amount_corrected"] = True
                result["original_ai_amount"] = ai_amount
            else:
                result["amount_corrected"] = False
            
            result["confidence"] = confidence
            result["validation_warning"] = (confidence == "low")
            
            # Sanity checks on final amount
            final_amount = result["total_amount"]
            if final_amount < 10 or final_amount > 50000:
                print(f"⚠️ WARNING: Amount ₹{final_amount} outside normal range (₹10-₹50,000)")
                result["confidence"] = "low"
                result["validation_warning"] = True
        else:
            # No validation - trust AI completely
            result["amount_corrected"] = False
            result["confidence"] = "high"
            result["validation_warning"] = False
            print(f"✅ Using AI amount directly (no validation): ₹{ai_amount}")
        
        # Ensure items list exists for Blinkit orders
        if order_type == "Blinkit" and "items" not in result:
            result["items"] = []
        
        print(f"✅ AI Extraction completed with {result.get('confidence', 'unknown')} confidence")
        print(f"   Final Amount: ₹{result['total_amount']}")
        if result.get("amount_corrected"):
            print(f"   (Corrected from AI's ₹{result['original_ai_amount']})")
        if order_type == "Blinkit" and result.get("items"):
            print(f"   Items extracted: {len(result['items'])}")
        
        return result
        
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse AI response as JSON: {e}")
        print(f"Response was: {response.text}")
        return None
    except Exception as e:
        print(f"❌ Error in AI extraction: {e}")
        import traceback
        traceback.print_exc()
        return None

def extract_order_details_fallback(image_bytes, order_type):
    """
    Fallback to Vision API + regex if Gemini AI is not available
    """
    try:
        print("Using fallback Vision API extraction")
        extracted_text = extract_text_from_image(image_bytes)
        
        if not extracted_text:
            return None
        
        amount = extract_amount_from_text(extracted_text)
        
        if amount is None:
            return None
        
        result = {"total_amount": amount}
        
        if order_type == "Blinkit":
            items = extract_items_from_text(extracted_text)
            result["items"] = items
        
        return result
        
    except Exception as e:
        print(f"Fallback extraction failed: {e}")
        return None

def extract_items_from_text(text):
    """Extract ordered items with quantities and prices from text - IMPROVED VERSION"""
    try:
        print(f"\n=== EXTRACTING ITEMS ===")
        print(f"Full text received:\n{text}\n")
        
        items = []
        lines = text.split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            # Skip common non-item lines
            skip_keywords = ['order', 'summary', 'arrived', 'download', 'invoice', 'item details', 
                           'delivery', 'total', 'bill', 'mrp', 'discount', 'charge', 'help',
                           'completed', 'rate now', 'how were', 'repeat order', 'view cart']
            if any(keyword in line.lower() for keyword in skip_keywords):
                i += 1
                continue
            
            # Pattern 1: Instamart format - "4 x [Combo] Britannia Milk Bikis Biscuits ₹484.0"
            pattern1 = r'^(\d+)\s*x\s*(.+?)\s*₹\s*([\d,]+(?:\.\d+)?)\s*$'
            match1 = re.match(pattern1, line)
            
            if match1:
                quantity = match1.group(1)
                item_name = match1.group(2).strip()
                # Remove checkmarks and extra symbols
                item_name = re.sub(r'^[✓✔\s]+', '', item_name).strip()
                price = match1.group(3).replace(',', '')
                
                items.append({
                    'name': item_name,
                    'quantity': quantity,
                    'price': float(price)
                })
                print(f"✓ Pattern 1: {quantity} x {item_name} - ₹{price}")
                i += 1
                continue
            
            # Pattern 2: Blinkit multi-line format
            # Line i: Item name (e.g., "Whole Farm Grocery Cashew")
            # Line i+1: Quantity format (e.g., "500 g x 8")
            # Line i+2: Prices (e.g., "₹6,000 ₹3,640")
            
            if i + 2 < len(lines):
                next_line = lines[i + 1].strip()
                price_line = lines[i + 2].strip()
                
                # Check if next line contains quantity pattern like "500 g x 8" or "8 x 500g"
                qty_patterns = [
                    r'^\(?(\d+(?:-\d+)?)\s*(?:g|kg|ml|l|pc|pcs|nos?|pack)?\)?\s*x\s*(\d+)$',
                    r'^(\d+)\s*x\s*\(?(\d+(?:-\d+)?)\s*(?:g|kg|ml|l|pc|pcs|nos?|pack)?\)?$',
                    r'^(\d+)\s*x\s*$'
                ]
                
                qty_match = None
                for pattern in qty_patterns:
                    qty_match = re.match(pattern, next_line, re.IGNORECASE)
                    if qty_match:
                        break
                
                if qty_match:
                    # Extract all prices from third line
                    price_pattern = r'₹\s*([\d,]+(?:\.\d+)?)'
                    prices = re.findall(price_pattern, price_line)
                    
                    if prices:
                        item_name = line
                        # Remove checkmarks and clean up
                        item_name = re.sub(r'^[✓✔\s]+', '', item_name).strip()
                        
                        # Build quantity string
                        if len(qty_match.groups()) >= 2:
                            quantity = f"{qty_match.group(1)} x {qty_match.group(2)}"
                        else:
                            quantity = qty_match.group(1)
                        
                        # Use the last price (final/discounted price)
                        final_price = prices[-1].replace(',', '')
                        
                        items.append({
                            'name': item_name,
                            'quantity': quantity,
                            'price': float(final_price)
                        })
                        print(f"✓ Pattern 2: {quantity} x {item_name} - ₹{final_price}")
                        i += 3  # Skip the 3 lines we just processed
                        continue
            
            i += 1
        
        # Remove duplicates
        unique_items = []
        seen = set()
        for item in items:
            key = (item['name'].lower().strip(), item['price'])
            if key not in seen:
                seen.add(key)
                unique_items.append(item)
        
        print(f"\n✅ Total unique items extracted: {len(unique_items)}")
        for item in unique_items:
            print(f"  - {item['quantity']} x {item['name']} - ₹{item['price']}")
        
        return unique_items
        
    except Exception as e:
        print(f"Error extracting items: {e}")
        import traceback
        traceback.print_exc()
        return []

def format_items_for_sheet(items):
    """Format items list as a string for Google Sheets"""
    if not items:
        return ""
    
    formatted = []
    for item in items:
        formatted.append(f"{item['quantity']} x {item['name']} - ₹{item['price']}")
    
    return " | ".join(formatted) 

def extract_travel_locations_with_ai(image_bytes):
    """
    Use Google Gemini AI to extract start and end locations from travel receipt
    Returns: dict with 'start_location' and 'end_location'
    """
    try:
        if not gemini_model:
            print("⚠️ Gemini AI not available for location extraction")
            return None
        
        print(f"\n=== AI LOCATION EXTRACTION STARTED ===")
        
        image = Image.open(io.BytesIO(image_bytes))
        
        prompt = """
You are analyzing a travel/transportation receipt (auto, cab, Uber, Ola, Rapido, etc.).

Please extract the pickup and drop locations and return them as a JSON object:

{
  "start_location": "<pickup/starting location>",
  "end_location": "<drop/ending location>"
}

Rules:
1. Look for keywords like: pickup, from, start, origin, source
2. Look for keywords like: drop, to, destination, end
3. Extract full location names/addresses when available
4. If exact addresses are present, use them; otherwise use area/landmark names
5. Return ONLY valid JSON, no additional text
6. Be concise but complete with location names

If you cannot extract the locations, return:
{"error": "Could not extract locations"}
"""
        
        response = gemini_model.generate_content([prompt, image])
        
        print(f"AI Location Response received")
        
        response_text = response.text.strip()
        
        if response_text.startswith("```json"):
            response_text = response_text.replace("```json", "").replace("```", "").strip()
        elif response_text.startswith("```"):
            response_text = response_text.replace("```", "").strip()
        
        result = json.loads(response_text)
        
        if "error" in result:
            print(f"❌ AI could not extract locations: {result['error']}")
            return None
        
        if "start_location" not in result or "end_location" not in result:
            print("❌ Incomplete location data in AI response")
            return None
        
        print(f"✅ AI Location Extraction successful!")
        print(f"   Start: {result['start_location']}")
        print(f"   End: {result['end_location']}")
        
        return result
        
    except Exception as e:
        print(f"❌ Error in AI location extraction: {e}")
        return None

def save_travel_allowance(emp_id, emp_name, outlet, trip_type, amount):
    """Save travel allowance (Going/Coming) to Travel Allowance sheet"""
    try:
        sheet = client.open_by_key(TRAVEL_SHEET_ID).worksheet(TAB_NAME_TRAVEL)
        
        # Verify headers
        headers = sheet.row_values(1)
        expected_headers = ["Travel ID", "Date", "Employee ID", "Outlet", "Going Amount", "Coming Amount"]
        
        if not headers or headers != expected_headers:
            print("Setting up Travel Allowance sheet headers")
            sheet.update('A1:F1', [expected_headers])
        
        current_date = datetime.datetime.now(INDIA_TZ).strftime("%Y-%m-%d")
        
        # Check if there's already a row for this employee on this date
        all_records = sheet.get_all_records()
        existing_row_index = None
        
        for idx, record in enumerate(all_records, start=2):  # start=2 because row 1 is headers
            if (str(record.get("Date", "")).strip() == current_date and 
                str(record.get("Employee ID", "")).strip() == emp_id):
                existing_row_index = idx
                print(f"Found existing row at index {idx} for Employee ID {emp_id} on {current_date}")
                break
        
        if existing_row_index:
            # Update existing row
            if trip_type == "Going":
                col = "E"  # Going Amount column
            else:  # Coming
                col = "F"  # Coming Amount column
            
            cell_address = f"{col}{existing_row_index}"
            sheet.update(cell_address, [[amount]])
            print(f"Updated {trip_type} amount (₹{amount}) in cell {cell_address}")
            
        else:
            # Create new row
            # Generate Travel ID (you can customize this format)
            travel_id = f"TRV-{current_date}-{emp_id}"
            
            going_amount = amount if trip_type == "Going" else ""
            coming_amount = amount if trip_type == "Coming" else ""
            
            row_data = [
                travel_id,
                current_date,
                emp_id,  # Changed from emp_name to emp_id
                outlet,
                going_amount,
                coming_amount
            ]
            
            sheet.append_row(row_data)
            print(f"Created new travel row: {travel_id} - Employee ID {emp_id} - {trip_type}: ₹{amount}")
        
        return True
        
    except Exception as e:
        print(f"Error saving to Travel Allowance sheet: {e}")
        import traceback
        traceback.print_exc()
        return False

def save_blinkit_order(emp_id, emp_name, outlet, amount, items_list, extracted_text):
    """Save Blinkit order to allowance sheet"""
    try:
        sheet = client.open_by_key(ALLOWANCE_SHEET_ID).worksheet(TAB_NAME_ALLOWANCE)
        
        headers = sheet.row_values(1)
        expected_headers = ["Date", "Time", "Employee ID", "Employee Name", "Outlet", 
                           "Order Type", "Amount", "Items Ordered", "Extracted Text"]
        
        if not headers:
            sheet.update('A1:I1', [expected_headers])
        elif len(headers) < 9:
            sheet.update('A1:I1', [expected_headers])
        
        now = datetime.datetime.now(INDIA_TZ)
        row_data = [
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            emp_id,
            emp_name,
            outlet,
            "Blinkit",  # Order Type
            amount,
            items_list,
            extracted_text[:500]
        ]
        
        sheet.append_row(row_data)
        print(f"Saved Blinkit order: {emp_name} - ₹{amount}")
        print(f"Items: {items_list[:200]}")
        return True
        
    except Exception as e:
        print(f"Error saving to allowance sheet: {e}")
        import traceback
        traceback.print_exc()
        return False      

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

def checklist_status_start(update: Update, context):
    """Start checklist status command - Ask for date"""
    try:
        # Generate date options
        today = datetime.datetime.now(INDIA_TZ)
        dates = []
        date_labels = []

        # Add "Today"
        dates.append(today.strftime("%d/%m/%Y"))
        date_labels.append("📅 Today")

        # Add last 6 days
        for i in range(1, 7):
            past_date = today - datetime.timedelta(days=i)
            dates.append(past_date.strftime("%d/%m/%Y"))
            if i == 1:
                date_labels.append("📅 Yesterday")
            else:
                date_labels.append(f"📅 {past_date.strftime('%d %b %Y')}")

        # Store dates in context
        context.user_data['checklist_status_dates'] = dates

        # Create keyboard with date options
        keyboard = []
        for label in date_labels:
            keyboard.append([KeyboardButton(label)])
        keyboard.append([KeyboardButton("❌ Cancel")])

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        update.message.reply_text(
            "📋 *Checklist Completion Status*\n\n"
            "Please select a date to view checklist status:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

        return CHECKLIST_STATUS_ASK_DATE

    except Exception as e:
        print(f"Error in checklist_status_start: {e}")
        import traceback
        traceback.print_exc()
        update.message.reply_text(
            "❌ Error starting checklist status view. Please try again.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

def checklist_status_handle_date(update: Update, context):
    """Handle date selection and show checklist status"""
    try:
        selected_text = update.message.text.strip()

        # Handle cancel
        if selected_text == "❌ Cancel":
            update.message.reply_text(
                "❌ Checklist status view cancelled.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END

        # Extract date index from selection
        dates = context.user_data.get('checklist_status_dates', [])

        # Map selection to date
        date_map = {
            "📅 Today": 0,
            "📅 Yesterday": 1,
        }

        selected_date = None
        if selected_text in date_map:
            selected_date = dates[date_map[selected_text]]
        else:
            # Try to match other date formats
            for i, date in enumerate(dates):
                date_obj = datetime.datetime.strptime(date, "%d/%m/%Y")
                if selected_text == f"📅 {date_obj.strftime('%d %b %Y')}":
                    selected_date = date
                    break

        if not selected_date:
            update.message.reply_text(
                "❌ Invalid date selection. Please try again.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END

        # Send loading message
        progress_msg = update.message.reply_text(
            f"⏳ Fetching checklist completion status for {selected_date}...",
            reply_markup=ReplyKeyboardRemove()
        )

        # Fetch data from API with date parameter
        response = requests.get(
            "https://restaurant-dashboard-nqbi.onrender.com/api/checklist-completion-status",
            params={"date": selected_date},
            timeout=30
        )
        response.raise_for_status()

        data = response.json()

        if not data.get("success"):
            update.message.reply_text("❌ Failed to fetch checklist status data.")
            return ConversationHandler.END

        outlets = data.get("data", [])

        if not outlets:
            update.message.reply_text(f"📋 No checklist data available for {selected_date}.")
            return ConversationHandler.END

        # Format the message
        message_parts = ["```"]
        message_parts.append(f"📋 *Checklist Status - {selected_date}*")
        message_parts.append("")

        for outlet in outlets:
            outlet_name = outlet.get("outletName", "Unknown")
            outlet_code = outlet.get("outletCode", "N/A")
            overall_status = outlet.get("overallStatus", "Unknown")
            completion_pct = outlet.get("completionPercentage", 0)
            total_employees = outlet.get("totalScheduledEmployees", 0)
            last_submission = outlet.get("lastSubmissionTime", "")

            # Status emoji
            status_emoji = "✅" if overall_status == "Completed" else "🟡" if overall_status == "Partial" else "❌"

            message_parts.append(f"{status_emoji} *{outlet_name} ({outlet_code})*")
            message_parts.append(f"Overall: {overall_status} ({completion_pct}%)")
            message_parts.append(f"Scheduled Employees: {total_employees}")

            # Time slot details
            time_slots = outlet.get("timeSlotStatus", [])
            if time_slots:
                message_parts.append("Time Slots:")
                for slot in time_slots:
                    slot_name = slot.get("timeSlot", "Unknown")
                    slot_status = slot.get("status", "Unknown")
                    employee_count = slot.get("employeeCount", 0)
                    slot_emoji = "✅" if slot_status == "Completed" else "❌"

                    slot_line = f"  {slot_emoji} {slot_name}: {slot_status}"
                    if slot_status == "Completed":
                        submitted_by = slot.get("submittedBy", "Unknown")
                        timestamp = slot.get("timestamp", "")
                        slot_line += f"\n     By: {submitted_by}"
                        if timestamp:
                            slot_line += f"\n     At: {timestamp}"
                    slot_line += f"\n     Employees: {employee_count}"
                    message_parts.append(slot_line)

            if last_submission:
                message_parts.append(f"Last Submission: {last_submission}")

            message_parts.append("")

        # Remove last empty line
        if message_parts[-1] == "":
            message_parts.pop()

        message_parts.append("```")

        # Send the formatted message
        full_message = "\n".join(message_parts)

        # Telegram has a 4096 character limit, so split if needed
        if len(full_message) > 4000:
            # Delete the progress message first
            try:
                progress_msg.delete()
            except:
                pass

            # Split by outlet
            current_msg = ["```", f"📋 *Checklist Status - {selected_date}*", ""]
            message_sent = False

            for outlet in outlets:
                outlet_name = outlet.get("outletName", "Unknown")
                outlet_code = outlet.get("outletCode", "N/A")
                overall_status = outlet.get("overallStatus", "Unknown")
                completion_pct = outlet.get("completionPercentage", 0)
                status_emoji = "✅" if overall_status == "Completed" else "🟡" if overall_status == "Partial" else "❌"

                outlet_info = f"{status_emoji} *{outlet_name} ({outlet_code})*: {overall_status} ({completion_pct}%)"

                # If adding this outlet would exceed limit, send current message
                if len("\n".join(current_msg)) + len(outlet_info) > 3900:
                    current_msg.append("```")
                    update.message.reply_text("\n".join(current_msg), parse_mode="Markdown")
                    message_sent = True
                    # Start new message
                    current_msg = ["```"]

                current_msg.append(outlet_info)

            current_msg.append("```")
            update.message.reply_text("\n".join(current_msg), parse_mode="Markdown")
        else:
            try:
                progress_msg.edit_text(full_message, parse_mode="Markdown")
            except BadRequest as e:
                # If message can't be edited (too old, already deleted, etc.), send a new one
                print(f"Could not edit message: {e}. Sending new message instead.")
                try:
                    progress_msg.delete()
                except:
                    pass
                update.message.reply_text(full_message, parse_mode="Markdown")

        print(f"Checklist completion status sent successfully for {selected_date}")
        return ConversationHandler.END

    except requests.exceptions.RequestException as e:
        update.message.reply_text(
            f"❌ Network error: Could not fetch data from server.\n{str(e)}",
            reply_markup=ReplyKeyboardRemove()
        )
        print(f"Error fetching checklist completion status: {e}")
        return ConversationHandler.END
    except Exception as e:
        update.message.reply_text(
            f"❌ Error generating checklist status: {e}",
            reply_markup=ReplyKeyboardRemove()
        )
        print(f"Error sending checklist status: {e}")
        import traceback
        traceback.print_exc()
        return ConversationHandler.END

def checklist_completion_status(update: Update, context):
    """Show checklist completion status for all outlets"""
    try:
        # Send loading message
        progress_msg = update.message.reply_text("⏳ Fetching checklist completion status...")

        # Fetch data from API
        response = requests.get("https://restaurant-dashboard-nqbi.onrender.com/api/checklist-completion-status", timeout=30)
        response.raise_for_status()

        data = response.json()

        if not data.get("success"):
            update.message.reply_text("❌ Failed to fetch checklist status data.")
            return

        outlets = data.get("data", [])

        if not outlets:
            update.message.reply_text("📋 No checklist data available.")
            return

        # Format the message
        message_parts = ["```"]
        message_parts.append("📋 *Checklist Completion Status*")
        message_parts.append("")

        for outlet in outlets:
            outlet_name = outlet.get("outletName", "Unknown")
            outlet_code = outlet.get("outletCode", "N/A")
            overall_status = outlet.get("overallStatus", "Unknown")
            completion_pct = outlet.get("completionPercentage", 0)
            total_employees = outlet.get("totalScheduledEmployees", 0)
            last_submission = outlet.get("lastSubmissionTime", "")

            # Status emoji
            status_emoji = "✅" if overall_status == "Completed" else "🟡" if overall_status == "Partial" else "❌"

            message_parts.append(f"{status_emoji} *{outlet_name} ({outlet_code})*")
            message_parts.append(f"Overall: {overall_status} ({completion_pct}%)")
            message_parts.append(f"Scheduled Employees: {total_employees}")

            # Time slot details
            time_slots = outlet.get("timeSlotStatus", [])
            if time_slots:
                message_parts.append("Time Slots:")
                for slot in time_slots:
                    slot_name = slot.get("timeSlot", "Unknown")
                    slot_status = slot.get("status", "Unknown")
                    employee_count = slot.get("employeeCount", 0)
                    slot_emoji = "✅" if slot_status == "Completed" else "❌"

                    slot_line = f"  {slot_emoji} {slot_name}: {slot_status}"
                    if slot_status == "Completed":
                        submitted_by = slot.get("submittedBy", "Unknown")
                        timestamp = slot.get("timestamp", "")
                        slot_line += f"\n     By: {submitted_by}"
                        if timestamp:
                            slot_line += f"\n     At: {timestamp}"
                    slot_line += f"\n     Employees: {employee_count}"
                    message_parts.append(slot_line)

            if last_submission:
                message_parts.append(f"Last Submission: {last_submission}")

            message_parts.append("")

        # Remove last empty line
        if message_parts[-1] == "":
            message_parts.pop()

        message_parts.append("```")

        # Send the formatted message
        full_message = "\n".join(message_parts)

        # Telegram has a 4096 character limit, so split if needed
        if len(full_message) > 4000:
            # Split by outlet
            current_msg = ["```", "📋 *Checklist Completion Status*", ""]

            for outlet in outlets:
                outlet_name = outlet.get("outletName", "Unknown")
                outlet_code = outlet.get("outletCode", "N/A")
                overall_status = outlet.get("overallStatus", "Unknown")
                completion_pct = outlet.get("completionPercentage", 0)
                status_emoji = "✅" if overall_status == "Completed" else "🟡" if overall_status == "Partial" else "❌"

                outlet_info = f"{status_emoji} *{outlet_name} ({outlet_code})*: {overall_status} ({completion_pct}%)"

                # If adding this outlet would exceed limit, send current message
                if len("\n".join(current_msg)) + len(outlet_info) > 3900:
                    current_msg.append("```")
                    progress_msg.edit_text("\n".join(current_msg), parse_mode="Markdown")
                    # Start new message
                    current_msg = ["```"]

                current_msg.append(outlet_info)

            current_msg.append("```")
            update.message.reply_text("\n".join(current_msg), parse_mode="Markdown")
        else:
            progress_msg.edit_text(full_message, parse_mode="Markdown")

        print(f"Checklist completion status sent successfully")

    except requests.exceptions.RequestException as e:
        update.message.reply_text(f"❌ Network error: Could not fetch data from server.\n{str(e)}")
        print(f"Error fetching checklist completion status: {e}")
    except Exception as e:
        update.message.reply_text(f"❌ Error generating checklist status: {e}")
        print(f"Error sending checklist status: {e}")
        import traceback
        traceback.print_exc()

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

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
        sheet = gspread.authorize(creds).open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
        
        # Try to get records, if header has empty cells, use alternative method
        try:
            records = sheet.get_all_records()
        except gspread.exceptions.GSpreadException as e:
            if "empty cells" in str(e):
                # Alternative: use get_all_values and manually parse
                all_values = sheet.get_all_values()
                if len(all_values) < 2:
                    print("Roster sheet has insufficient data")
                    return None, None, None, None, None
                
                headers = all_values[0]
                # Find the indices of the columns we need
                try:
                    emp_id_idx = headers.index("Employee ID")
                    date_idx = headers.index("Date")
                    outlet_idx = headers.index("Outlet")
                    signin_idx = headers.index("Sign-In Time")
                    signout_idx = headers.index("Sign-Out Time")
                except ValueError as ve:
                    print(f"Required column not found in headers: {ve}")
                    return None, None, None, None, None
                
                # Search through data rows
                for idx, row in enumerate(all_values[1:], start=2):
                    if len(row) > max(emp_id_idx, date_idx) and \
                       str(row[emp_id_idx]).strip() == emp_id and \
                       str(row[date_idx]).strip() == target_date:
                        outlet = str(row[outlet_idx]).strip() if len(row) > outlet_idx else ""
                        signin = row[signin_idx] if len(row) > signin_idx else ""
                        signout = row[signout_idx] if len(row) > signout_idx else ""
                        return outlet, signin, signout, idx, sheet
                
                print(f"No matching record found for emp_id {emp_id} on date {target_date}")
                return None, None, None, None, None
            else:
                raise

        # Normal path when no header issues
        for idx, row in enumerate(records, start=2):
            if str(row.get("Employee ID")).strip() == emp_id and str(row.get("Date")).strip() == target_date:
                return str(row.get("Outlet")).strip(), row.get("Sign-In Time"), row.get("Sign-Out Time"), idx, sheet
                
        print(f"No matching record found for emp_id {emp_id} on date {target_date}")
        return None, None, None, None, None
        
    except Exception as e:
        print(f"Error in get_outlet_row_by_emp_id: {e}")
        import traceback
        traceback.print_exc()
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
        
        # FIXED: For early morning hours (after midnight), use yesterday's roster
        now = datetime.datetime.now(INDIA_TZ)
        if now.hour < 3:  # Between 00:00 and 03:00, use yesterday
            target_date = (now - datetime.timedelta(days=1)).strftime("%d/%m/%Y")
            print(f"Early morning ({now.strftime('%H:%M')}), using yesterday's roster: {target_date}")
        else:
            target_date = now.strftime("%d/%m/%Y")
            print(f"Normal hours, using today's roster: {target_date}")
        
        for row in emp_records:
            row_phone = normalize_number(str(row.get("Phone Number", "")))
            if row_phone == phone:
                emp_name = sanitize_filename(str(row.get("Full Name", "Unknown")))
                emp_id = str(row.get("Employee ID", ""))
                roster_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_ROSTER)
                roster_records = roster_sheet.get_all_records()
                for record in roster_records:
                    if record.get("Employee ID") == emp_id and record.get("Date") == target_date:
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
    """Start command - Show options"""
    user_name = update.message.from_user.first_name
    
    keyboard = [
        [InlineKeyboardButton("📍 Sign In", callback_data="signin"), InlineKeyboardButton("📍 Sign Out", callback_data="signout")],
        [InlineKeyboardButton("✅ Checklist", callback_data="checklist")],
        [InlineKeyboardButton("💰 Travel Allowance", callback_data="allowance")],
        [InlineKeyboardButton("🎫 Ticket", callback_data="ticket")],
        [InlineKeyboardButton("⚡ Power Status", callback_data="power_status")],
        [InlineKeyboardButton("👨‍🍳 Kitchen", callback_data="kitchen")],  # ← ONLY THIS LINE IS NEW
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        f"👋 Hi {user_name}! Welcome to AOD Bot.\n\n"
        "Please select an option:",
        reply_markup=reply_markup
    )
    
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
    elif query.data == "allowance":
        query.message.reply_text("Please verify your phone number to submit allowance:", reply_markup=markup)
        return ALLOWANCE_ASK_CONTACT
    elif query.data == "power":
        query.message.reply_text("Please verify your phone number for power status:", reply_markup=markup)
        return POWER_ASK_CONTACT
    elif query.data == "kitchen":  # NEW BLOCK
        query.message.reply_text("Please verify your phone number for kitchen tracker:", reply_markup=markup)
        return KITCHEN_ASK_CONTACT
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

def get_available_time_slots():
    """Get available time slots based on current time"""
    now = datetime.datetime.now(INDIA_TZ)
    current_time = now.time()
    available_slots = []
    
    # Define time ranges for each slot
    morning_start = datetime.time(9, 0)   # 9:00 AM
    morning_end = datetime.time(13, 0)    # 1:00 PM
    
    midday_start = datetime.time(16, 0)   # 4:00 PM
    midday_end = datetime.time(19, 0)     # 7:00 PM
    
    closing_start = datetime.time(23, 0)  # 11:00 PM
    closing_end = datetime.time(3, 0)     # 3:00 AM (next day)
    
    # Check Morning slot (9 AM to 1 PM)
    if morning_start <= current_time <= morning_end:
        available_slots.append("Morning")
    
    # Check Mid Day slot (4 PM to 7 PM)  
    if midday_start <= current_time <= midday_end:
        available_slots.append("Mid Day")
    
    # Check Closing slot (11 PM to 3 AM) - spans midnight
    if current_time >= closing_start or current_time <= closing_end:
        available_slots.append("Closing")
    
    return available_slots

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
    
    # Get available time slots based on current time
    available_slots = get_available_time_slots()
    
    if not available_slots:
        current_time = datetime.datetime.now(INDIA_TZ).strftime("%H:%M")
        update.message.reply_text(
            f"❌ No checklist time slots are currently available.\n"
            f"Current time: {current_time}\n\n"
            f"Available times:\n"
            f"🌅 Morning: 9:00 AM - 1:00 PM\n"
            f"🌞 Mid Day: 4:00 PM - 7:00 PM\n" 
            f"🌙 Closing: 11:00 PM - 3:00 AM",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    # Create keyboard with only available slots
    keyboard = [available_slots]  # Put all available slots in one row
    
    current_time = datetime.datetime.now(INDIA_TZ).strftime("%H:%M")
    update.message.reply_text(
        f"⏰ Select time slot (Current time: {current_time}):",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return CHECKLIST_ASK_SLOT

def cl_load_questions(update: Update, context):
    print("Loading checklist questions for selected slot")
    slot = update.message.text
    
    # Verify the selected slot is still valid (in case time changed during interaction)
    available_slots = get_available_time_slots()
    
    if slot not in available_slots:
        print(f"Selected slot '{slot}' is no longer available")
        current_time = datetime.datetime.now(INDIA_TZ).strftime("%H:%M")
        update.message.reply_text(
            f"❌ The '{slot}' time slot is no longer available.\n"
            f"Current time: {current_time}\n"
            f"Please use /start to try again.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
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
    """
    FIXED: Now writes to ChecklistSubmissions when ALL questions are done
    This ensures one summary row per checklist, regardless of number of images
    """
    print(f"Asking checklist question {context.user_data['current_q'] + 1}")
    idx = context.user_data["current_q"]
    
    if idx >= len(context.user_data["questions"]):
        print("All checklist questions completed, saving responses")
        
        try:
            # ============================================
            # STEP 1: Save to ChecklistResponses (individual Q&A)
            # ============================================
            responses_sheet = client.open(SHEET_NAME).worksheet(TAB_RESPONSES)
            for answer in context.user_data["answers"]:
                responses_sheet.append_row([
                    context.user_data["submission_id"],
                    answer["question"],
                    answer["answer"],
                    answer.get("image_link", ""),
                    answer.get("image_hash", "")
                ])
            print(f"✓ Saved {len(context.user_data['answers'])} responses to ChecklistResponses")
            
            # ============================================
            # STEP 2: Save to ChecklistSubmissions (ONE summary row)
            # ============================================
            submissions_sheet = client.open(SHEET_NAME).worksheet(TAB_SUBMISSIONS)
            
            # Collect all image hashes for this submission
            all_image_hashes = []
            for answer in context.user_data["answers"]:
                if answer.get("image_hash"):
                    all_image_hashes.append(answer.get("image_hash"))
            
            # Create comma-separated string of image hashes
            image_hashes_str = ", ".join(all_image_hashes) if all_image_hashes else ""
            
            # Write ONE summary row to ChecklistSubmissions
            submissions_sheet.append_row([
                context.user_data["submission_id"],                # Column A: Submission ID
                context.user_data["date"],                         # Column B: Date
                context.user_data["slot"],                         # Column C: Time Slot
                context.user_data["outlet"],                       # Column D: Outlet
                context.user_data["emp_name"].replace("_", " "),   # Column E: Submitted By
                context.user_data["timestamp"],                    # Column F: Timestamp
                image_hashes_str                                   # Column G: Image Hash(es)
            ])
            print(f"✓ Saved submission summary to ChecklistSubmissions with ID: {context.user_data['submission_id']}")
            
            # Success message with details
            update.message.reply_text(
                f"✅ Checklist completed successfully!\n\n"
                f"📋 Submission ID: {context.user_data['submission_id']}\n"
                f"👤 Employee: {context.user_data['emp_name']}\n"
                f"🏢 Outlet: {context.user_data['outlet']}\n"
                f"⏰ Slot: {context.user_data['slot']}\n"
                f"📅 Date: {context.user_data['date']}\n"
                f"📸 Images: {len(all_image_hashes)}",
                reply_markup=ReplyKeyboardRemove()
            )
            
        except Exception as e:
            print(f"Failed to save checklist: {e}")
            import traceback
            traceback.print_exc()
            update.message.reply_text(
                f"❌ Error saving checklist: {str(e)}\n"
                f"Submission ID: {context.user_data.get('submission_id', 'N/A')}\n"
                f"Please contact admin and share this ID.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        return ConversationHandler.END
    
    # Continue with next question
    q_data = context.user_data["questions"][idx]
    if q_data["image_required"]:
        update.message.reply_text(
            f"📷 {q_data['question']}\n\nPlease upload an image for this step.", 
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.setdefault("answers", []).append({
            "question": q_data["question"], 
            "answer": "Image Required", 
            "image_link": "",
            "image_hash": ""
        })
        return CHECKLIST_ASK_IMAGE
    else:
        update.message.reply_text(
            f"❓ {q_data['question']}",
            reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True)
        )
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
    """
    FIXED: Removed redundant ChecklistSubmissions write
    This function only handles image upload, NOT submission tracking
    """
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
        
        # ============================================
        # 🔥 REMOVED: Duplicate image check
        # This was checking ChecklistSubmissions which creates circular logic
        # If you need duplicate detection, implement it differently
        # ============================================
        
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
        
        # Store image link and hash in the current answer
        context.user_data["answers"][-1]["image_link"] = image_url
        context.user_data["answers"][-1]["image_hash"] = image_hash
        
        # ============================================
        # 🔥 REMOVED: Write to ChecklistSubmissions
        # This was creating duplicate rows (one per image)
        # Now we only write to ChecklistSubmissions when ALL questions are complete
        # ============================================
        
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
    
    # Move to next question
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
    
    # Show three main ticket categories
    keyboard = [
        ["🔧 Repair and Maintenance"],
        ["❓ Difficulty in Order"], 
        ["📦 Place an Order"]
    ]
    update.message.reply_text(
        "📝 What type of ticket would you like to raise?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return TICKET_ASK_TYPE

def ticket_handle_type(update: Update, context):
    print("Handling ticket type selection")
    ticket_type = update.message.text
    
    if ticket_type == "🔧 Repair and Maintenance":
        context.user_data["ticket_type"] = "Repair and Maintenance"
        context.user_data["assigned_to"] = "Nishat","Jatin"
        context.user_data["ticket_category"] = "Repair and Maintenance"
        prompt_text = "Please describe the repair or maintenance issue. You can send a text message or upload a photo with a caption."
        update.message.reply_text(prompt_text, reply_markup=ReplyKeyboardRemove())
        return TICKET_ASK_ISSUE
        
    elif ticket_type == "❓ Difficulty in Order":
        context.user_data["ticket_type"] = "Difficulty in Order"
        context.user_data["assigned_to"] = ""  # No specific assignment mentioned
        context.user_data["ticket_category"] = "Difficulty in Order"
        prompt_text = "Please describe the difficulty you're facing with your order. You can send a text message or upload a photo with a caption."
        update.message.reply_text(prompt_text, reply_markup=ReplyKeyboardRemove())
        return TICKET_ASK_ISSUE
        
    elif ticket_type == "📦 Place an Order":
        context.user_data["ticket_type"] = "Place an Order"
        context.user_data["ticket_category"] = "Place an Order"
        # Show subcategories for "Place an Order"
        keyboard = [
            ["📋 Stock Items"],
            ["🧹 Housekeeping"],
            ["📌 Others"]
        ]
        update.message.reply_text(
            "📦 What type of order would you like to place?",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return TICKET_ASK_SUBTYPE
    
    else:
        print(f"Invalid ticket type selected: {ticket_type}")
        update.message.reply_text("❌ Please select a valid option.")
        return TICKET_ASK_TYPE

def ticket_handle_subtype(update: Update, context):
    print("Handling ticket subtype selection")
    subtype = update.message.text
    
    if subtype == "📋 Stock Items":
        context.user_data["ticket_subtype"] = "Stock Items"
        context.user_data["assigned_to"] = "Nishat & Ajay"
        prompt_text = "Please describe the stock items you need to order. You can send a text message or upload a photo with a caption."
        
    elif subtype == "🧹 Housekeeping":
        context.user_data["ticket_subtype"] = "Housekeeping"
        context.user_data["assigned_to"] = "Kim"
        prompt_text = "Please describe the housekeeping items you need to order. You can send a text message or upload a photo with a caption."
        
    elif subtype == "📌 Others":
        context.user_data["ticket_subtype"] = "Others"
        context.user_data["assigned_to"] = "Kim"
        prompt_text = "Please describe the other items you need to order. You can send a text message or upload a photo with a caption."
        
    else:
        print(f"Invalid subtype selected: {subtype}")
        update.message.reply_text("❌ Please select a valid option.")
        return TICKET_ASK_SUBTYPE
    
    print(f"Subtype selected: {context.user_data['ticket_subtype']}, assigned to: {context.user_data['assigned_to']}")
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

    # Save ticket to Tickets tab with detailed categorization and assignment
    try:
        ticket_sheet = client.open_by_key(TICKET_SHEET_ID).worksheet(TAB_TICKETS)
        headers = ticket_sheet.row_values(1)
        if not headers:
            headers = [
                "Ticket ID", "Date", "Outlet", "Submitted By", "Issue Description", 
                "Image Link", "Image Hash", "Status", "Assigned To", "Action Taken", 
                "Category"
            ]
            ticket_sheet.update('A1:L1', [headers])
        
        # Determine final ticket display information
        ticket_category = context.user_data.get("ticket_category", "")
        ticket_subtype = context.user_data.get("ticket_subtype", "")
        assigned_to = context.user_data.get("assigned_to", "")
        
        # Create full category description for confirmation message
        if ticket_subtype:
            full_category = f"{ticket_category} - {ticket_subtype}"
        else:
            full_category = ticket_category
        
        # For the spreadsheet category field: use subcategory if present, else main category
        category_for_sheet = ticket_subtype if ticket_subtype else ticket_category
        
        row_data = [
            context.user_data["ticket_id"],
            context.user_data["date"],
            context.user_data["outlet"],
            context.user_data["emp_name"].replace("_", " "),
            issue_text,
            image_url,
            image_hash,
            "Open",
            assigned_to,  # Auto-assigned based on category
            "",  # Action Taken (empty initially)
            category_for_sheet,  # Category (subcategory if present, else main category)
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

    # Send confirmation with detailed ticket information
    confirmation_message = f"✅ Ticket {context.user_data['ticket_id']} raised successfully!\n\n"
    confirmation_message += f"📋 Category: {full_category}\n"
    if assigned_to:
        confirmation_message += f"👤 Assigned to: {assigned_to}\n"
    confirmation_message += f"🕐 Created: {context.user_data['timestamp']}"
    
    update.message.reply_text(confirmation_message, reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# === Allowance Handlers ===
def allowance_handle_contact(update: Update, context):
    """Handle contact verification for allowance"""
    print("Handling allowance contact verification")
    if not update.message.contact:
        update.message.reply_text("❌ Please use the button to send your contact.")
        return ALLOWANCE_ASK_CONTACT
    
    phone = normalize_number(update.message.contact.phone_number)
    emp_name, outlet_code = get_employee_info(phone)
    
    if emp_name == "Unknown" or not outlet_code:
        update.message.reply_text(
            "❌ You're not rostered today or not registered.\n"
            "Please contact your manager.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    # Get employee ID
    emp_id = ""
    try:
        emp_sheet = client.open(SHEET_NAME).worksheet(TAB_NAME_EMP_REGISTER)
        emp_records = emp_sheet.get_all_records()
        for row in emp_records:
            row_phone = normalize_number(str(row.get("Phone Number", "")))
            if row_phone == phone:
                emp_id = str(row.get("Employee ID", ""))
                short_name = str(row.get("Short Name", ""))
                break
    except:
        short_name = emp_name
    
    context.user_data.update({
        "emp_name": emp_name,
        "emp_id": emp_id,
        "short_name": short_name,
        "outlet": outlet_code
    })
    
    # Ask for trip type
    keyboard = [
        ["🏠➡️🏢 Going (To Outlet)", "🏢➡️🏠 Coming (From Outlet)"],
        ["🛒 Blinkit/Instamart Order"]
    ]
    update.message.reply_text(
        f"✅ Verified: {short_name}\n"
        f"🏢 Outlet: {outlet_code}\n\n"
        f"🚗 What type of Reimbursements are you registering for?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    
    return ALLOWANCE_ASK_TRIP_TYPE

def allowance_handle_trip_type(update: Update, context):
    """Handle trip type selection"""
    trip_text = update.message.text
    
    if "Going" in trip_text or "TO" in trip_text:
        trip_type = "Going"
    elif "Coming" in trip_text or "FROM" in trip_text:
        trip_type = "Coming"
    elif "Blinkit" in trip_text or "blinkit" in trip_text.lower():
        trip_type = "Blinkit"
    else:
        update.message.reply_text("❌ Please select a valid option.")
        return ALLOWANCE_ASK_TRIP_TYPE
    
    context.user_data["trip_type"] = trip_type
    print(f"Trip type selected: {trip_type}")
    
    if trip_type == "Blinkit":
        prompt_text = (
            f"✅ Trip Type: {trip_type}\n\n"
            f"📸 Please upload a screenshot of your Blinkit/Instamart order.\n"
            f"The bot will automatically extract:\n"
            f"• Total amount\n"
            f"• Items ordered with prices"
        )
    else:
        prompt_text = (
            f"✅ Trip Type: {trip_type}\n\n"
            f"📸 Please upload a screenshot of your payment/allowance.\n"
            f"The bot will automatically extract the amount from the image."
        )
    
    update.message.reply_text(prompt_text, reply_markup=ReplyKeyboardRemove())
    return ALLOWANCE_ASK_IMAGE

def allowance_handle_image(update: Update, context):
    """Handle allowance image upload with AI-powered extraction"""
    
    if not update.message.photo:
        update.message.reply_text("❌ Please upload a photo/screenshot.")
        return ALLOWANCE_ASK_IMAGE
    
    try:
        processing_msg = update.message.reply_text("⏳ Processing image...")

        photo = update.message.photo[-1]
        print(f"Photo file_id: {photo.file_id}, file_size: {photo.file_size}")

        # Check file size before downloading (10MB limit)
        if photo.file_size > 10 * 1024 * 1024:
            update.message.reply_text("❌ Image too large (max 10MB allowed).")
            return ALLOWANCE_ASK_IMAGE

        file = photo.get_file()
        image_bytes = file.download_as_bytearray()
        
        trip_type = context.user_data["trip_type"]
        
        # Handle based on trip type
        if trip_type == "Blinkit":
            processing_msg.edit_text("⏳ Processing image with AI...")
            
            # Use AI extraction WITHOUT validation for Blinkit
            result = extract_order_details_with_ai(bytes(image_bytes), trip_type, skip_validation=True)
            
            if not result or "total_amount" not in result:
                processing_msg.edit_text(
                    "❌ Could not extract information from the image.\n\n"
                    "💡 Tips:\n"
                    "• Make sure the image is clear and not blurry\n"
                    "• Ensure good lighting\n"
                    "• The total amount should be visible\n"
                    "• Try taking the screenshot again"
                )
                return ALLOWANCE_ASK_IMAGE
            
            amount = result["total_amount"]
            items = result.get("items", [])
            items_formatted = format_items_for_sheet(items)
            
            # No validation warnings for Blinkit - trust AI completely
            extracted_text_backup = ""
            try:
                extracted_text_backup = extract_text_from_image(bytes(image_bytes))
            except:
                pass
            
            success = save_blinkit_order(
                context.user_data["emp_id"],
                context.user_data["emp_name"],
                context.user_data["outlet"],
                amount,
                items_formatted,
                extracted_text_backup
            )
            
            if success:
                confirmation = [
                    f"✅ Blinkit order recorded successfully!\n",
                    f"👤 Employee: {context.user_data['short_name']}",
                    f"🏢 Outlet: {context.user_data['outlet']}",
                    f"💰 Total Amount: ₹{amount:.2f}",
                ]
                
                if items:
                    confirmation.append(f"\n📦 Items Ordered ({len(items)}):")
                    for item in items[:8]:
                        item_name = item.get('name', 'Unknown')
                        item_qty = item.get('quantity', '1')
                        item_price = item.get('price', 0)
                        confirmation.append(f"  • {item_qty} x {item_name} - ₹{item_price:.2f}")
                    if len(items) > 8:
                        confirmation.append(f"  ... and {len(items) - 8} more items")
                else:
                    confirmation.append(f"\n⚠️ Note: Could not extract item details, but amount saved.")
                
                confirmation.extend([
                    f"\n📅 Date: {datetime.datetime.now(INDIA_TZ).strftime('%Y-%m-%d')}",
                    f"⏰ Time: {datetime.datetime.now(INDIA_TZ).strftime('%H:%M:%S')}",
                    f"\n✨ Extracted by AI",
                    f"\nUse /start to submit another order."
                ])
                
                processing_msg.edit_text("\n".join(confirmation))
            else:
                processing_msg.edit_text("❌ Error saving to sheet. Please try again or contact admin.")
                return ALLOWANCE_ASK_IMAGE
        
        else:
            # Travel allowance (Going/Coming) - Use AI WITH validation
            processing_msg.edit_text("⏳ Extracting amount from image...")
            
            # Try AI extraction first (with validation)
            result = extract_order_details_with_ai(bytes(image_bytes), "Travel", skip_validation=False)
            
            amount = None
            amount_corrected = False
            original_amount = 0
            
            if result and "total_amount" in result:
                # AI extraction successful
                amount = result["total_amount"]
                amount_corrected = result.get("amount_corrected", False)
                original_amount = result.get("original_ai_amount", 0)
                print(f"✅ AI extracted travel amount: ₹{amount}")
                if amount_corrected:
                    print(f"   (Corrected from ₹{original_amount})")
            else:
                # Fallback to regex method
                print("⚠️ AI extraction failed, falling back to regex method")
                extracted_text = extract_text_from_image(bytes(image_bytes))
                
                if not extracted_text:
                    processing_msg.edit_text(
                        "❌ Could not extract text from the image.\n\n"
                        "💡 Tips:\n"
                        "• Make sure the image is clear\n"
                        "• Ensure good lighting\n"
                        "• Try taking the screenshot again"
                    )
                    return ALLOWANCE_ASK_IMAGE
                
                # Extract amount using regex
                amount = extract_amount_from_text(extracted_text)
            
            if amount is None:
                processing_msg.edit_text(
                    "❌ Could not extract amount from the image.\n\n"
                    "💡 Tips:\n"
                    "• Ensure the fare/amount is clearly visible\n"
                    "• Try capturing the entire receipt\n"
                    "• Make sure the amount is in ₹ symbol or near keywords like 'fare', 'total'\n"
                    "• Try taking the screenshot again"
                )
                return ALLOWANCE_ASK_IMAGE
            
            # Extract locations using AI (for display only)
            processing_msg.edit_text("⏳ Extracting travel locations...")
            locations = extract_travel_locations_with_ai(bytes(image_bytes))
            
            # Save to Travel Allowance sheet (same structure)
            success = save_travel_allowance(
                context.user_data["emp_id"],
                context.user_data["emp_name"],
                context.user_data["outlet"],
                trip_type,
                amount
            )
            
            if success:
                confirmation = [
                    f"✅ Travel allowance recorded successfully!\n",
                    f"👤 Employee: {context.user_data['short_name']}",
                    f"🏢 Outlet: {context.user_data['outlet']}",
                    f"🚗 Trip: {trip_type}",
                    f"💰 Amount: ₹{amount:.2f}",
                ]
                
                # Show correction notice if amount was corrected by OCR
                if amount_corrected:
                    confirmation.append(f"⚠️ Amount corrected: Initially read as ₹{original_amount:.2f}, verified as ₹{amount:.2f}")
                
                # Add location info if extracted
                if locations:
                    confirmation.append(f"\n📍 Travel Details:")
                    confirmation.append(f"   From: {locations['start_location']}")
                    confirmation.append(f"   To: {locations['end_location']}")
                else:
                    confirmation.append(f"\n📍 Location details could not be extracted")
                
                confirmation.extend([
                    f"\n📅 Date: {datetime.datetime.now(INDIA_TZ).strftime('%Y-%m-%d')}",
                    f"⏰ Time: {datetime.datetime.now(INDIA_TZ).strftime('%H:%M:%S')}",
                    f"\nUse /start to submit another allowance."
                ])
                
                processing_msg.edit_text("\n".join(confirmation))
            else:
                processing_msg.edit_text("❌ Error saving to sheet. Please try again or contact admin.")
                return ALLOWANCE_ASK_IMAGE
        
        return ConversationHandler.END
        
    except Exception as e:
        print(f"Error processing allowance image: {e}")
        import traceback
        traceback.print_exc()
        update.message.reply_text("❌ Error processing image. Please try again or contact admin.")
        return ALLOWANCE_ASK_IMAGE
    
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

# === Manual Testing Commands ===
def test_reminders(update: Update, context):
    """Manual command to test sign-in reminder system"""
    check_and_send_reminders()
    update.message.reply_text("✅ Sign-in reminder check completed. Check logs for details.")

def test_checklist_reminders(update: Update, context):
    """Manual command to test checklist reminder system"""
    check_and_send_checklist_reminders()
    update.message.reply_text("✅ Checklist reminder check completed. Check logs for details.")

def send_test_checklist_reminder(update: Update, context):
    """Manual command to send test checklist reminders"""
    args = context.args
    if not args:
        update.message.reply_text("Usage: /testchecklistreminder <Morning|Mid Day|Closing>")
        return
    
    slot = ' '.join(args)
    if slot not in ["Morning", "Mid Day", "Closing"]:
        update.message.reply_text("❌ Invalid slot. Use: Morning, Mid Day, or Closing")
        return
    
    send_checklist_reminder_to_groups(slot)
    update.message.reply_text(f"✅ Test {slot} checklist reminders sent to all groups.")

def reminder_status_cmd(update: Update, context):
    """Show current reminder status"""
    message = ["📊 Sign-In Reminder Status:\n"]
    if reminder_status:
        for emp_id, status in reminder_status.items():
            last_reminder = status['last_reminder'].strftime('%H:%M:%S') if status.get('last_reminder') else 'Never'
            reminders_sent = status.get('reminders_sent', 0)
            message.append(f"Employee {emp_id}: {reminders_sent} reminders, last at {last_reminder}")
    else:
        message.append("No sign-in reminders have been sent yet today.")
    
    message.append(f"\n📋 Checklist Reminder Status:")
    if checklist_reminder_status:
        for reminder_key, last_sent in checklist_reminder_status.items():
            message.append(f"{reminder_key}: last sent at {last_sent.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        message.append("No checklist reminders have been sent yet today.")
    
    update.message.reply_text('\n'.join(message))

def view_checklist_start(update: Update, context):
    """Start viewing checklist submissions - Ask for date"""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name

    print(f"[VIEW_CHECKLIST] Command initiated by user {user_name} (ID: {user_id})")
    print(f"[VIEW_CHECKLIST] Timestamp: {datetime.datetime.now(INDIA_TZ).strftime('%Y-%m-%d %H:%M:%S')}")

    # Get last 7 days
    dates = []
    now = datetime.datetime.now(INDIA_TZ)
    for i in range(7):
        date = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        dates.append(date)

    print(f"[VIEW_CHECKLIST] Generated {len(dates)} date options for selection")

    keyboard = [[date] for date in dates]
    keyboard.append(["❌ Cancel"])

    update.message.reply_text(
        f"👋 Hi {user_name}!\n\n"
        "📅 Select a date to view checklist submissions:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )

    print(f"[VIEW_CHECKLIST] Date selection prompt sent to user {user_name}")

    return VIEW_CHECKLIST_ASK_DATE

def view_checklist_select_date(update: Update, context):
    """Handle date selection and show available outlets"""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    selected_date = update.message.text.strip()

    print(f"[VIEW_CHECKLIST] User {user_name} (ID: {user_id}) entered date selection handler")
    print(f"[VIEW_CHECKLIST] Selected date: '{selected_date}'")

    if selected_date == "❌ Cancel":
        print(f"[VIEW_CHECKLIST] User {user_name} cancelled the operation")
        update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    try:
        # Validate date format
        datetime.datetime.strptime(selected_date, "%Y-%m-%d")
        print(f"[VIEW_CHECKLIST] Date validation successful for: {selected_date}")
    except:
        print(f"[VIEW_CHECKLIST] Invalid date format entered: {selected_date}")
        update.message.reply_text("❌ Invalid date format. Please try again.")
        return VIEW_CHECKLIST_ASK_DATE

    context.user_data["selected_date"] = selected_date
    print(f"[VIEW_CHECKLIST] Date {selected_date} saved to user context")
    
    # Fetch data from API
    try:
        progress_msg = update.message.reply_text("⏳ Loading checklist data...")
        print(f"[VIEW_CHECKLIST] Fetching data from API for date: {selected_date}")

        response = requests.get("https://restaurant-dashboard-nqbi.onrender.com/api/checklist-data", timeout=30)
        response.raise_for_status()
        print(f"[VIEW_CHECKLIST] API response status: {response.status_code}")

        data = response.json()
        total_submissions = len(data.get("submissions", []))
        print(f"[VIEW_CHECKLIST] Total submissions in database: {total_submissions}")

        # Filter submissions by selected date
        submissions = [
            sub for sub in data.get("submissions", [])
            if sub.get("date") == selected_date
        ]
        print(f"[VIEW_CHECKLIST] Filtered submissions for {selected_date}: {len(submissions)}")

        if not submissions:
            print(f"[VIEW_CHECKLIST] No submissions found for date {selected_date}")
            progress_msg.edit_text(
                f"❌ No checklist submissions found for {selected_date}.\n\n"
                "Use /viewchecklist to try another date."
            )
            return ConversationHandler.END

        # Group by outlet and time slot
        outlet_groups = {}
        for sub in submissions:
            outlet = sub.get("outlet", "Unknown")
            time_slot = sub.get("timeSlot", "Unknown")
            key = f"{outlet} - {time_slot}"

            if key not in outlet_groups:
                outlet_groups[key] = []
            outlet_groups[key].append(sub)

        print(f"[VIEW_CHECKLIST] Grouped into {len(outlet_groups)} outlet/time slot combinations:")
        for key in outlet_groups:
            print(f"[VIEW_CHECKLIST]   - {key}: {len(outlet_groups[key])} submission(s)")

        # Store for later use
        context.user_data["submissions"] = submissions
        context.user_data["outlet_groups"] = outlet_groups
        print(f"[VIEW_CHECKLIST] Data stored in user context")

        # Create keyboard with outlets
        keyboard = [[outlet_key] for outlet_key in sorted(outlet_groups.keys())]
        keyboard.append(["❌ Cancel"])

        # Delete the progress message and send a new one with the keyboard
        progress_msg.delete()
        update.message.reply_text(
            f"📅 Date: {selected_date}\n"
            f"✅ Found {len(submissions)} submission(s)\n\n"
            "🏢 Select an outlet to view details:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        print(f"[VIEW_CHECKLIST] Outlet selection prompt sent to user {user_name}")

        return VIEW_CHECKLIST_ASK_OUTLET

    except requests.exceptions.Timeout:
        print(f"[VIEW_CHECKLIST] ERROR: Request timed out for user {user_name}")
        update.message.reply_text(
            "❌ Request timed out. The server took too long to respond.\n"
            "Please try again later."
        )
        return ConversationHandler.END
    except requests.exceptions.RequestException as e:
        print(f"[VIEW_CHECKLIST] ERROR: Request exception - {e}")
        update.message.reply_text(
            f"❌ Error loading data: {str(e)}\n\n"
            "Please try again later or contact admin."
        )
        return ConversationHandler.END

def view_checklist_show_outlet(update: Update, context):
    """Show checklist details for selected outlet - MEMORY-SAFE VERSION"""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    selected_outlet = update.message.text.strip()

    print(f"[VIEW_CHECKLIST] User {user_name} (ID: {user_id}) entered outlet selection handler")
    print(f"[VIEW_CHECKLIST] Selected outlet: '{selected_outlet}'")

    if selected_outlet == "❌ Cancel":
        print(f"[VIEW_CHECKLIST] User {user_name} cancelled the operation")
        update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    outlet_groups = context.user_data.get("outlet_groups", {})
    print(f"[VIEW_CHECKLIST] Available outlet groups: {list(outlet_groups.keys())}")

    if selected_outlet not in outlet_groups:
        print(f"[VIEW_CHECKLIST] ERROR: Invalid outlet selection '{selected_outlet}'")
        update.message.reply_text("❌ Invalid outlet selection.")
        return VIEW_CHECKLIST_ASK_OUTLET

    print(f"[VIEW_CHECKLIST] Valid outlet selected, proceeding to load details")

    try:
        progress_msg = update.message.reply_text("⏳ Loading checklist details...", reply_markup=ReplyKeyboardRemove())
        print(f"[VIEW_CHECKLIST] Fetching full data with responses from API")

        # Fetch full data with responses
        response = requests.get("https://restaurant-dashboard-nqbi.onrender.com/api/checklist-data", timeout=30)
        response.raise_for_status()
        print(f"[VIEW_CHECKLIST] API response status: {response.status_code}")
        data = response.json()
        
        submissions = outlet_groups[selected_outlet]
        all_responses = data.get("responses", [])
        print(f"[VIEW_CHECKLIST] Processing {len(submissions)} submission(s) for outlet '{selected_outlet}'")
        print(f"[VIEW_CHECKLIST] Total responses in database: {len(all_responses)}")

        # Process each submission
        for idx, submission in enumerate(submissions, 1):
            submission_id = submission.get("submissionId")
            print(f"[VIEW_CHECKLIST] Processing submission {idx}/{len(submissions)} - ID: {submission_id}")

            # Get responses for this submission
            sub_responses = [
                resp for resp in all_responses
                if resp.get("submissionId") == submission_id
            ]
            print(f"[VIEW_CHECKLIST] Found {len(sub_responses)} response(s) for submission {submission_id}")

            # Build message
            message_parts = [
                f"📋 **Checklist {idx}/{len(submissions)}**",
                f"",
                f"🆔 ID: {submission_id}",
                f"📅 Date: {submission.get('date')}",
                f"⏰ Time Slot: {submission.get('timeSlot')}",
                f"🏢 Outlet: {submission.get('outlet')}",
                f"👤 Submitted By: {submission.get('submittedBy')}",
                f"🕐 Timestamp: {submission.get('timestamp')}",
                f"",
                f"━━━━━━━━━━━━━━━━━",
                f"",
                f"📝 **Responses** ({len(sub_responses)} questions):",
                f""
            ]

            # Send header message
            update.message.reply_text(
                "\n".join(message_parts),
                parse_mode='Markdown'
            )
            print(f"[VIEW_CHECKLIST] Sent header message for submission {submission_id}")

            # Process responses
            for q_num, resp in enumerate(sub_responses, 1):
                question = resp.get("question", "No question")
                answer = resp.get("answer", "No answer")
                image_link = resp.get("image", "")

                # Send question and answer
                resp_text = f"**Q{q_num}:** {question}\n**A:** {answer}"
                update.message.reply_text(resp_text, parse_mode='Markdown')
                print(f"[VIEW_CHECKLIST] Sent Q{q_num} for submission {submission_id}")

                # ============================================
                # MEMORY-SAFE IMAGE HANDLING
                # ============================================
                if image_link and image_link.startswith("/api/image-proxy/"):
                    file_id = image_link.replace("/api/image-proxy/", "")
                    print(f"[VIEW_CHECKLIST] Sending image {file_id} for Q{q_num}")
                    
                    try:
                        # Use Google Drive's direct download URL
                        # Telegram fetches it directly - doesn't go through our server
                        image_url = f"https://drive.google.com/uc?export=download&id={file_id}"
                        
                        # Send photo by URL - Telegram downloads it, not our server
                        update.message.reply_photo(
                            photo=image_url,
                            caption=f"📸 Image for Q{q_num}",
                            timeout=30
                        )
                        print(f"[VIEW_CHECKLIST] Successfully sent image for Q{q_num}")
                        
                    except Exception as img_error:
                        print(f"[VIEW_CHECKLIST] ERROR sending image: {img_error}")
                        # Try alternative URL format
                        try:
                            alt_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                            update.message.reply_photo(
                                photo=alt_url,
                                caption=f"📸 Image for Q{q_num}",
                                timeout=30
                            )
                            print(f"[VIEW_CHECKLIST] Sent with alternative URL")
                        except:
                            # Last resort: clickable link with preview
                            view_url = f"https://drive.google.com/file/d/{file_id}/view"
                            update.message.reply_text(
                                f"📸 [Image for Q{q_num}]({view_url})",
                                parse_mode='Markdown',
                                disable_web_page_preview=False
                            )
                
                # Small delay to avoid rate limiting
                time.sleep(0.2)
            
            # Add separator between submissions
            if idx < len(submissions):
                update.message.reply_text("━━━━━━━━━━━━━━━━━━━━")

        progress_msg.delete()
        print(f"[VIEW_CHECKLIST] Successfully displayed all {len(submissions)} submission(s)")
        update.message.reply_text(
            f"✅ Displayed {len(submissions)} checklist submission(s).\n\n"
            "Use /viewchecklist to view more checklists."
        )
        print(f"[VIEW_CHECKLIST] View checklist operation completed for user {user_name}")

        return ConversationHandler.END

    except requests.exceptions.Timeout:
        print(f"[VIEW_CHECKLIST] ERROR: Request timed out while loading details for user {user_name}")
        update.message.reply_text(
            "❌ Request timed out while loading details.\n"
            "Please try again later."
        )
        return ConversationHandler.END
    except Exception as e:
        print(f"[VIEW_CHECKLIST] ERROR showing checklist details: {e}")
        import traceback
        traceback.print_exc()
        update.message.reply_text(
            f"❌ Error displaying checklist: {str(e)}\n\n"
            "Please try again or contact admin."
        )
        return ConversationHandler.END

# === Dispatcher & Webhook ===
@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        # Log only essential info to avoid memory issues with large binary data
        update_type = "unknown"
        if update.message:
            update_type = "message"
            if update.message.photo:
                update_type = "photo"
            elif update.message.document:
                update_type = "document"
        elif update.callback_query:
            update_type = "callback_query"
        print(f"Received update type: {update_type}, update_id: {update.update_id}")
        dispatcher.process_update(update)
        return "OK"
    except Exception as e:
        print(f"Error processing webhook: {e}")
        import traceback
        traceback.print_exc()
        return "Error", 500

@app.route("/", methods=["GET"])
def health_check():
    return "AOD Bot is running with checklist reminders!"

def setup_dispatcher():
    """Setup conversation handler"""
    
    # Main conversation handler (Sign In/Out, Checklist, Ticket, Allowance, Power, Kitchen)
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
            TICKET_ASK_TYPE: [MessageHandler(Filters.text & ~Filters.command, ticket_handle_type)],
            TICKET_ASK_SUBTYPE: [MessageHandler(Filters.text & ~Filters.command, ticket_handle_subtype)],
            TICKET_ASK_ISSUE: [MessageHandler(Filters.text | Filters.photo, ticket_handle_issue)],
            ALLOWANCE_ASK_CONTACT: [MessageHandler(Filters.contact, allowance_handle_contact)],
            ALLOWANCE_ASK_TRIP_TYPE: [MessageHandler(Filters.text & ~Filters.command, allowance_handle_trip_type)],
            ALLOWANCE_ASK_IMAGE: [MessageHandler(Filters.photo | Filters.text, allowance_handle_image)],
            POWER_ASK_CONTACT: [MessageHandler(Filters.contact, power_handle_contact)],
            POWER_ASK_STATUS: [MessageHandler(Filters.text & ~Filters.command, power_handle_status)],
            KITCHEN_ASK_CONTACT: [MessageHandler(Filters.contact, kitchen_handle_contact)],
            KITCHEN_ASK_ACTION: [MessageHandler(Filters.text & ~Filters.command, kitchen_handle_action)],
            KITCHEN_ASK_ACTIVITY: [MessageHandler(Filters.text & ~Filters.command, kitchen_handle_activity_selection)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("reset", reset)
        ]
    ))
    
    # View Checklist conversation handler
    dispatcher.add_handler(ConversationHandler(
        entry_points=[CommandHandler("viewchecklist", view_checklist_start)],
        states={
            VIEW_CHECKLIST_ASK_DATE: [MessageHandler(Filters.text & ~Filters.command, view_checklist_select_date)],
            VIEW_CHECKLIST_ASK_OUTLET: [MessageHandler(Filters.text & ~Filters.command, view_checklist_show_outlet)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ]
    ))

    # Checklist Status conversation handler
    dispatcher.add_handler(ConversationHandler(
        entry_points=[CommandHandler("checkliststatus", checklist_status_start)],
        states={
            CHECKLIST_STATUS_ASK_DATE: [MessageHandler(Filters.text & ~Filters.command, checklist_status_handle_date)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ]
    ))

    # Standalone command handlers
    dispatcher.add_handler(CommandHandler("reset", reset))
    dispatcher.add_handler(CommandHandler("statustoday", statustoday))
    dispatcher.add_handler(CommandHandler("statusyesterday", statusyesterday))
    dispatcher.add_handler(CommandHandler("getroster", getroster))
    dispatcher.add_handler(CommandHandler("testreminders", test_reminders))
    dispatcher.add_handler(CommandHandler("testchecklistreminders", test_checklist_reminders))
    dispatcher.add_handler(CommandHandler("testchecklistreminder", send_test_checklist_reminder))
    dispatcher.add_handler(CommandHandler("reminderstatus", reminder_status_cmd))

    # Set bot commands menu
    try:
        bot.set_my_commands([
            ("start", "Start the bot and access main menu"),
            ("reset", "Reset the current conversation"),
            ("viewchecklist", "View completed checklist submissions"),
            ("checkliststatus", "Show checklist completion status for all outlets"),
            ("statustoday", "Show today's sign-in status report"),
            ("statusyesterday", "Show yesterday's full attendance report"),
            ("getroster", "Show today's roster for all outlets"),
            ("testreminders", "Test sign-in reminder system (admin only)"),
            ("testchecklistreminders", "Test checklist reminder system (admin only)"),
            ("testchecklistreminder", "Send test checklist reminder (admin only)"),
            ("reminderstatus", "Show current reminder status (admin only)")
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
print("Bot started with sign-in and checklist reminder systems active!")
print("Checklist reminders will be sent to the following groups:")
for outlet_name, chat_id in CHECKLIST_REMINDER_GROUPS.items():
    print(f"  - {outlet_name}: {chat_id}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))