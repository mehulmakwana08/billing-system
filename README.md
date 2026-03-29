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

### macOS / Linux
```bash
chmod +x start.sh
./start.sh
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
