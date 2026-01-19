"""
Setup script for Kitchen Checklist Google Sheet
Creates the required tabs and adds sample questions
"""
import gspread
from oauth2client.service_account import ServiceAccountCredentials

CREDS_FILE = "credentials.json"
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

KITCHEN_CHECKLIST_SHEET_ID = "1pXGZfQgn6EYjcf-zSZ-saCjmp6y_p0wuu_Y0AAVYCYU"

def setup_sheet():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    client = gspread.authorize(creds)

    spreadsheet = client.open_by_key(KITCHEN_CHECKLIST_SHEET_ID)

    # Get existing sheet names
    existing_sheets = [ws.title for ws in spreadsheet.worksheets()]
    print(f"Existing sheets: {existing_sheets}")

    # Create Questions tab
    if "Questions" not in existing_sheets:
        questions_sheet = spreadsheet.add_worksheet(title="Questions", rows=100, cols=10)
        print("Created 'Questions' tab")
    else:
        questions_sheet = spreadsheet.worksheet("Questions")
        print("'Questions' tab already exists")

    # Create Responses tab
    if "Responses" not in existing_sheets:
        responses_sheet = spreadsheet.add_worksheet(title="Responses", rows=1000, cols=10)
        print("Created 'Responses' tab")
    else:
        responses_sheet = spreadsheet.worksheet("Responses")
        print("'Responses' tab already exists")

    # Create Submissions tab
    if "Submissions" not in existing_sheets:
        submissions_sheet = spreadsheet.add_worksheet(title="Submissions", rows=1000, cols=10)
        print("Created 'Submissions' tab")
    else:
        submissions_sheet = spreadsheet.worksheet("Submissions")
        print("'Submissions' tab already exists")

    # Create Assignments tab
    if "Assignments" not in existing_sheets:
        assignments_sheet = spreadsheet.add_worksheet(title="Assignments", rows=100, cols=10)
        print("Created 'Assignments' tab")
    else:
        assignments_sheet = spreadsheet.worksheet("Assignments")
        print("'Assignments' tab already exists")

    # Set up Questions headers and sample data
    questions_headers = ["Question", "Answer Type", "Image Required", "Assigned To"]
    questions_sheet.update('A1:D1', [questions_headers])

    # Sample kitchen checklist questions - all assigned to AOD019 for now
    sample_questions = [
        ["Is the kitchen floor clean and dry?", "Yes/No", "No", "AOD019"],
        ["Are all refrigerator temperatures within acceptable range (3-7°C)?", "Temperature", "No", "AOD019"],
        ["Is the freezer temperature at -18°C or below?", "Temperature", "No", "AOD019"],
        ["Are all food items properly labeled with dates?", "Yes/No", "No", "AOD019"],
        ["Is the hand washing station stocked with soap and paper towels?", "Yes/No", "No", "AOD019"],
        ["Are all cutting boards clean and sanitized?", "Yes/No", "No", "AOD019"],
        ["Is the dishwashing area clean?", "Yes/No", "No", "AOD019"],
        ["Are all storage containers properly sealed?", "Yes/No", "No", "AOD019"],
        ["Photo of today's prep station setup", "Yes/No", "Yes", "AOD019"],
        ["Is the waste disposal area clean?", "Yes/No", "No", "AOD019"],
    ]

    if questions_sheet.row_count < len(sample_questions) + 1:
        questions_sheet.add_rows(len(sample_questions) + 1 - questions_sheet.row_count)

    questions_sheet.update(f'A2:D{len(sample_questions) + 1}', sample_questions)
    print(f"Added {len(sample_questions)} sample questions")

    # Set up Responses headers
    responses_headers = ["Submission ID", "Date", "Employee Code", "Employee Name", "Question", "Answer", "Image Link", "Image Hash"]
    responses_sheet.update('A1:H1', [responses_headers])
    print("Set up Responses headers")

    # Set up Submissions headers
    submissions_headers = ["Submission ID", "Date", "Employee Code", "Employee Name", "Timestamp", "Questions Answered", "Image Hashes"]
    submissions_sheet.update('A1:G1', [submissions_headers])
    print("Set up Submissions headers")

    # Set up Assignments headers (for future use)
    assignments_headers = ["Employee Code", "Employee Name", "Question IDs"]
    assignments_sheet.update('A1:C1', [assignments_headers])
    print("Set up Assignments headers")

    print("\n✅ Kitchen Checklist sheet setup complete!")
    print(f"Sheet URL: https://docs.google.com/spreadsheets/d/{KITCHEN_CHECKLIST_SHEET_ID}")

if __name__ == "__main__":
    setup_sheet()
