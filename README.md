# RAG Chatbot

A Retrieval Augmented Generation chatbot with a FastAPI/Chroma backend and elegant web UI.

## First deploy quick checklist

Use this order:

1. Complete local setup (`Setup` section).
2. Configure production env values (`Local vs Production` section).
3. Follow `Production deployment flow (nginx)` end-to-end.
4. Use `Reverse proxy alternatives` if you choose Caddy instead of nginx.

## Setup

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create your environment file:

```bash
cp .env.example .env
```

Edit `.env` and set `OPENAI_API_KEY`.

If you want endpoint protection enabled, generate an app key and set `APP_API_KEY` in `.env`:

```bash
openssl rand -base64 32
```

Then copy that value into:

```dotenv
APP_API_KEY=YOUR_GENERATED_VALUE
```

Optional but recommended for any internet-facing deployment:

- `APP_API_KEY`: shared API key required for `/chat`, `/ingest`, and `/documents*`.
- `MAX_UPLOAD_MB`: maximum upload size for document uploads (default: `2`).
- `RATE_LIMIT_PER_MIN`: per-client per-route request limit per minute (default: `10`).
- `RETRIEVER_K`: number of chunks returned to the LLM (default: `12`).
- `RETRIEVER_SEARCH_TYPE`: retrieval mode (`mmr` or `similarity`, default: `mmr`).
- `RETRIEVER_FETCH_K`: candidate pool size used by MMR (default: `24`).
- `RETRIEVER_LAMBDA_MULT`: MMR diversity/score balance (default: `0.35`).
- `TOKENIZERS_PARALLELISM`: set to `false` to reduce local multiprocessing/tokenizer warning noise.
- `DEBUG_RAG`: set to `true` to include retrieval diagnostics in `/chat` JSON responses.

These defaults are intentionally conservative for limited-access demos. For broader production traffic, tune limits based on expected load, abuse risk, and budget.

3. Add documents to `./data`.

4. Start the server:

```bash
python3 -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 in your browser.

If `APP_API_KEY` is set, enter it in the in-page **App API key** field and click **Save key**. The key panel hides after a successful authenticated request and reappears if the server returns `401`.

## Security before publishing

Before pushing this project to GitHub:

- Keep `.env` out of version control (it contains real secrets).
- Commit only `.env.example` with placeholder values.
- Confirm `.gitignore` includes `.env` and `.env.*` plus `!.env.example`.
- Rotate any API keys that were ever shared, pasted in chats, or committed by mistake.
- If a secret was committed, remove it from git history and rotate it immediately.

## Rebuild the index

Use the "Rebuild index" button in the UI or run:

```bash
curl -X POST http://127.0.0.1:8000/ingest \
  -H "x-api-key: YOUR_APP_API_KEY"
```

If `APP_API_KEY` is not set, the `x-api-key` header is not required.

## Local vs Production

Use the same code in both places and change only runtime/infrastructure settings.

### Local development

- Keep the app directly accessible at `127.0.0.1:8000`.
- Use auto-reload for fast iteration.

```bash
python3 -m uvicorn app.main:app --reload
```

### Production server

- Run the app on localhost only (`127.0.0.1:8000`).
- Put nginx in front for HTTPS and public access (`80/443`).
- Do not use `--reload`.

```bash
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Recommended production env values in `.env`:

- strong `APP_API_KEY`
- tuned `RATE_LIMIT_PER_MIN`
- conservative `MAX_UPLOAD_MB`

## Production deployment flow (nginx)

Use this sequence on a Linux server.

### 1) Prepare app and env

```bash
cp .env.example .env
```

Set at least:

- `OPENAI_API_KEY`
- `APP_API_KEY` (strong random value)
- `MAX_UPLOAD_MB`
- `RATE_LIMIT_PER_MIN`

### 2) Run app with systemd

Sample unit file is included at `deploy/doc-chat.service`.

```bash
sudo cp deploy/doc-chat.service /etc/systemd/system/doc-chat.service
sudo systemctl daemon-reload
sudo systemctl enable --now doc-chat
sudo systemctl status doc-chat
```

### Example systemd service (production)

Use this if you want to customize service fields (paths/user may differ):

```ini
[Unit]
Description=Doc Chat FastAPI service
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/doc_chat
EnvironmentFile=/opt/doc_chat/.env
ExecStart=/opt/doc_chat/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

After customizing the unit file, run the same `systemctl daemon-reload` and service enable/start commands shown above.

### 3) Put nginx in front

Sample config is included at `deploy/nginx.conf`.

1. Replace `example.com` and certificate paths in `deploy/nginx.conf`.
2. Install/load the config in nginx.
3. Test and reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 4) Verify production

- Visit `https://your-domain` and confirm chat UI loads.
- Confirm protected endpoints reject missing key (`401`).
- Confirm normal traffic works with valid key from the UI.
- Check logs for startup/requests:

```bash
sudo systemctl status doc-chat
sudo journalctl -u doc-chat -n 100 --no-pager
```

## Reverse proxy alternatives

Both proxy samples terminate HTTPS, forward to `127.0.0.1:8000`, and enforce upload limits.

- `deploy/nginx.conf`
- `deploy/Caddyfile`

## Live demo script (quick)

Use this runbook for a clean ~3 minute walkthrough.

### 1) Start app

```bash
source .venv/bin/activate
python3 -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

### 2) Show secure empty state

1. Enter API key in **App API key** and click **Save key**.
2. Point out:
  - `No documents found.`
  - **Ask** disabled
  - **Rebuild index** disabled

### 3) Upload and index

1. Upload one small PDF (`< 2 MB`).
2. Point out status: `Upload complete.` then `Index rebuilt.`

### 4) Ask two questions

- `Who are Cornelius and Voltemand?`
- `Where are they mentioned?`

Point out source filename links under **Sources** opening in a new tab.

### 5) Show edge-case resilience and recovery

1. Delete the only document and confirm.
2. Point out:
  - `Index cleared.`
  - `No documents found.`
  - **Ask** disabled again
3. Upload a document and ask one final question to show full recovery.
