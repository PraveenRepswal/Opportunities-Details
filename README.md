# Opportunities Details

A high-performance **Retrieval-Augmented Generation (RAG)** system designed to aggregate global scholarship and opportunity data. This project scrapes multiple opportunity portals concurrently, indexes them using vector search, and uses a local Large Language Model (Qwen3:3b via Ollama) to answer user queries with high precision and privacy.

## 🚀 Features

*   **High-Speed Scraper:** asynchronous scraping pipeline using `aiohttp` and `asyncio` that fetches data from 6+ portals concurrently.
*   **Local RAG System:** Fully local inference using **Ollama**, eliminating cloud API costs and ensuring data privacy.
*   **Vector Search:** Implements **FAISS** vector database with **HuggingFace Embeddings** (`all-MiniLM-L6-v2`) for semantic retrieval of relevant opportunities.
*   **Data Processing:** Robust cleaning pipeline using `trafilatura` and `BeautifulSoup`, with intelligent deduplication using Jaccard similarity.
*   **Interactive Interfaces:** Includes both a command-line chat interface and a **Streamlit** web UI.

## 🛠️ Tech Stack

*   **Language:** Python
*   **LLM Orchestration:** LangChain (Core, Community, Ollama)
*   **Inference Engine:** Ollama (Qwen3:4b)
*   **Vector DB:** FAISS
*   **Scraping:** Aiohttp, Asyncio, BeautifulSoup4, Trafilatura
*   **Embeddings:** HuggingFace (`sentence-transformers`)
*   **Frontend:** Streamlit

## 📋 Prerequisites

1.  **Python 3.10+**
2.  **Ollama**: Download and install from [ollama.com](https://ollama.com/).
3.  **Llama 3.2 Model**: Run the following command in your terminal:
    ```bash
    ollama pull llama3.2
    ```

## ⚡ Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/PraveenRepswal/Opportunities-Details.git
    cd Opportunities-Details
    ```

2.  Create and activate a virtual environment:
    ```bash
    python -m venv .venv
    # Windows
    .venv\Scripts\activate
    # Linux/Mac
    source .venv/bin/activate
    ```

3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## 🏃‍♂️ Usage

### 1. Command Line Interface (CLI)
To run the scraper, build the index, and chat in the terminal:
```bash
python test2.py
```
*The script will first scrape data (or use fallback), build the FAISS index, and then prompt for user input.*

### 2. Streamlit Web Interface
To run the web-based chat application:
```bash
streamlit run LlamaWrapper.py
```

## 📂 Project Structure

*   `test2.py`: Main CLI entry point. Handles the RAG pipeline construction and chat loop.
*   `scraper.py`: Orchestrator for running all individual scraper modules concurrently.
*   `LlamaWrapper.py`: Streamlit-based user interface.
*   `youthop.py`, `greatyop.py`, `scholars4dev.py`, etc.: Individual scraper modules for specific portals.
*   `faiss_index/`: Directory where the vector store is saved (if enabled).

## 📄 License
[MIT](LICENSE)


### This Readme is written using AI