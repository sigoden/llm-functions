#!/bin/bash
# Multi-symbol scalper daemon loop
echo "===================================================="
echo "⚡ Starting pyrm multi-symbol high-leverage scalper..."
echo "===================================================="

while true; do
  python scripts/run-tool.py pyrm_scalp_tool '{"dry_run": false}'
  echo "Sleeping 10s before next cycle..."
  sleep 10
done
