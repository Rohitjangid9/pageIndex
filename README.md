# PageIndex FastAPI App

This is a FastAPI application for Vectorless RAG using the PageIndex library. It provides endpoints to upload a PDF (generating a tree structure) and a chat endpoint to perform reasoning-based RAG.

## Setup Instructions

**1. Create a Virtual Environment**
```bash
python -m venv .venv
```

**2. Activate the Environment**
- On Windows:
  ```bash
  .venv\Scripts\activate
  ```
- On Mac/Linux:
  ```bash
  source .venv/bin/activate
  ```

**3. Install Dependencies**
```bash
pip install -r requirements.txt
```

**4. Set Up Environment Variables**
Create a new file named `.env` in the root folder and add your OpenAI API key:
```env
OPENAI_API_KEY=your_openai_api_key_here
```

## Running the Application

Start the FastAPI server using Uvicorn:
```bash
python -m uvicorn app:app --reload
```

- **Swagger UI:** Open your browser and go to `http://127.0.0.1:8000/docs`

## API Endpoints

1. **`POST /upload-pdf/`**
   - Upload your PDF file here. 
   - It parses the PDF, extracts the raw text, and uses GPT-4o to generate a JSON tree based on the document's Table of Contents.

2. **`POST /chat/`**
   - Pass your question and the exact `pdf_name` (without the `.pdf` extension).
   - It performs Reasoning-Based Retrieval: finding the most relevant nodes from the JSON tree, fetching their full text, and generating an answer.

All detailed step-by-step logs for both endpoints are saved in the `upload pdf logs/` directory as `.md` files.
