@echo off
setlocal

title Arvind Billing System - Shared DB Mode

set ROOT=%~dp0
cd /d "%ROOT%"

echo ============================================
echo   Arvind Billing System - Shared DB Mode
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    pause
    exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed or not in PATH.
    pause
    exit /b 1
)

echo Installing Python dependencies...
pip install -r requirements.txt --quiet

if not exist "frontend\node_modules" (
    echo Installing npm dependencies...
    cd frontend
    npm install
    cd ..
)

echo Starting shared backend on http://127.0.0.1:5000 ...
start "Billing Backend" cmd /k "cd /d \"%ROOT%backend\" && set APP_MODE=cloud && set AUTH_REQUIRED=1 && set CLOUD_ONLY_MODE=1 && set LOGIN_ONLY_MODE=1 && set ALLOW_SELF_REGISTER=0 && set DEFAULT_ADMIN_USERNAME=admin && set DEFAULT_ADMIN_PASSWORD=Admin@123 && set JWT_SECRET=dev-local-change-me && python app.py"

timeout /t 3 >nul

echo Opening web application...
start "" http://127.0.0.1:5000

echo Starting desktop in external-backend mode...
cd /d "%ROOT%frontend"
set BILLING_USE_EXTERNAL_BACKEND=1
set BILLING_CLOUD_ONLY_MODE=1
set BILLING_BACKEND_ORIGIN=http://127.0.0.1:5000
npm start
