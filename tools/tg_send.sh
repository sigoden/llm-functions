#!/data/data/com.termux/files/usr/bin/bash
# @describe Send files via Telegram.
# @arg file_path! Path to the file to send.
# @arg caption Optional caption.

# Check if at least one argument is provided
if [ $# -eq 0 ]; then
    echo "Usage: tg_send <file_path> [caption]"
    exit 1
fi

FILE_PATH="$1"
CAPTION="${2:-File sent from Termux}"

# Check if file exists
if [ ! -f "$FILE_PATH" ]; then
    echo "Error: File $FILE_PATH not found."
    exit 1
fi

# Use the existing notify.sh logic or direct curl for files
# Since notify.sh is for text, we use a direct curl call to the Telegram API
TOKEN="$TELEGRAM_BOT_TOKEN"
CHAT_ID="$TELEGRAM_CHAT_ID"

if [ -z "$TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set."
    exit 1
fi

STATUS_CODE=$(curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendDocument" \
     -F "chat_id=$CHAT_ID" \
     -F "document=@$FILE_PATH" \
     -F "caption=$CAPTION" \
     -o /dev/null -w "%{http_code}")

if [ "$STATUS_CODE" -eq 200 ]; then
    echo "✅ File sent successfully!"
else
    echo "❌ Failed to send file."
fi
