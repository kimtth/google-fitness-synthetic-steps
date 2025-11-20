# 🏃‍♂️ Google Fit Synthetic Step Uploader

Generate random daily step counts and push them to your Google Fit account for a single date or a range. Lean, fast, configurable. 

## ✨ Features
* 🎲 Random steps via global min/max or per‑day overrides (`--ranges`)
* 📅 Single day (`--date`) or range (`--start-date ... --end-date`)
* 🧩 Mixed overrides: `YYYY-MM-DD:min-max` list
* 💤 Dry‑run preview (`--dry-run`) — no writes
* 🛠️ Auto‑creates a dedicated raw data source if missing
* 🌍 Custom timezone (`--timezone`)

## 🧪 Usage
Week range:
```bash
python fit_steps.py --start-date 2025-11-01 --end-date 2025-11-07 --min 6000 --max 12000
```
Single day:
```bash
python fit_steps.py --date 2025-11-05 --min 8000 --max 10000
```
Overrides:
```bash
python fit_steps.py --start-date 2025-11-01 --end-date 2025-11-05 --min 6000 --max 9000 \
  --ranges 2025-11-02:3000-5000,2025-11-05:10000-15000
```
Dry run:
```bash
python fit_steps.py --date 2025-11-05 --min 8000 --max 10000 --dry-run
```
Verbose:
```bash
python fit_steps.py --start-date 2025-11-01 --end-date 2025-11-03 --min 5000 --max 7000 -v
```

## 🔧 Setup
1. Enable Google Fitness API in a Google Cloud project.
2. Create Desktop OAuth client; save JSON as `client_secrets.json` here.
3. Install:
```bash
uv sync
```
4. First run launches browser; saves `token.json`.

## 🗃 Data Source
ID: `raw:com.google.step_count.delta:GitHubCopilot:synthetic_steps` (created automatically). Field: `steps` (integer).

## 🔐 Scope
`https://www.googleapis.com/auth/fitness.activity.write`

## ⏰ Timezone
- Local system default; override with `--timezone UTC` (etc.).
- Common IANA names: UTC, America/Los_Angeles, Europe/London, Asia/Seoul, Australia/Sydney, America/New_York.
- Avoid Windows legacy names like Pacific Standard Time; prefer IANA forms above.

## ⚠️ Ethics
For testing / personal shaping only. Don’t misrepresent medical/official health data.

## 🚫 Limitations
* Daily totals only (no intra‑day granularity)
* No de‑duplication — avoid overlapping uploads
