from fastapi import FastAPI, File, UploadFile, HTTPException, status, BackgroundTasks
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
from openai import AsyncAzureOpenAI
import os
from datetime import datetime
from typing import List, Dict
import logging
from dotenv import load_dotenv
import json
import aiosmtplib
from email.message import EmailMessage
from bson import ObjectId
from fastapi.middleware.cors import CORSMiddleware
# Load environment variables from .env file
load_dotenv()

# Configure logging to suppress pymongo DEBUG logs
logging.basicConfig(level=logging.INFO)  # Set root logger to INFO
logger = logging.getLogger("main")  # Use a specific logger for your app

# Initialize FastAPI app
app = FastAPI(title="kko-backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Configuration from environment variables
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "kko_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "summaries")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID")
DOCUMENT_AI_LOCATION = os.getenv("DOCUMENT_AI_LOCATION", "us")
DOCUMENT_AI_PROCESSOR_ID = os.getenv("DOCUMENT_AI_PROCESSOR_ID")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Global variables for clients
mongo_client = None
documentai_client = None
azure_openai_client = None

# Pydantic models
class SummaryResponse(BaseModel):
    filename: str
    departments: List[Dict[str, str]]
    summary: str
    mongo_id: str

class ChatRequest(BaseModel):
    mongo_id: str
    question: str

class ChatResponse(BaseModel):
    answer: str

# Startup event to initialize clients
@app.on_event("startup")
async def startup_event():
    global mongo_client, documentai_client, azure_openai_client
    try:
        # Initialize MongoDB client
        mongo_client = AsyncIOMotorClient(MONGO_URI)
        await mongo_client.server_info()
        logger.info("MongoDB connection established")

        # Initialize Google Cloud Document AI client
        if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_PROJECT_ID or not DOCUMENT_AI_PROCESSOR_ID:
            raise ValueError("GOOGLE_CREDENTIALS_JSON, GOOGLE_PROJECT_ID, or DOCUMENT_AI_PROCESSOR_ID not set in .env")
        
        try:
            creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
            credentials = service_account.Credentials.from_service_account_info(creds_dict)
            documentai_client = documentai.DocumentProcessorServiceClient(credentials=credentials)
            logger.info("Google Document AI client initialized")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid GOOGLE_CREDENTIALS_JSON format: {str(e)}")
        except Exception as e:
            raise ValueError(f"Failed to initialize Google Document AI client: {str(e)}")

        # Initialize Azure OpenAI client
        if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
            raise ValueError("AZURE_OPENAI_KEY or AZURE_OPENAI_ENDPOINT not set")
        azure_openai_client = AsyncAzureOpenAI(
            api_key=AZURE_OPENAI_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION
        )
        logger.info("Azure OpenAI client initialized")

        # Validate SMTP configuration
        if not SMTP_USER or not SMTP_PASSWORD or not SMTP_SERVER:
            raise ValueError("SMTP_USER, SMTP_PASSWORD, or SMTP_SERVER not set in .env")
        logger.info("SMTP configuration loaded")

    except Exception as e:
        logger.error(f"Startup error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize services: {str(e)}"
        )

# Helper function to extract text from PDF using Google Document AI
async def extract_text_from_pdf(file_content: bytes) -> str:
    try:
        # Construct the processor resource name
        processor_name = f"projects/{GOOGLE_PROJECT_ID}/locations/{DOCUMENT_AI_LOCATION}/processors/{DOCUMENT_AI_PROCESSOR_ID}"

        # Create RawDocument for Document AI
        raw_document = documentai.RawDocument(content=file_content, mime_type="application/pdf")

        # Create ProcessRequest
        request = documentai.ProcessRequest(
            name=processor_name,
            raw_document=raw_document
        )

        # Process the document
        response = documentai_client.process_document(request=request)
        document = response.document

        # Extract text
        if not document.text:
            raise Exception("No text extracted from the document")
        
        return document.text
    except Exception as e:
        logger.error(f"Error extracting text from PDF with Document AI: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract text from PDF: {str(e)}"
        )

# Helper function to process text with Azure OpenAI GPT-4o
async def process_with_gpt4o(text: str) -> tuple[str, List[Dict[str, str]]]:
    try:
        prompt = f"""
        Analyze the following text extracted from a PDF document:

        {text}

        Tasks:
        1. Summarize the content into a clear, concise version in bullet-point format, written in a professional tone suitable for direct communication to a user.
        2. Identify all departments or organizational units mentioned in the text and extract their associated email addresses, if available. Each entry should include the department name and its email. If no email is provided, use null.

        Return the response strictly in JSON format:
        ```json
        {{
            "summary": "<summary_text>",
            "departments": [
                {{"name": "<department_name>", "email": "<department_email_or_null>"}},
                ...
            ]
        }}
        ```
        """
        response = await azure_openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that summarizes documents and extracts department names with emails. Always return a JSON object with 'summary' (string) and 'departments' (list of dicts)."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        result = response.choices[0].message.content
        
        try:
            parsed_result = json.loads(result)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Azure OpenAI response as JSON: {result}, error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid JSON response from Azure OpenAI"
            )
        
        if not isinstance(parsed_result, dict) or "summary" not in parsed_result or "departments" not in parsed_result:
            logger.error(f"Invalid response structure: {parsed_result}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Azure OpenAI response missing required fields"
            )
        
        if not isinstance(parsed_result["summary"], str):
            logger.error(f"Invalid summary type: {type(parsed_result['summary'])}, value: {parsed_result['summary']}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Azure OpenAI returned invalid summary type"
            )
        if not isinstance(parsed_result["departments"], list) or not all(isinstance(d, dict) and "name" in d and "email" in d for d in parsed_result["departments"]):
            logger.error(f"Invalid departments format: {parsed_result['departments']}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid departments format returned from Azure OpenAI"
            )
        
        return parsed_result["summary"], parsed_result["departments"]
    except Exception as e:
        logger.error(f"Error processing text with Azure OpenAI: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process text with Azure OpenAI: {str(e)}"
        )

# Helper function to prepare email data
def prepare_email_data(departments: List[Dict[str, str]], summary: str, filename: str) -> List[dict]:
    email_list = []
    for dept in departments:
        if dept.get("email"):
            email_list.append({
                "department": dept["name"],
                "summary": summary,
                "subject": f"Notice Summary for {dept['name']}: {filename}",
                "to": dept["email"]
            })
    return email_list

# Helper function to send emails asynchronously
async def send_emails_async(email_data: List[Dict[str, str]]):
    if not email_data:
        logger.warning("No emails to send.")
        return

    for email_entry in email_data:
        msg = EmailMessage()
        msg["From"] = SMTP_USER
        msg["To"] = email_entry["to"]
        msg["Subject"] = email_entry["subject"]
        msg.set_content(email_entry["summary"])

        try:
            await aiosmtplib.send(
                msg,
                hostname=SMTP_SERVER,
                port=SMTP_PORT,
                start_tls=True,
                username=SMTP_USER,
                password=SMTP_PASSWORD,
            )
            logger.info(f"Email sent to {email_entry['to']} ({email_entry['department']})")
        except Exception as e:
            logger.error(f"Failed to send email to {email_entry['to']}: {str(e)}")

# Helper function to answer questions with Azure OpenAI
async def answer_question(mongo_id: str, question: str) -> str:
    try:
        collection = mongo_client[DB_NAME][COLLECTION_NAME]
        document = await collection.find_one({"_id": ObjectId(mongo_id)})
        if not document:
            logger.error(f"No document found for mongo_id: {mongo_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )
        
        summary = document.get("summary", "")
        extracted_text = document.get("extracted_text", "")
        
        prompt = f"""
        Using the following document summary and extracted text, answer the question in 20–30 words:
        
        Summary: {summary}  
        Extracted Text: {extracted_text}
        
        Question: {question}
        
        Provide a clear, concise answer (20–30 words) in plain text.
        """
        
        response = await azure_openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Answer questions based on the provided document summary and text in 20–30 words."},
                {"role": "user", "content": prompt}
            ]
        )
        
        answer = response.choices[0].message.content.strip()
        
        word_count = len(answer.split())
        if word_count < 20 or word_count > 30:
            logger.warning(f"Answer word count {word_count} outside 20–30 range: {answer}")
            answer = " ".join(answer.split()[:30]) if word_count > 30 else answer
        
        return answer
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error answering question: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to answer question: {str(e)}"
        )

# Upload endpoint
@app.post("/upload", response_model=SummaryResponse)
async def upload_pdf(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are allowed"
        )

    try:
        file_content = await file.read()
        extracted_text = await extract_text_from_pdf(file_content)
        summary, departments = await process_with_gpt4o(extracted_text)
        
        if not isinstance(summary, str):
            logger.error(f"Invalid summary type: {type(summary)}, value: {summary}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid summary format returned from Azure OpenAI"
            )
        if not isinstance(departments, list) or not all(isinstance(d, dict) and "name" in d and "email" in d for d in departments):
            logger.error(f"Invalid departments format: {departments}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid departments format returned from Azure OpenAI"
            )
        
        email_data = prepare_email_data(departments, summary, file.filename)
        if background_tasks:
            background_tasks.add_task(send_emails_async, email_data)
        
        collection = mongo_client[DB_NAME][COLLECTION_NAME]
        document = {
            "filename": file.filename,
            "extracted_text": extracted_text,
            "summary": summary,
            "departments": departments,
            "timestamp": datetime.utcnow(),
            "email_data": email_data
        }
        
        result = await collection.insert_one(document)
        
        response = SummaryResponse(
            filename=file.filename,
            departments=departments,
            summary=summary,
            mongo_id=str(result.inserted_id)
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing upload: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
    finally:
        await file.close()

# Chatbot endpoint
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        answer = await answer_question(request.mongo_id, request.question)
        return ChatResponse(answer=answer)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
    
@app.get("/all")
async def get_all_documents(page: int = 1, limit: int = 5):
    """Return paginated documents from the collection.

    Query Params:
      page: 1-based page index (default 1)
      limit: page size (default 5, max 50)
    """
    try:
        if page < 1:
            page = 1
        if limit < 1:
            limit = 5
        if limit > 50:
            limit = 50

        collection = mongo_client[DB_NAME][COLLECTION_NAME]

        total = await collection.count_documents({})
        skip = (page - 1) * limit

        cursor = collection.find().sort("timestamp", -1).skip(skip).limit(limit)
        docs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            docs.append(doc)

        total_pages = (total + limit - 1) // limit if limit else 1

        return {
            "data": docs,
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1
        }
    except Exception as e:
        logger.error(f"Error fetching paginated documents: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch documents: {str(e)}"
        )

# Health check endpoint
@app.get("/health")
async def health_check():
    try:
        await mongo_client.server_info()
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unhealthy"
        )

# Cleanup on shutdown
@app.on_event("shutdown")
async def shutdown_event():
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB connection closed")

