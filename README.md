# kko-backend

A modern, async FastAPI backend for smart document management—featuring PDF text extraction, automatic summarization, department/email finder, and notification workflows. Built for extensibility and cloud integration.

## Features

- **PDF Upload & Text Extraction:** Processes uploaded PDFs using Google Document AI for high-accuracy OCR.
- **Automated Summarization:** Uses Azure GPT-4o for structured, professional summaries.
- **Department & Email Extraction:** Detects and extracts department names and email addresses from documents with AI.
- **Chat FAQ over Documents:** Ask questions about any uploaded document and get short, clear answers via GPT-4o.
- **Department Email Notifications:** Optionally sends document summaries to relevant email addresses asynchronously.
- **Pagination API:** List, paginate, and browse all processed documents via `/all`.
- **Health Monitoring:** Simple `/health` endpoint for uptime checks.
- **CORS Support:** Compatible with any frontend UI.

## Tech Stack

- Python 3.11+, FastAPI, Motor (MongoDB), Google Cloud Document AI, Azure OpenAI (GPT-4o), aiosmtplib
- Environment config managed via `.env`

## Setup

### Environment Variables

Create a `.env` file with these variables:

MONGO_URI=mongodb+srv://<username>:<password>@cluster
DB_NAME=kko_db
COLLECTION_NAME=summaries

AZURE_OPENAI_KEY=...
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-02-15-preview

GOOGLE_CREDENTIALS_JSON="json string here"
GOOGLE_PROJECT_ID=your_project_id
DOCUMENT_AI_LOCATION=us
DOCUMENT_AI_PROCESSOR_ID=...

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=youremail@domain
SMTP_PASSWORD=yourpassword

text

### Installation

git clone https://github.com/aditya-Kumar421/kko-backend.git
cd kko-backend
pip install -r requirements.txt

text

### Running

uvicorn main:app --reload

text

## API Endpoints

| Method | Path      | Description                                                    |
|--------|-----------|----------------------------------------------------------------|
| POST   | /upload   | Upload PDF, extract text, summarize, extract depts/emails, store |
| POST   | /chat     | Ask a question about a doc (by `mongo_id`), get AI answer       |
| GET    | /all      | List all document summaries with pagination                    |
| GET    | /health   | Service status (for monitoring)                                |

### /upload

- **Request:** `multipart/form-data` with `file: PDF`
- **Response:**
{
"filename": "example.pdf",
"departments": [{"name": "HR", "email": "hr@org.com"}],
"summary": "...",
"mongo_id": "..."
}

text

### /chat

- **Request:**
{
"mongo_id": "...",
"question": "What is the main notice?"
}

text
- **Response:**
{"answer": "Brief answer in 20-30 words."}

text

### /all

- **Query Params:** `page`, `limit`
- **Returns:** Paginated document list with total count.

### /health

- Simple service status check.

## Contributing

- Use PRs for changes.
- Update requirements if adding/removing dependencies.
- See code style in `main.py`.

## License

MIT

---

*Project maintained by [aditya-Kumar421](https://github.com/aditya-Kumar421).*