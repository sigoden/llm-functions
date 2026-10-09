#!/data/data/com.termux/files/usr/bin/env bash
# @describe Send a notification via Telegram.
# @arg title The message title/category.
# @arg message The message content.
export TELEGRAM_BOT_TOKEN="8811508626:AAG4Ii7bN6X_qUqdAzq4GsdpkDmbmsYAw-0"
export TELEGRAM_CHAT_ID="1864234012"
"$HOME/.config/aichat/llm-functions/tools/send_telegram.sh" --token "$TELEGRAM_BOT_TOKEN" --chat-id "$TELEGRAM_CHAT_ID" --message "$2"
