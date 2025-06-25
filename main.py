import os
import json
import logging
import requests
from firebase_db import save_report_links, load_report_links, remove_report_links, save_user_data, search_user_id
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
# print(os.getenv("GOOGLE_PRIVATE_KEY"))

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



# Load existing file data or initialize an empty dictionary
DATA_FILE = "file_data.json"
report_links = {}
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
    await update.message.reply_text("⬆️ Upload process cancelled. You can start over with /upload.")
    return ConversationHandler.END

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return ConversationHandler.END
    await update.message.reply_text("♻️ Upload Process has Started...", reply_markup=get_cancel_keyboard(),parse_mode="Markdown")

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
    if not user_input.isdigit() or int(user_input) <= 2:
        await update.message.reply_text("❌ Please enter a number greater than 2.")
        return WAITING_FOR_MULTIPLE_FILES

    context.user_data["upload_limit"] = int(user_input)
    await update.message.reply_text(f"📤 Please send {user_input} files one by one.")
    return COLLECTING_FILES


async def handle_multiple_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        await update.message.reply_text("Please send a valid file.")
        return COLLECTING_FILES

    file = await context.bot.get_file(document.file_id)
    os.makedirs("downloads", exist_ok=True)
    file_path = f"downloads/{document.file_name}"
    await file.download_to_drive(file_path)

    context.user_data["files"].append((file_path, document.file_name))

    if len(context.user_data["files"]) >= context.user_data["upload_limit"]:
        await update.message.reply_text("✅ All files received. Now enter payment amount:")
        return WAITING_FOR_PAYMENT

    await update.message.reply_text(f"📒 File *{document.file_name}* received. Send next file...,", parse_mode="Markdown")
    return COLLECTING_FILES



async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Handle file upload from users """
    document = update.message.document
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
    context.user_data["amount"] = amount
    await update.message.reply_text("👤 Enter the User ID:")
    return WAITING_FOR_USER

async def receive_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global code
    """ Ensure the entered user ID is an integer """
    user_id_text = update.message.text.strip()

    if not user_id_text.isdigit() or not (100000000 <= int(user_id_text) <= 9999999999):
        await update.message.reply_text("❌ Please enter a valid Telegram User ID (7–10 digits).")
        return WAITING_FOR_USER

    """ Receive user ID and upload file to Google Drive """
    user_id = update.message.text
    file_path = context.user_data.get("file_path")
    file_name = context.user_data.get("file_name")
    amount = context.user_data.get("amount")

    if "files" not in context.user_data or not context.user_data["files"]:
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

    if not links:
        await update.message.reply_text("❌ Error uploading files to Google Drive.")
        return ConversationHandler.END

    # (Optional) Send multiple links or store them together
    short_links = [short_link(link, f"{user_id}-{i + 1}") for i, link in enumerate(links)]

    save_report_links(user_id, amount, short_links)  # Saving data online
    # Refresh subscriptions from Firestore
    global report_links
    report_links = load_report_links()  # Refresh from Firebase

    links_formatted = "\n".join([f"📥 File {i + 1}: {link}" for i, link in enumerate(report_links[user_id]["links"])])
    # Send confirmation
    await update.message.reply_text(f"<b>🔰FILE UPLOADED SUCCESSFULLY!🔰</b>\n\n"
                                    f"✅ <a href='tg://user?id={user_id}'>User</a>'s report successfully uploaded.\n\n"
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
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"<b>🔰REPORT DELIVERED🔰</b>\n\n"
                     f"✅ <a href='tg://user?id={user_id}'>User</a>-generated report's download link successfully.\n\n",
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
    for chat_id, details in report_links.items():
        user_link = f"User ID:<a href='tg://user?id={chat_id}'>{chat_id}</a>"
        amount = details.get("amount", "N/A")
        links = details.get("links", [])

        if links:
            link_lines = "\n".join([f"📥 File {i + 1}: {link}" for i, link in enumerate(links)])
        else:
            link_lines = "🔗 No links found."

        messages.append(f"{user_link}\n💰 Amount: Rs {amount}\n{link_lines}\n")

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
    print(f"1. user_id;{type(user_id)}")
    report_links = load_report_links()  # Refresh from Firebase
    if user_id in report_links:
        remove_report_links(user_id)
        load_report_links()  # Refresh from Firebase
        await update.message.reply_text(f"🗑️ Report data for user ID {user_id} has been deleted.")
    else:
        await update.message.reply_text(f"⚠️ No data found for user ID {user_id}.")
    return ConversationHandler.END


async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return

    if not message or not hasattr(message, 'forward_origin'):
        return

    origin = message.forward_origin
    print(origin)

    # Case 1: Forwarded from a visible user
    if origin.type.name == "USER" and hasattr(origin, "sender_user"):
        user = origin.sender_user
        print(user)
        full_name = f"{user.first_name} {user.last_name or ''}".strip()
        username = f"@{user.username}" if user.username else "No username"
        chat_id = user.id

        await message.reply_text(
            f"👤 <b>Forwarded User Info</b>\n\n"
            f"🧾 Name: {full_name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 User ID: <code>{chat_id}</code>",
            parse_mode="HTML"
        )

    # Case 2: Forwarded from a hidden user
    elif origin.type.name == "HIDDEN_USER" and hasattr(origin, "sender_user_name"):
        print(origin.type)
        await message.reply_text(
            f"🚫 Forwarded from a user with privacy enabled.\n\n"
            f"🔗 User's Telegram Name: {origin.sender_user_name}",
            parse_mode="HTML"
        )
    else:
        await message.reply_text("⚠️ Unable to identify forwarded user.")

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
        f"🧾 Name: {full_name}\n"
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
                f"🧾 Name: {data.get('name')}\n"
                f"🔗 Username: {data.get('username')}"
            ),
            parse_mode="HTML"
        )

    await update.message.reply_text("✅ Search result sent to admin.")
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
/show_reports - Show list of all reports not downloaded by users (Admin only)
/search_user - Search user by name or username (Admin only)
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
        fallbacks=[CommandHandler("cancel", cancel_upload)],
    )

    # Search user conversation
    conv_handler_search = ConversationHandler(
        entry_points=[CommandHandler("search_user", request_user_search)],
        states={
            WAITING_FOR_SEARCH_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_search)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_upload)],
    )

    application.add_handler(conv_handler_search)


    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^(hi|Hi|hello|Hello)$'), user_info))
    # application.add_handler(CommandHandler("admin_commands", admin_commands))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler_upload)
    application.add_handler(conv_handler_delete)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_message))


    application.run_polling()

if __name__ == "__main__":
    main()
