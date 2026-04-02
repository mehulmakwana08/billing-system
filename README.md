# Arvind Plastic Industries — GST Billing System

A complete offline GST billing desktop application built with **Python Flask + Electron + SQLite**.

---

## Features

- **Tax Invoice generation** with proper CGST/SGST/IGST calculation
- **PDF export** matching original invoice format with company letterhead
- **Customer & Product master** management
- **Dashboard** with 6-month revenue chart
- **Reports** — Monthly Sales, GSTR-1, HSN Summary
- **CSV export** for GSTR-1 filing
- **Settings** — company details, bank details, invoice numbering, terms

---

## Tech Stack

| Layer    | Technology          |
|----------|---------------------|
| Backend  | Python 3 + Flask    |
| Frontend | Electron (HTML/JS)  |
| Database | SQLite              |
| PDF      | ReportLab           |

---

## Prerequisites

| Tool      | Version | Download |
|-----------|---------|----------|
| Python    | 3.8+    | https://python.org |
| Node.js   | 18+     | https://nodejs.org |
| npm       | 9+      | Included with Node.js |

---

## Quick Start

### Windows
```
Double-click start.bat
```

### Windows (Desktop + Web With Shared Database)
```
Double-click start-shared.bat
```

### macOS / Linux
```bash
chmod +x start.sh
./start.sh
```

### macOS / Linux (Desktop + Web With Shared Database)
```bash
chmod +x start-shared.sh
./start-shared.sh
```

### Manual Start
```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install npm packages
cd frontend
npm install

# 3. Start the app
npm start
```

---

## Testing

### Backend Regression Tests
```bash
python -m pytest backend/tests -q
```

### API Smoke Tests
Start backend first:
```bash
python backend/app.py
```

Then run smoke scripts in another terminal:
```bash
python backend/test_api.py
python backend/test_api_urllib.py
python backend/test_delete.py
```

### Frontend E2E Tests (Playwright)
```bash
cd frontend
npm install
npx playwright install chromium
npm run test:e2e
```

### Shared Database Notes
- In shared mode, one backend process serves both the browser app and desktop app.
- Desktop runs with BILLING_USE_EXTERNAL_BACKEND=1, so it will not spawn another backend process.
- Both apps read and write through the same backend API and therefore use the same SQLite database file.

---

## Deployment (Common Backend + Common Database)

Use this model when you want both the Windows EXE and web app to use one backend and one shared database.

### 1) Deploy Backend (Production WSGI)

Do not use Flask dev server in production. Run the backend with Gunicorn:

```bash
pip install -r requirements.txt
cd backend
gunicorn --workers 2 --threads 4 --bind 0.0.0.0:${PORT:-5000} wsgi:application
```

Set environment variables on your host:

```bash
APP_MODE=cloud
AUTH_REQUIRED=1
CLOUD_ONLY_MODE=1
LOGIN_ONLY_MODE=1
ALLOW_SELF_REGISTER=0
JWT_SECRET=replace-with-long-random-secret
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD_HASH=<bcrypt-hash>
HOST=0.0.0.0
PORT=5000
BILLING_DB_PATH=/var/lib/arvind-billing/billing.db
BILLING_BILLS_DIR=/var/lib/arvind-billing/bills
```

Notes:
- `BILLING_DB_PATH` and `BILLING_BILLS_DIR` must point to persistent storage.
- Use your domain, for example: `https://billing-api.yourdomain.com`.

### 2) Deploy Web App Against Same Backend

For static web deployment (for example Vercel/Netlify), set backend base in `frontend/renderer/runtime-config.js`:

```javascript
window.BILLING_RUNTIME_CONFIG = {
	webApiBase: 'https://billing-api.yourdomain.com'
}
```

Then deploy `frontend/renderer` as static site.

### 3) Build and Configure Windows EXE

Build installer:

```bash
cd frontend
npm install
npm run build-win
```

Set desktop app to use external shared backend:

```bat
setx BILLING_USE_EXTERNAL_BACKEND 1
setx BILLING_CLOUD_ONLY_MODE 1
setx BILLING_BACKEND_ORIGIN https://billing-api.yourdomain.com
```

Restart the desktop app after setting env vars.

### Cloud-Only Login-First Behavior

- Web and desktop now use one backend API base and do not auto-fallback to local/offline APIs.
- App opens in login-required state and blocks page access until valid username/password login.
- Self-registration can be disabled (`ALLOW_SELF_REGISTER=0`) for admin-managed users only.
- For first-time setup, provide either `DEFAULT_ADMIN_PASSWORD_HASH` or `DEFAULT_ADMIN_PASSWORD`.

### 4) Validation Checklist

- Web health check: `https://billing-api.yourdomain.com/api/health` returns `{"status":"ok"}`.
- Create one invoice in web app.
- Open Windows EXE and confirm same invoice appears.

---

## First Run

On first launch the app automatically:
1. Creates `backend/billing.db` (SQLite database)
2. Seeds **Arvind Plastic Industries** as the company
3. Adds **Chamunda Bangles** as a sample customer
4. Adds **Bangles Acrylic Paip** (HSN 3906, ₹80, 18% GST) as a sample product
5. Sets next invoice number to **GT/31**

---

## Project Structure

```
billing-system/
├── backend/
│   ├── app.py              ← Flask API (all routes)
│   ├── wsgi.py             ← Production WSGI entrypoint
│   ├── Procfile            ← Hosting process command
│   ├── .env.example        ← Deployment environment template
│   ├── pdf_generator.py    ← ReportLab PDF generation
│   ├── num_words.py        ← Indian number-to-words
│   └── billing.db          ← SQLite database (auto-created)
│   └── bills/              ← Generated PDF files
├── frontend/
│   ├── main.js             ← Electron main process
│   ├── preload.js          ← Electron context bridge
│   ├── package.json
│   └── renderer/
│       ├── index.html      ← Single Page Application
│       ├── runtime-config.js ← Runtime web API target config
│       ├── css/style.css   ← UI styles
│       └── js/
│           ├── api.js      ← HTTP client
│           ├── app.js      ← Router & utilities
│           ├── dashboard.js
│           ├── invoices.js
│           ├── customers.js
│           ├── products.js
│           ├── reports.js
│           └── settings.js
├── requirements.txt
├── start.bat               ← Windows launcher
├── start.sh                ← Linux/macOS launcher
└── README.md
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET/POST | `/api/company` | Company settings |
| GET/POST | `/api/customers` | Customer list / create |
| GET/PUT/DELETE | `/api/customers/:id` | Customer CRUD |
| GET/POST | `/api/products` | Product list / create |
| GET/PUT/DELETE | `/api/products/:id` | Product CRUD |
| GET | `/api/invoices/next-number` | Next invoice number |
| GET/POST | `/api/invoices` | Invoice list / create |
| GET/DELETE | `/api/invoices/:id` | Invoice get / delete |
| GET | `/api/invoices/:id/pdf` | Stream PDF |
| GET | `/api/invoices/:id/pdf-path` | Generate PDF, return path |
| POST | `/api/payments` | Record payment |
| GET | `/api/dashboard` | Dashboard stats + chart |
| GET | `/api/reports/monthly` | Monthly sales report |
| GET | `/api/reports/gstr1` | GSTR-1 data |
| GET | `/api/reports/hsn-summary` | HSN summary |

---

## Default Company Data (Edit in Settings)

```
Name:   ARVIND PLASTIC INDUSTRIES
GSTIN:  24AAIFC6554D1ZN
State:  24 - Gujarat
Prefix: GT/
Next:   31
```

---

## GST Logic

- **Intra-state** (seller & buyer both Gujarat → CGST 9% + SGST 9%)
- **Inter-state** (different states → IGST 18%)
- State detected automatically from GSTIN (first 2 digits)

---

## Building Executable (Optional)

```bash
cd frontend
npm run build-win     # Windows .exe installer
npm run build-mac     # macOS .dmg
npm run build-linux   # Linux AppImage
```

Output in `frontend/dist/`

---

## License
MIT — Arvind Plastic Industries, 2026
