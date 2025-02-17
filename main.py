import os
import json
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler, CallbackContext
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
# from keep_alive import keep_alive
# keep_alive()

from dotenv import load_dotenv
load_dotenv()

# Enable logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
TOKEN = os.getenv("TOKEN")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PAYMENT_URL = os.getenv('PAYMENT_URL')
PAYMENT_CAPTURED_DETAILS_URL= os.getenv("PAYMENT_CAPTURED_DETAILS_URL")

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
print(os.getenv("GOOGLE_PRIVATE_KEY"))

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
WAITING_FOR_FILE, WAITING_FOR_PAYMENT, WAITING_FOR_USER = range(3)

# Load existing file data or initialize an empty dictionary
DATA_FILE = "file_data.json"
# Global variable to store the code fetched from the API.
code = None

if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            file_content = f.read().strip()  # Remove any accidental empty spaces
            file_data = json.loads(file_content) if file_content else {}
    except json.JSONDecodeError:
        print("Warning: JSON file is corrupted. Resetting data.")
        file_data = {}
else:
    file_data = {}

def save_data():
    """Save the file data to JSON."""
    with open(DATA_FILE, "w") as f:
        json.dump(file_data, f, indent=4)

def verify_payment(chat_id,payment_amount):
    response = requests.get(url=PAYMENT_CAPTURED_DETAILS_URL)
    try:
        response.raise_for_status()
        data = response.json()
        for entry in data:
            if entry['user_Id'] == str(chat_id):
                if entry['amount'] == str(payment_amount):
                    return True
        print("No payment details found! ")
    except requests.exceptions.HTTPError as err:
        print("HTTP Error:", err)



# ------------------ Start Command ------------------ #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.from_user.id
    user_id = str(chat_id)
    # print(f"user id: {user_id}, data type: {type(user_id)}")

    if user_id in file_data:
        payment_button_text = f"🚀Make Payment of Rs {file_data[user_id]['amount']}/-🚀"
        download_button_text = "📥 Download Report"

        payment_button = InlineKeyboardButton(payment_button_text, url=PAYMENT_URL)
        download_button = InlineKeyboardButton(download_button_text, callback_data=f"download_{user_id}")

        keyboard = [[payment_button], [download_button]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
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
        await update.message.reply_text("There is no information about your report. Please contact Admin @coding_services.")



async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return ConversationHandler.END
    """ Start the upload process """
    await update.message.reply_text("Please upload your file.")
    return WAITING_FOR_FILE

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Handle file upload from users """
    document = update.message.document
    if not document:
        await update.message.reply_text("No document detected. Please try again.")
        return WAITING_FOR_FILE

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
    """ Receive user ID and upload file to Google Drive """
    user_id = update.message.text
    file_path = context.user_data.get("file_path")
    file_name = context.user_data.get("file_name")
    amount = context.user_data.get("amount")

    if not file_path:
        await update.message.reply_text("No file found. Please restart with /upload.")
        return ConversationHandler.END

    await update.message.reply_text("♻️ Uploading file to Google Drive...")

    # Upload to Google Drive
    gdrive_link = await upload_to_drive(file_path, file_name)
    if not gdrive_link:
        await update.message.reply_text("❌ Error uploading file to Google Drive.")
        return ConversationHandler.END

    if gdrive_link:
        file_data[user_id] = {
            "link": gdrive_link,
            "amount": amount
        }
        save_data()  # Save the updated data to JSON

        # Send confirmation
        await update.message.reply_text(f"<b>🔰FILE UPLOADED SUCCESSFULLY!🔰</b>\n\n"
                                        f"✅ <a href='tg://user?id={user_id}'>User</a>'s report successfully uploaded.\n\n"
                                        f"⬇️ Download Link: {gdrive_link}\n\n",
                                        parse_mode="HTML")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"<b>🔰REPORT IS READY🔰</b>\n\n"
                     f"Type /start and  make the payment of <b>Rs {amount}/-</b> to download your report.", parse_mode="HTML"
            )
        except Exception as e:
            await update.message.reply_text(f"*🔰USER CHAT NOT FOUND!🔰*\n\n"
                                            f"Message is not send to user. \n"
                                            f"Tell user to send a message to this bot, otherwise send the message to user manually.",
                                            parse_mode="Markdown")
            logger.error(f"Can't send message to user: {e}")
            return None

    else:
        await update.message.reply_text("❌ Error uploading file to Google Drive.")

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



async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    user_id = query.data.replace("download_", "")
    sent_message = await query.edit_message_text(f"Payment verifying. Please wait...")
    if user_id in file_data:
        invoice_amount = int(file_data[user_id]['amount'])
        if verify_payment(user_id, invoice_amount):
            await query.message.reply_text(
                f"<b>🔰PAYMENT VERIFIED🔰</b>\n\n"
                f"🙏Thank you for making the payment.\n\n"
                f"✅ Download your report by clicking on the link below.\n\n"
                f"<b>⬇️ Link:</b> {file_data[user_id]['link']}",
                parse_mode="HTML"
            )
            context.job_queue.run_once(delete_message, 0, data=(sent_message.chat.id, sent_message.message_id))
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"<b>🔰REPORT DELIVERED🔰</b>\n\n"
                     f"✅ <a href='tg://user?id={user_id}'>User</a>-generated report download link successfully.\n\n",
                parse_mode="HTML"
            )
            DELETED_CODES_URL = f"{PAYMENT_CAPTURED_DETAILS_URL}/amount/{invoice_amount}"
            requests.delete(url=DELETED_CODES_URL)

            del file_data[user_id]
            save_data()
        else:
            await query.message.reply_text("<b>🔰PAYMENT NOT RECEIVED🔰</b>\n\n"
                                           "We have not received your payment. Please first make the payment then click on Download Report button.\n\n"
                                           "✅ Need help? Please contact to Admin @coding_services.", parse_mode="HTML")
    else:
        await query.message.reply_text("Your report is not ready. Please wait for some time!")


# ------------------ Admin Command: Show Users ------------------ #
async def show_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not file_data:
        await update.message.reply_text("No active users found.")
        return
    user_list = "\n".join([
        f"👤 <a href='tg://user?id={chat_id}'>User ID: {chat_id}</a> \nRport Link: {details['link']}\n\n"
        for chat_id, details in file_data.items()
    ])
    await update.message.reply_text(
        f"📜 <b>Not Downloaded  Reports :</b>\n\n{user_list}",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

# ------------------ Delete Message Function ------------------ #
async def delete_message(context: CallbackContext):
    chat_id, message_id = context.job.data
    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)

# ------------------ New Admin Command: Admin Commands ------------------ #
async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    await update.message.reply_text(
        """
Commands available:
/upload - Upload report
/show_reports - Show list of all reports not downloaded by users
"""
    )

# ------------------ Help Command ------------------ #
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
Commands available:
/start - Start the bot to make payment
/download - Generate your report download
/admin_commands - Show all commands that only admin can use
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
            WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL, handle_document)],
            WAITING_FOR_PAYMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment)],
            WAITING_FOR_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_user)],
        },
        fallbacks=[],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("show_reports", show_reports))
    application.add_handler(CommandHandler("admin_commands", admin_commands))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler_upload)
    application.add_handler(CallbackQueryHandler(button_handler))

    application.run_polling()

if __name__ == "__main__":
    main()
