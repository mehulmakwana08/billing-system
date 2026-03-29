#!/bin/bash

echo "============================================"
echo "  Arvind Plastic Industries - Billing System"
echo "============================================"
echo ""

# Check Python3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed."
    echo "Install with: sudo apt install python3  (Ubuntu/Debian)"
    echo "           or: brew install python3      (macOS)"
    exit 1
fi

# Install Python deps
echo "Checking Python dependencies..."
pip3 install -r requirements.txt --quiet 2>/dev/null || \
    pip3 install -r requirements.txt --break-system-packages --quiet

# Check Node.js
if ! command -v node &>/dev/null; then
    echo "ERROR: Node.js is not installed."
    echo "Install from https://nodejs.org"
    exit 1
fi

# Install npm packages if needed
if [ ! -d "frontend/node_modules" ]; then
    echo "Installing npm packages..."
    cd frontend && npm install && cd ..
fi

echo "Starting application..."
cd frontend && npm start
