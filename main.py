import os
import json
import logging
import requests
import uuid
import fitz  # PyMuPDF
from PyPDF2 import PdfReader, PdfWriter  # Required for sign_pdf
from firebase_db import save_report_links, load_report_links, remove_report_links, save_user_data, search_user_id, load_user_data
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler, CallbackContext
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import warnings
from keep_alive import keep_alive
keep_alive()

# from dotenv import load_dotenv
# load_dotenv()

warnings.filterwarnings("ignore", category=DeprecationWarning)
# Enable logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
TOKEN = os.getenv("TOKEN")
PDF_PASSWORD = os.getenv("PDF_PASSWORD")
SIGN_TEXT_1 = os.getenv("SIGN_TEXT_1")
URL = f'https://api.telegram.org/bot{TOKEN}/getUpdates'
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PAYMENT_URL = os.getenv('PAYMENT_URL')
PAYMENT_CAPTURED_DETAILS_URL= os.getenv("PAYMENT_CAPTURED_DETAILS_URL")
SHORTIO_LINK_API_KEY = os.getenv("SHORTIO_LINK_API_KEY")
SHORTIO_LINK_URL = os.getenv("SHORTIO_LINK_URL")    # Short.io API Endpoint
SHORTIO_DOMAIN = os.getenv("SHORTIO_DOMAIN")  # Example: "example.short.io"

# Load Google Drive API Credentials from environment variables
SERVICE_ACCOUNT_INFO = {
    "type": "service_account",
    "project_id": os.getenv("GOOGLE_PROJECT_ID"),
    "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("GOOGLE_PRIVATE_KEY", "").replace('\\n', '\n'),  # Convert \n into real newlines
    "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
    "client_id": os.getenv("GOOGLE_CLIENT_ID"),
    "auth_uri": os.getenv("GOOGLE_AUTH_URI"),
    "token_uri": os.getenv("GOOGLE_TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("GOOGLE_AUTH_PROVIDER_CERT"),
    "client_x509_cert_url": os.getenv("GOOGLE_CLIENT_CERT_URL"),
}

# ✅ Debugging: Check if private key is loaded correctly
if not SERVICE_ACCOUNT_INFO["private_key"].startswith("-----BEGIN PRIVATE KEY-----"):
    print("❌ ERROR: Private key is not correctly formatted.")
    exit(1)

# Authenticate with Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']
creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=creds)

# Authenticate with Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']
creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=creds)

# Define states for conversation handler
WAITING_FOR_UPLOAD_OPTION, WAITING_FOR_MULTIPLE_FILES, COLLECTING_FILES = range(100, 103)
WAITING_FOR_PAYMENT, WAITING_FOR_USER = range(103, 105)
WAITING_FOR_DELETE_ID = 105
WAITING_FOR_SEARCH_INPUT = 106  # 🔍 New state for search
WAITING_FOR_NAME = 107  # add this line


# Load existing file data or initialize an empty dictionary
DATA_FILE = "file_data.json"
report_links = {}
active_conversations = {}
# Define folders for input and edited PDFs
INPUT_FOLDER = "input_pdfs"
OUTPUT_FOLDER = "edited_pdfs"

# Ensure both folders exist
os.makedirs(INPUT_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Global variable to store the code fetched from the API.
code = None
# Define the cancel button
CANCEL_BUTTON = "🚫 Cancel"
START_BUTTON = "🤖 Start the Bot"



if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            file_content = f.read().strip()  # Remove any accidental empty spaces
            report_links = json.loads(file_content) if file_content else {}
    except json.JSONDecodeError:
        print("Warning: JSON file is corrupted. Resetting data.")
        report_links = {}
else:
    report_links = {}

def save_data():
    """Save the file data to JSON."""
    with open(DATA_FILE, "w") as f:
        json.dump(report_links, f, indent=4)

def edit_pdf(input_pdf, output_pdf, output_pdf_name, selected_text):
    doc = fitz.open(input_pdf)
    page = doc[0]

    hide_rect = fitz.Rect(30.0, 304.0, 600, 410)
    page.draw_rect(hide_rect, color=(1, 1, 1), fill=(1, 1, 1))

    rect = fitz.Rect(36, 329, 600, 400)
    page.insert_textbox(rect, selected_text, fontsize=23, fontname="helvetica-bold", color=(0, 0, 0), align=0)

    page.insert_text((36, 383), output_pdf_name, fontsize=17, fontname="helvetica-bold", color=(0, 0, 0))

    if selected_text != SIGN_TEXT_1:
        page.insert_text((402, 560), f"Digitally signed by {selected_text}", fontsize=8,
                         fontname="times-italic", color=(1, 0, 0))
    else:
        rect = fitz.Rect(382, 680, 580, 740)
        page.draw_rect(rect, color=(1, 0, 0))
        page.insert_textbox(
            rect,
            f"\n  \t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\tDigitally signed by {selected_text}\n\n"
            f" Contact to Coding Services for Plagiarism and AI checking report on telegram @coding_services.",
            fontsize=8,
            fontname="times-italic",
            color=(0, 0, 0),
            align=0
        )

    doc.save(output_pdf)
    doc.close()


def sign_pdf(pdf_file_path):
    reader = PdfReader(pdf_file_path)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    signed_pdf_path = os.path.join("edited_pdfs", os.path.basename(pdf_file_path))

    writer.encrypt(user_password="", owner_pwd=PDF_PASSWORD, permissions_flag=3)

    with open(signed_pdf_path, "wb") as f_out:
        writer.write(f_out)

    return signed_pdf_path

def verify_payment(chat_id,payment_amount):
    response = requests.get(url=PAYMENT_CAPTURED_DETAILS_URL)
    try:
        response.raise_for_status()
        data = response.json()
        for entry in data:
            if entry['user_id'] == str(chat_id):
                if entry['amount'] == str(payment_amount):
                    return True
        print("No payment details found! ")
    except requests.exceptions.HTTPError as err:
        print("HTTP Error:", err)

def short_link(long_url, title):
    # Headers
    headers = {
        "Authorization": SHORTIO_LINK_API_KEY,
        "Content-Type": "application/json"
    }
    # Payload
    data = {"domain": SHORTIO_DOMAIN,
            "originalURL": long_url,
            "title": title
            }
    response = requests.post(SHORTIO_LINK_URL, json=data, headers=headers)
    try:
        response_data = response.json()
        if "shortURL" in response_data:
            return response_data["shortURL"]
        else:
            return long_url
    except Exception as e:
        return long_url


# Function to create a reply keyboard with a Cancel button
def get_cancel_keyboard():
    return ReplyKeyboardMarkup([[CANCEL_BUTTON]], resize_keyboard=True, one_time_keyboard=True)

# Function to create a reply keyboard with a Cancel button
def get_start_keyboard():
    return ReplyKeyboardMarkup([[START_BUTTON]], resize_keyboard=True, one_time_keyboard=True)



# Add a new function to handle cancellation of the upload process
async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the upload process and reset the conversation state."""
    active_conversations[update.message.chat_id] = False
    await update.message.reply_text("⬆️ Upload process cancelled. You can start over with /upload.")
    return ConversationHandler.END

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return ConversationHandler.END
    await update.message.reply_text("♻️ Upload Process has Started...", reply_markup=get_cancel_keyboard(),parse_mode="Markdown")
    active_conversations[update.message.chat_id] = True
    keyboard = [
        [InlineKeyboardButton("📁 One File", callback_data="upload_1")],
        [InlineKeyboardButton("📂 Two Files", callback_data="upload_2")],
        [InlineKeyboardButton("📦 More than Two Files", callback_data="upload_more")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("How many files do you want to upload?", reply_markup=reply_markup)
    return WAITING_FOR_UPLOAD_OPTION

# Handle the Cancel button
async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Cancel button press."""
    active_conversations[update.message.chat_id] = False
    await update.message.reply_text(
        "Upload process cancelled.",
        reply_markup=ReplyKeyboardRemove()  # Remove the custom keyboard
    )
    return ConversationHandler.END

async def upload_option_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["files"] = []
    if query.data == "upload_1":
        context.user_data["upload_limit"] = 1
        await query.edit_message_text("📤 Please send 1 file.")
        return COLLECTING_FILES
    elif query.data == "upload_2":
        context.user_data["upload_limit"] = 2
        await query.edit_message_text("📤 Please send 2 files.")
        return COLLECTING_FILES
    else:
        await query.edit_message_text("✳️ Please enter how many files you want to upload (must be a number > 2):")
        return WAITING_FOR_MULTIPLE_FILES

async def ask_file_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    active_conversations[update.message.chat_id] = True
    if not user_input.isdigit() or int(user_input) <= 2:
        await update.message.reply_text("❌ Please enter a number greater than 2.")
        return WAITING_FOR_MULTIPLE_FILES

    context.user_data["upload_limit"] = int(user_input)
    await update.message.reply_text(f"📤 Please send {user_input} files one by one.")
    return COLLECTING_FILES


async def handle_multiple_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    active_conversations[update.message.chat_id] = True

    if not document:
        await update.message.reply_text("Please send a valid file.")
        return COLLECTING_FILES

    file = await context.bot.get_file(document.file_id)
    os.makedirs("downloads", exist_ok=True)

    # Step 1: Download original file
    original_file_path = f"downloads/{document.file_name}"
    await file.download_to_drive(original_file_path)

    # Step 2: Prepare filenames
    file_base, _ = os.path.splitext(document.file_name)
    edited_file_name = f"{file_base}{uuid.uuid4().hex[:1]}.pdf"
    edited_file_path = f"downloads/{edited_file_name}"

    # Step 3: Edit the PDF using SIGN_TEXT_1 from .env
    selected_text = os.getenv("SIGN_TEXT_1", "Default Signature Text")
    edit_pdf(original_file_path, edited_file_path, edited_file_name, selected_text)

    # Step 4: Sign the edited PDF
    signed_file_path = sign_pdf(edited_file_path)
    signed_file_name = os.path.basename(signed_file_path)

    # Step 5: Store the final signed PDF path and name
    context.user_data["files"].append((signed_file_path, signed_file_name))

    # Step 6: Clean up intermediate files
    for path in [original_file_path, edited_file_path]:
        if os.path.exists(path):
            os.remove(path)

    # Step 7: Check if all files are received
    if len(context.user_data["files"]) >= context.user_data["upload_limit"]:
        await update.message.reply_text("✅ All files received. Now enter payment amount:")
        return WAITING_FOR_PAYMENT

    await update.message.reply_text(
        f"📒 File *{document.file_name}* received and signed. Send next file...",
        parse_mode="Markdown"
    )
    return COLLECTING_FILES



async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Handle file upload from users """
    document = update.message.document
    active_conversations[update.message.chat_id] = True
    if not document:
        await update.message.reply_text("No document detected. Please try again.")
        return WAITING_FOR_UPLOAD_OPTION
    sent_message = await update.message.reply_text(
        "♻️ Uploading Report ....",
        reply_markup=ReplyKeyboardRemove()  # Remove the keyboard
    )
    logger.info(f"Received file: {document.file_name}")  # Debugging log

    file = await context.bot.get_file(document.file_id)
    file_path = f"downloads/{document.file_name}"

    # Create folder if not exists
    os.makedirs("downloads", exist_ok=True)

    # Download file
    await file.download_to_drive(file_path)
    logger.info(f"File saved locally: {file_path}")  # Debugging log

    # Store file path for later use
    context.user_data["file_path"] = file_path
    context.user_data["file_name"] = document.file_name
    context.job_queue.run_once(delete_message, 0, data=(sent_message.chat.id, sent_message.message_id))
    await update.message.reply_text("💵 Now, enter the payment amount:")

    return WAITING_FOR_PAYMENT

async def receive_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global code
    """ Receive payment amount and prompt for user ID """
    amount = update.message.text
    active_conversations[update.message.chat_id] = True
    context.user_data["amount"] = amount
    await update.message.reply_text("✍️ Please enter the name of the user:")
    return WAITING_FOR_NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive name after amount"""
    name = update.message.text.strip()
    active_conversations[update.message.chat_id] = True
    context.user_data["name"] = name
    await update.message.reply_text("👤 Enter the User ID:")
    return WAITING_FOR_USER

async def receive_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global code
    """ Ensure the entered user ID is an integer """
    user_id_text = update.message.text.strip()
    active_conversations[update.message.chat_id] = True
    if not user_id_text.isdigit() or not (100000000 <= int(user_id_text) <= 9999999999):
        await update.message.reply_text("❌ Please enter a valid Telegram User ID (7–10 digits).")
        return WAITING_FOR_USER

    """ Receive user ID and upload file to Google Drive """
    user_id = update.message.text
    file_path = context.user_data.get("file_path")
    file_name = context.user_data.get("file_name")
    amount = context.user_data.get("amount")

    if "files" not in context.user_data or not context.user_data["files"]:
        active_conversations[update.message.chat_id] = False
        await update.message.reply_text("🚫 No files found. Please restart with /upload.")
        return ConversationHandler.END

    await update.message.reply_text(
        "♻️ Uploading file to Google Drive...",
        reply_markup=ReplyKeyboardRemove()  # Remove the keyboard
    )

    # Upload to Google Drive
    links = []
    for path, name in context.user_data["files"]:
        gdrive_link = await upload_to_drive(path, name)
        if gdrive_link:
            links.append(gdrive_link)
    # ✅ Delete signed PDF files after upload
    for path, _ in context.user_data["files"]:
        if os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"🧹 Deleted signed file: {path}")
            except Exception as e:
                logger.warning(f"⚠️ Could not delete file {path}: {e}")

    if not links:
        await update.message.reply_text("❌ Error uploading files to Google Drive.")
        active_conversations[update.message.chat_id] = False
        return ConversationHandler.END

    # (Optional) Send multiple links or store them together
    short_links = [short_link(link, f"{user_id}-{i + 1}") for i, link in enumerate(links)]

    name = context.user_data.get("name", "Unknown")
    save_report_links(user_id, amount, short_links)
    save_user_data(user_id, name, "no_username")  # save name, username optional
    # Refresh subscriptions from Firestore
    global report_links
    report_links = load_report_links()  # Refresh from Firebase

    links_formatted = "\n".join([f"📥 File {i + 1}: {link}" for i, link in enumerate(report_links[user_id]["links"])])
    # Send confirmation
    await update.message.reply_text(f"<b>🔰REPORT UPLOADED SUCCESSFULLY!🔰</b>\n\n"
                                    # f"✅ <b>Name:</b> <a href='tg://user?id={user_id}'>{name}</a>'s report successfully uploaded.\n\n"
                                    f"👤 <b>Name:</b> <a href='tg://user?id={user_id}'>{name}</a>\n"
                                    f"💰 <b>Amount:</b> Rs {amount}/-\n\n"
                                    # f"⬇️ Download Link: {gdrive_link}\n\n"
                                   # f"⬇️ Download Link: {short_links}\n\n",
                                    f"<b>⬇️ Report Download Links:</b>\n{links_formatted}",
                                    parse_mode="HTML")
    try:

        start_button_text = "🤖 Start the Bot!"
        start_button = InlineKeyboardButton(start_button_text, callback_data=f"start_{user_id}")
        keyboard = [[start_button]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=user_id,
            text=f"<b>🔰REPORT IS READY🔰</b>\n\n"
                 f"Start bot and  make the payment of <b>Rs {amount}/-</b> to download your report.",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"*🔰USER CHAT NOT FOUND!🔰*\n\n"
                                        f"Message is not send to user. \n"
                                        f"Tell user to send a message to this bot, otherwise send the message to user manually.",
                                        parse_mode="Markdown")
        logger.error(f"Can't send message to user: {e}")
        return None
    else:
        return None

    # Delete the local file
    os.remove(file_path)
    return ConversationHandler.END

async def upload_to_drive(file_path, file_name):
    """ Upload a file to Google Drive and return the shareable link """
    try:
        file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
        media = MediaFileUpload(file_path, resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

        # Make file public
        drive_service.permissions().create(fileId=file.get('id'), body={'role': 'reader', 'type': 'anyone'}).execute()

        return f"https://drive.google.com/uc?id={file.get('id')}&export=download"
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return None



# ------------------ Start Command ------------------ #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if the update is from a callback query (button press)
    if update.callback_query:
        query = update.callback_query
        await query.answer()  # Acknowledge the button press
        chat_id = query.from_user.id
        user_id = str(chat_id)
        message = query.message  # Use the message from the callback query
    else:
        chat_id = update.message.from_user.id
        user_id = str(chat_id)
        message = update.message  # Use the message from the regular update

    # NEW CODE TO ADD:
    full_name = f"{update.effective_user.first_name} {(update.effective_user.last_name or '')}".strip()
    username = f"@{update.effective_user.username}" or "No username"
    #find list of all existing users
    existing_users = load_user_data()
    # Check if user exists
    if user_id not in existing_users:
        user_type = "new"
        save_user_data(user_id, full_name, username)
        logger.info(f"✅ Added new user to Firestore: {full_name} ({username})")
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔰<b>NEW USER DETECTED!</b>🔰\n\n"
                 f" Name: <code>{full_name}</code>\n"
                 f"🔗 Username: {username}\n"
                 f"🆔 User ID: <code>{user_id}</code>",
            parse_mode="HTML"
        )
        await message.reply_text(
            f"🔰*Welcome {full_name}!*🔰\n\n"
            f"Thank you for joining us.\n\n"
            f"📝 *How this works?*\n"
            f"1️⃣ Once your report is ready, I’ll notify you here.\n"
            f"2️⃣ You’ll receive secure download links after completing payment.\n\n"
            f"🆘 If you have any questions, feel free to contact Admin *@coding_services*.\n\n",
            parse_mode="Markdown"
        )
    else:
        user_type = "old"
        logger.info(f"✅ Existing user detected: {full_name} ({username})")

    #check is there any user's report
    if user_id in report_links:
        payment_button_text = f"🚀Make Payment of Rs {report_links[user_id]['amount']}/-🚀"
        download_button_text = "📥 Download Report"

        payment_button = InlineKeyboardButton(payment_button_text, url=PAYMENT_URL)
        download_button = InlineKeyboardButton(download_button_text, callback_data=f"download_{user_id}")
        keyboard = [[payment_button], [download_button]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await message.reply_text(
            f"*🔰Report Downloader Bot🔰*"
            f"\n\nTo download your report, follow these two steps:"
            f"\n 1️⃣ First click on the button below and make the payment."
            f"\n 2️⃣ After payment download the report."
            f"\n\n Your User ID: `{user_id}` (tap to copy)\n\n"
            f"✅ Use this User ID on Razorpay Payment Gateway.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        if user_type == "old":
            await message.reply_text("🚫 There is no information about your report. Please contact Admin @coding_services.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Acknowledge the button press
    # Check if the callback data starts with "start_"
    if query.data.startswith("start_"):
        user_id = query.data.replace("start_", "")
        await start(update, context)  # Call the start function
        return

    user_id = query.data.replace("download_", "")
    sent_message = await query.edit_message_text(f"Payment verifying. Please wait...")
    report_links = load_report_links() # Refresh from Firebase
    if user_id in report_links:
        invoice_amount = int(report_links[user_id]['amount'])
        if verify_payment(user_id, invoice_amount):

            links_formatted = "\n".join(
                [f"📥 File {i + 1}: {link}" for i, link in enumerate(report_links[user_id]["links"])])
            await query.message.reply_text(
                f"<b>🔰PAYMENT VERIFIED🔰</b>\n\n"
                f"🙏Thank you for making the payment.\n\n"
                f"✅ Download your report by clicking on the link below.\n\n"
                f"<b>⬇️ Report Download Links:</b>\n{links_formatted}",
                parse_mode="HTML"
            )
            context.job_queue.run_once(delete_message, 0, data=(sent_message.chat.id, sent_message.message_id))
            users = load_user_data()
            name = users.get(user_id, {}).get("name", "Unknown")
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"<b>🔰REPORT DELIVERED🔰</b>\n\n"
                     f"✅ <a href='tg://user?id={user_id}'>{name}</a>-generated report's download link successfully.\n\n",
                parse_mode="HTML"
            )
            DELETED_CODES_URL = f"{PAYMENT_CAPTURED_DETAILS_URL}/amount/{invoice_amount}"
            requests.delete(url=DELETED_CODES_URL)

            remove_report_links(user_id)
            load_report_links()  # Refresh from Firebase
        else:
            start_button_text = "🚀Click here to Pay🚀"
            start_button = InlineKeyboardButton(start_button_text, callback_data=f"start_{user_id}")
            keyboard = [[start_button]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text("<b>🔰PAYMENT NOT RECEIVED🔰</b>\n\n"
                                           "We have not received your payment. Please first make the payment then click on Download Report button.\n\n"
                                           "✅ Need help? Please contact to Admin @coding_services.", parse_mode="HTML", reply_markup=reply_markup)
    else:
        await query.message.reply_text("⭕️ Your report is not ready. Please wait for some time!")


# ------------------ Admin Command: Show Users ------------------ #
async def show_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return

    report_links = load_report_links()  # Refresh from Firebase
    if not report_links:
        await update.message.reply_text("📭 No pending reports found.")
        return

    messages = []
    users = load_user_data()
    for chat_id, details in report_links.items():
        name = users.get(chat_id, {}).get("name", "Unknown")
        # user_link = f"🆔 User ID:<a href='tg://user?id={chat_id}'>{chat_id}</a>"
        amount = details.get("amount", "N/A")
        links = details.get("links", [])

        if links:
            link_lines = "\n".join([f"📥 File {i + 1}: {link}" for i, link in enumerate(links)])
        else:
            link_lines = "🔗 No links found."

        messages.append(
            f"<b>👤 Name:</b> <a href='tg://user?id={chat_id}'> {name}</a>\n"
            f"<b>🆔 User ID:</b> <code>{chat_id}</code>\n"
            f"<b>💰 Amount:</b> Rs {amount}/-\n"
            f"{link_lines}\n"
        )

    final_report = "\n\n".join(messages)
    await update.message.reply_text(
        f"📜 <b>Not Downloaded Reports:</b>\n\n{final_report}\n"
        f"✂️ <b>To delete a report, send the User ID now.</b>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return WAITING_FOR_DELETE_ID

async def delete_user_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.text.strip()
    report_links = load_report_links()  # Refresh from Firebase
    if user_id in report_links:
        remove_report_links(user_id)
        load_report_links()  # Refresh from Firebase
        await update.message.reply_text(f"🗑️ Report data for user ID {user_id} has been deleted.")
    else:
        await update.message.reply_text(f"⚠️ No data found for user ID {user_id}.")
    return ConversationHandler.END

async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return ConversationHandler.END

    # check from the global variable
    if active_conversations.get(user_id, False):
        await update.message.reply_text(
            "⚠️ Ongoing process cancelled due to forwarded message.",
            reply_markup=ReplyKeyboardRemove()
        )
        active_conversations[user_id] = False
        return_state = ConversationHandler.END
    else:
        return_state = None

    # process the forwarded message as before
    message = update.message
    if not message or not hasattr(message, 'forward_origin'):
        return return_state

    origin = message.forward_origin

    if origin and origin.type.name == "USER" and hasattr(origin, "sender_user"):
        user = origin.sender_user
        full_name = f"{user.first_name} {user.last_name or ''}".strip()
        username = f"@{user.username}" if user.username else "No username"
        chat_id = user.id

        await message.reply_text(
            f"👤 <b>Forwarded User Info</b>\n\n"
            f"🧾 Name: <code>{full_name}</code>\n"
            f"🔗 Username: {username}\n"
            f"🆔 User ID: <code>{chat_id}</code>",
            parse_mode="HTML"
        )
    elif origin and origin.type.name == "HIDDEN_USER" and hasattr(origin, "sender_user_name"):
        await message.reply_text(
            f"🚫 Forwarded from a user with privacy enabled.\n\n"
            f"🔗 User's Telegram Name: {origin.sender_user_name}",
            parse_mode="HTML"
        )
    else:
        await message.reply_text("⚠️ Unable to identify forwarded user.")

    return return_state if return_state is not None else ConversationHandler.END



# ------------------ Send user info to admin when he/she send "Hi" or "hi" ------------------ #
async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "No username"
    user_id = user.id
    save_user_data(user_id, full_name, username)

    await update.message.reply_text(f"🔰*THANK YOU!*🔰\n\n"
                                    f"I'll inform you as soon as the report is ready.",
                                    parse_mode="Markdown"
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"👤 <b>User Info</b>\n\n"
        f"🧾 Name: <code>{full_name}</code>\n"
        f"🔗 Username: {username}\n"
        f"🆔 User ID: <code>{user_id}</code>",
        parse_mode="HTML"
    )

# ------------------ Search User ID ------------------ #
async def request_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return

    await update.message.reply_text("🔍 Please enter the name or username of the user you want to search:")
    return WAITING_FOR_SEARCH_INPUT

async def handle_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    results = search_user_id(query)

    if not results:
        await update.message.reply_text(f"❌ No user found for: {query}")
        return ConversationHandler.END

    for user_id, data in results:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🔍 <b>Search Result</b>\n\n"
                f"🆔 User ID: <code>{user_id}</code>\n"
                f"🧾 Name: <code>{data.get('name')}</code>\n"
                f"🔗 Username: {data.get('username')}"
            ),
            parse_mode="HTML"
        )
    return ConversationHandler.END


# ------------------ Delete Message Function ------------------ #
async def delete_message(context: CallbackContext):
    chat_id, message_id = context.job.data
    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)


# ------------------ Help Command ------------------ #
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
Commands available:
/start - Check whether your report is ready or not
/upload - Upload report (Admin only)
/cancel - Cancel the current process (Admin only)
/search_user - Search user by name or username (Admin only)
/show_reports - Show list of all reports not downloaded by users (Admin only)
/help - Show this help message
"""
    )

def main():
    """ Main function to start the bot """
    application = Application.builder().token(TOKEN).build()


    # Upload file conversation handler
    conv_handler_upload = ConversationHandler(
        entry_points=[CommandHandler("upload", upload)],
        states={
            WAITING_FOR_UPLOAD_OPTION: [
                CallbackQueryHandler(upload_option_handler),
                MessageHandler(filters.Text([CANCEL_BUTTON]), handle_cancel),  # Handle Cancel button
            ],
            WAITING_FOR_MULTIPLE_FILES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_file_count),
                MessageHandler(filters.Text([CANCEL_BUTTON]), handle_cancel),  # Handle Cancel button
            ],
            COLLECTING_FILES: [
                MessageHandler(filters.Document.ALL, handle_multiple_files),
                MessageHandler(filters.Text([CANCEL_BUTTON]), handle_cancel),  # Handle Cancel button
            ],
            WAITING_FOR_PAYMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment),
                MessageHandler(filters.Text([CANCEL_BUTTON]), handle_cancel),  # Handle Cancel button
            ],
            WAITING_FOR_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name),
                MessageHandler(filters.Text([CANCEL_BUTTON]), handle_cancel),
            ],
            WAITING_FOR_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_user),
                MessageHandler(filters.Text([CANCEL_BUTTON]), handle_cancel),  # Handle Cancel button
            ],
        },
        fallbacks=[
            CommandHandler("start", start),  # Reset conversation if /start is issued
            CommandHandler("upload", upload),  # Reset conversation if /upload is issued again
            CommandHandler("help", help_command),  # Reset conversation if /help is issued
            # CommandHandler("admin_commands", admin_commands),  # Reset conversation if /admin_commands is issued
            MessageHandler(filters.COMMAND, cancel_upload),  # Reset conversation on any other command
        ],
    )

    # Delete links conversation handler
    conv_handler_delete = ConversationHandler(
        entry_points=[CommandHandler("show_reports", show_reports)],
        states={
            WAITING_FOR_DELETE_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_user_report)
            ]
        },
        fallbacks=[
            CommandHandler("start", start),  # Reset conversation if /start is issued
            CommandHandler("upload", upload),  # Reset conversation if /upload is issued again
            CommandHandler("help", help_command),  # Reset conversation if /help is issued
            # CommandHandler("admin_commands", admin_commands),  # Reset conversation if /admin_commands is issued
            MessageHandler(filters.COMMAND, cancel_upload),  # Reset conversation on any other command
        ],
    )

    # Search user conversation
    conv_handler_search = ConversationHandler(
        entry_points=[CommandHandler("search_user", request_user_search)],
        states={
            WAITING_FOR_SEARCH_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_search)
            ]
        },
        fallbacks=[
            CommandHandler("start", start),  # Reset conversation if /start is issued
            CommandHandler("upload", upload),  # Reset conversation if /upload is issued again
            CommandHandler("help", help_command),  # Reset conversation if /help is issued
            # CommandHandler("admin_commands", admin_commands),  # Reset conversation if /admin_commands is issued
            MessageHandler(filters.COMMAND, cancel_upload),  # Reset conversation on any other command
        ],
    )

    application.add_handler(conv_handler_search)


    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^(hi|Hi|hello|Hello)$'), user_info))
    # application.add_handler(CommandHandler("admin_commands", admin_commands))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_message))
    application.add_handler(conv_handler_upload)
    application.add_handler(conv_handler_delete)
    application.add_handler(CallbackQueryHandler(button_handler))
    # application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_message))


    application.run_polling()

if __name__ == "__main__":
    main()
