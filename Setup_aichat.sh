#!/bin/bash

# Exit on error
set -e

# Define base directories
FUNCTIONS_DIR="/data/data/com.termux/files/home/llm-functions"
CONFIG_DIR="$HOME/.config/aichat"
CONFIG_FILE="$CONFIG_DIR/config.yaml"

# Create directories
echo "Creating directories..."
mkdir -p "$FUNCTIONS_DIR" "$CONFIG_DIR"

# Change to functions directory
cd "$FUNCTIONS_DIR" || { echo "Failed to change to $FUNCTIONS_DIR"; exit 1; }

# Create function scripts
echo "Creating function scripts..."

## get_date.sh
touch get_date.sh
chmod +x get_date.sh
cat << 'EOF' > get_date.sh
#!/bin/bash
date "$@" || echo "Error: Invalid date format"
EOF

## get_weather.sh (placeholder)
touch get_weather.sh
chmod +x get_weather.sh
cat << 'EOF' > get_weather.sh
#!/bin/bash
echo "Weather plugin not implemented. Args: $@"
exit 1
EOF

## get_system_info.sh
touch get_system_info.sh
chmod +x get_system_info.sh
cat << 'EOF' > get_system_info.sh
#!/bin/bash
uname -a || echo "Error: System info unavailable"
EOF

# Create tool scripts
echo "Creating tool scripts..."

## fs_cat.sh
touch fs_cat.sh
chmod +x fs_cat.sh
cat << 'EOF' > fs_cat.sh
#!/bin/bash
if [ -z "$1" ]; then
    echo "Error: No file specified"
    exit 1
fi
cat "$1" || echo "Error: Cannot read file $1"
EOF

## fs_ls.sh
touch fs_ls.sh
chmod +x fs_ls.sh
cat << 'EOF' > fs_ls.sh
#!/bin/bash
ls -la "$@" || echo "Error: Cannot list directory"
EOF

## fs_mkdir.sh
touch fs_mkdir.sh
chmod +x fs_mkdir.sh
cat << 'EOF' > fs_mkdir.sh
#!/bin/bash
if [ -z "$1" ]; then
    echo "Error: No directory specified"
    exit 1
fi
mkdir -p "$1" || echo "Error: Cannot create directory $1"
EOF

## fs_rm.sh
touch fs_rm.sh
chmod +x fs_rm.sh
cat << 'EOF' > fs_rm.sh
#!/bin/bash
if [ -z "$1" ]; then
    echo "Error: No file/directory specified"
    exit 1
fi
rm -rf "$1" || echo "Error: Cannot remove $1"
EOF

## fs_write.sh
touch fs_write.sh
chmod +x fs_write.sh
cat << 'EOF' > fs_write.sh
#!/bin/bash
if [ $# -lt 2 ]; then
    echo "Error: Usage: fs_write <file> <content>"
    exit 1
fi
file="$1"
shift
echo "$@" > "$file" || echo "Error: Cannot write to $file"
EOF

## web_search (Python script)
echo "Creating web_search tool..."
touch web_search
chmod +x web_search
cat << 'EOF' > web_search
#!/usr/bin/env python

import sys
import requests
import json

def main():
    if len(sys.argv) < 2:
        print("Error: No search query provided")
        sys.exit(1)

    query = ' '.join(sys.argv[1:])
    url = f"https://api.duckduckgo.com/?q={query}&format=json"

    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error: Failed to perform search - {e}")
        sys.exit(1)

    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        print("Error: Invalid response from search API")
        sys.exit(1)

    results = data.get('Results', [])

    if not results:
        print("No results found.")
        sys.exit(0)

    print(f"Search results for: {query}\n")

    for index, result in enumerate(results, start=1):
        title = result.get('Title', 'No title')
        description = result.get('Text', 'No description')
        url = result.get('FirstURL', 'No URL')
        print(f"{index}. {title} - {description} - {url}\n")

if __name__ == "__main__":
    main()
EOF

# Install dependencies for web_search
echo "Installing requests library for Python..."
pip install requests

# Create additional tools
echo "Creating additional tools..."

## get_time.sh
touch get_time.sh
chmod +x get_time.sh
cat << 'EOF' > get_time.sh
#!/bin/bash
date +%H:%M:%S
EOF

## list_files_with_sizes.sh
touch list_files_with_sizes.sh
chmod +x list_files_with_sizes.sh
cat << 'EOF' > list_files_with_sizes.sh
#!/bin/bash
if [ -z "$1" ]; then
    dir="."
else
    dir="$1"
fi
ls -lh "$dir" || echo "Error: Cannot list directory $dir"
EOF

## cd_and_mkdir.sh
touch cd_and_mkdir.sh
chmod +x cd_and_mkdir.sh
cat << 'EOF' > cd_and_mkdir.sh
#!/bin/bash
if [ -z "$1" ]; then
    echo "Error: No directory specified"
    exit 1
fi
dir="$1"
mkdir -p "$dir" || echo "Error: Cannot create directory $dir"
cd "$dir" || echo "Error: Cannot change directory to $dir"
echo "Changed to directory $dir"
EOF

## download_file.sh
touch download_file.sh
chmod +x download_file.sh
cat << 'EOF' > download_file.sh
#!/bin/bash
if [ $# -lt 2 ]; then
    echo "Error: Usage: download_file <url> <filename>"
    exit 1
fi
url="$1"
filename="$2"
curl -o "$filename" "$url" || echo "Error: Cannot download from $url to $filename"
echo "File downloaded successfully to $filename"
EOF

## compress_file.sh
touch compress_file.sh
chmod +x compress_file.sh
cat << 'EOF' > compress_file.sh
#!/bin/bash
if [ -z "$1" ]; then
    echo "Error: No file or directory specified"
    exit 1
fi
path="$1"
tar -czf "${path}.tar.gz" "$path" || echo "Error: Cannot compress $path"
echo "Compressed $path to ${path}.tar.gz"
EOF

## decompress_file.sh
touch decompress_file.sh
chmod +x decompress_file.sh
cat << 'EOF' > decompress_file.sh
#!/bin/bash
if [ -z "$1" ]; then
    echo "Error: No file specified"
    exit 1
fi
file="$1"
if [[ "$file" =~ \.tar\.gz$ ]]; then
    tar -xzf "$file" || echo "Error: Cannot decompress $file"
else
    echo "Error: File must be a .tar.gz file"
    exit 1
fi
echo "Decompressed $file successfully"
EOF

# Create config.yaml with all settings
echo "Creating config.yaml..."
cat << 'EOF' > "$CONFIG_FILE"
# Global Settings
serve_addr: 127.0.0.1:8000
user_agent: auto
save_shell_history: true
syncModelsURL: https://raw.githubusercontent.com/sigoden/aichat/main/models.yaml

# Clients
clients:
  - type: gemini
    api_key: AIzaSyDGIJHSOiWKfgbe5dQG7Lc4w9EMtRxUhPQ
    extra:
      safety_settings:
        - category: HARM_CATEGORY_HARASSMENT
          threshold: BLOCK_NONE
        - category: HARM_CATEGORY_HATE_SPEECH
          threshold: BLOCK_NONE
        - category: HARM_CATEGORY_SEXUALLY_EXPLICIT
          threshold: BLOCK_NONE
        - category: HARM_CATEGORY_DANGEROUS_CONTENT
          threshold: BLOCK_NONE

# Models
models:
  - name: gemini:gemini-2.0-flash-thinking-exp-01-21
    client: gemini

# LLM Settings
model: gemini:gemini-2.0-flash-thinking-exp-01-21
temperature: 0.7
top_p: 0.9
max_output_tokens: 2048

# Behavior Settings
stream: true
save: true
keybinding: emacs
editor: nano
wrap: auto
wrap_code: true
highlight: true
save_session: true
compress_threshold: 2000
copy_to_clipboard: true

# Function Calling
function_calling: true
mapping_tools:
  fs: 'fs_cat,fs_ls,fs_mkdir,fs_rm,fs_write'
  web: 'web_search'
use_tools: fs, web

# Preliminary Settings
prelude: role:default
repl_prelude: session:default

# Session Settings
summarize_prompt: 'Summarize the session concisely.'

# RAG Settings
rag_embedding_model: gemini:embedding-001
rag_reranker_model: gemini:reranker-001
rag_top_k: 5
rag_chunk_size: 512
rag_chunk_overlap: 128
rag_batch_size: 10
rag_template: |
  __CONTEXT__
  __INPUT__

# Appearance Settings
left_prompt: '[{session}] {role} > '
right_prompt: '{model}'
themes:
  default:
    prompt_color: "\033[1;34m"
    response_color: "\033[1;32m"
light_themes: false

# Macros
macros:
  greet: "Hello, how can I assist you today?"
  time: "The current time is: $(date +%H:%M:%S)"
  date: "Today is: $(date +%Y-%m-%d)"

# Functions
functions:
  - name: get_date
    command: "date"
  - name: get_weather
    command: "/path/to/weather_plugin.sh"
  - name: get_system_info
    command: "uname -a"

# Agents
agents:
  - name: assistant
    instructions: "Act as a helpful assistant with a friendly tone."
  - name: coding-agent
    instructions: |
      You are a Senior Software Developer with expertise in coding, debugging, and explaining code.
      - Generate accurate code snippets based on user requests (e.g., "Write a Python function to sort a list").
      - Debug code by identifying errors and suggesting fixes (e.g., "Fix this: print(x.sort())").
      - Explain code clearly, breaking down logic step-by-step (e.g., "Explain how this loop works").
      - Use tools: 'fs' to read/write files (e.g., fs_cat, fs_write) and 'web' to search for solutions.
      - Format code with proper syntax and include comments for clarity.
      - If unsure, ask clarifying questions (e.g., "What language do you want this in?").

# Debug Settings
debug_mode: true
log_file: ~/.config/aichat/aichat.log

# AI-Powered Suggestions
suggestionsEnabled: true

# Multi-Modal Inputs
multiModalEnabled: true

# Plugin System
plugins:
  - name: weather
    script: /path/to/weather_plugin.sh

# Voice Input/Output
voiceInput: true
voiceOutput: true

# Offline Mode
offlineMode: true
cacheFile: /.config/aichat(cache.db)

# Real-Time Collaboration
collaborationEnabled: false
serverAddress: "0.0.0.0:8080"

# Functions Path
functions_path: /data/data/com.termux/files/home/llm-functions
EOF

# Final instructions
echo "Setup complete. To link to aichat's functions_dir, run:"
echo "ln -s \"$(pwd)/$FUNCTIONS_DIR\" \"\$(aichat --info | sed -n 's/^functions_dir\s\+//p')\""
echo "Note: Ensure 'curl' is installed for download_file.sh (e.g., via 'pkg install curl')"
echo "Note: Replace '/path/to/weather_plugin.sh' in config.yaml with the actual path if implemented."

# Verify creation
echo "Listing files in $FUNCTIONS_DIR:"
ls -l "$FUNCTIONS_DIR"
