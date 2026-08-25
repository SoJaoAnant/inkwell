# Inkwell

Turns typed notes into handwritten-looking, slightly-scanned page images, generated from your own handwriting.

## Setup

```bash
git clone <this-repo-url>
cd Natural_handwriter/inkwell
python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

Install dependencies and run:

```bash
pip install -r requirements.txt
streamlit run app.py
```

This opens the app at `http://localhost:8501`.

## First-time use

1. In the sidebar, download the blank handwriting template (PDF) and print it.
2. Fill in each box (4 variants per character) using a dark blue or black pen.
3. Scan/photograph the filled pages, zip the images, and upload the zip under "Set up / add a profile" to build your handwriting profile.
4. Type your notes in the main text box and hit Generate.
