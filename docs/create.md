 aichat -f docs/tool.md <<-'EOF'
create tools/get_youtube_transcript.py

description: Extract transcripts from YouTube videos
parameters:
url (required): YouTube video URL or video ID
lang (default: "en"): Language code for transcript (e.g., "ko", "en")
EOF
