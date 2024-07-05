#!/usr/bin/env bash
set -e

# @describe Send a email.
# @env EMAIL_SMTP_ADDR! The SMTP Address, e.g. smtps://smtp.gmail.com:465
# @env EMAIL_SMTP_USER! The SMTP User, e.g. alice@gmail.com
# @env EMAIL_SMTP_PASS! The SMTP Password
# @env EMAIL_SENDER_NAME The sender name
# @option --recipient! The recipient of the email.
# @option --subject! The subject of the email.
# @option --body! The body of the email.

main() {
    sender_name="${EMAIL_SENDER_NAME:-$(echo "$EMAIL_SMTP_USER" | awk -F'@' '{print $1}')}"
    printf "%s\n" "From: $sender_name <$EMAIL_SMTP_USER>
To: $argc_recipient 
Subject: $argc_subject

$argc_body" | \
    curl -fsS --ssl-reqd \
        --url "$EMAIL_SMTP_ADDR" \
        --user "$EMAIL_SMTP_USER:$EMAIL_SMTP_PASS" \
        --mail-from "$EMAIL_SMTP_USER" \
        --mail-rcpt "$argc_recipient" \
        --upload-file -
    echo "Email sent successfully"
}

eval "$(argc --argc-eval "$0" "$@")"
