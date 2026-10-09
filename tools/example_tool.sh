#!/usr/bin/env bash
# ==============================================================================
# example_tool.sh — Example Tool
#
# @describe Example tool that prints a message.
# @option --msg! <VALUE>          Required message string
# @flag --verbose
# ==============================================================================

set -e

# Parse arguments
: "${argc_msg:?--msg is required}"

# Output the message
echo "Message: $argc_msg"
