@echo off
title Arvind Billing System

echo ============================================
echo   Arvind Plastic Industries - Billing System
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.8+ from https://python.org
    pause
    exit /b 1
)

REM Install Python dependencies if needed
echo Checking Python dependencies...
pip install -r requirements.txt --quiet

REM Check Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed or not in PATH.
    echo Please install Node.js from https://nodejs.org
    pause
    exit /b 1
)

REM Install npm packages if needed
if not exist "frontend\node_modules" (
    echo Installing npm packages...
    cd frontend
    npm install
    cd ..
)

echo Starting application...
cd frontend
npm start
