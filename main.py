import os
import tempfile
import subprocess
import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI

# Load .env file
load_dotenv()

# --- Environment variables ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
STUDENT_SECRET = os.getenv("STUDENT_SECRET")
GITHUB_OWNER = os.getenv("GITHUB_OWNER")
EVALUATION_URL = os.getenv("EVALUATION_URL", "http://localhost:9000/evaluation")
DATABASE_URL = os.getenv("DATABASE_URL")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

app = FastAPI()

# --- Validate required envs ---
required_vars = ["GITHUB_TOKEN", "STUDENT_SECRET", "GITHUB_OWNER", "OPENAI_API_KEY"]
missing = [var for var in required_vars if not os.getenv(var)]
if missing:
    print(f"⚠️ Missing required env vars: {missing}  (fill .env)")

# --- OpenAI client ---
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Helper: Enable GitHub Pages ---
def enable_github_pages(repo_name: str):
    print(f"🌐 Enabling GitHub Pages for {repo_name}...")
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo_name}/pages"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {"source": {"branch": "main", "path": "/"}}
    r = requests.post(url, headers=headers, json=data)
    if r.status_code in [201, 204]:
        print("✅ GitHub Pages enabled!")
    else:
        print(f"⚠️ Pages setup failed: {r.status_code} - {r.text}")


# --- Request model ---
class APIRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    evaluation_url: str


@app.get("/")
def root():
    return {"ok": True, "message": "LLM Deployment API is live"}


@app.post("/api-endpoint")
async def api_endpoint(payload: APIRequest):
    if payload.secret != STUDENT_SECRET:
        return {"error": "Invalid secret"}

    task = payload.task
    round_num = payload.round
    repo_name = f"{task}-round{round_num}"
    clone_url = f"https://{GITHUB_OWNER}:{GITHUB_TOKEN}@github.com/{GITHUB_OWNER}/{repo_name}.git"

    print(f"🚀 build start repo={repo_name} task={task} round={round_num}")

    # --- 1. Create GitHub repo ---
    create_repo_url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    repo_data = {"name": repo_name, "private": False, "auto_init": True}

    create_response = requests.post(create_repo_url, headers=headers, json=repo_data)
    if create_response.status_code != 201:
        return {
            "error": f"GitHub repo creation failed: {create_response.status_code} {create_response.text}"
        }

    print(f"✅ repo created: https://github.com/{GITHUB_OWNER}/{repo_name}")

    # --- 2. Generate simple HTML with OpenAI (or static fallback) ---
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Hello World</title>
        <style>
            body {{
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                background-color: #f8f9fa;
                font-family: Arial, sans-serif;
            }}
            h1 {{
                font-size: 48px;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <h1>Hello World 👋</h1>
        <p id="app-status">Status: Loaded Successfully</p>
        <script>
            document.getElementById("app-status").innerText = "Page ready!";
        </script>
    </body>
    </html>
    """

    # --- 3. Push files to GitHub ---
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", clone_url, tmp], check=True)

        with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)

        with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
            f.write(
                f"# {repo_name}\n\nAuto-generated for **{task}** round **{round_num}**.\n\nBrief:\n\n```\n{payload.brief}\n```"
            )

        with open(os.path.join(tmp, "LICENSE"), "w", encoding="utf-8") as f:
            f.write("MIT License\n\nCopyright (c) 2025")

        # ✅ Fix author identity
        subprocess.run(["git", "-C", tmp, "config", "user.email", "23f2005020@ds.study.iitm.ac.in"], check=True)
        subprocess.run(["git", "-C", tmp, "config", "user.name", "saksham-bansal-1"], check=True)

        subprocess.run(["git", "-C", tmp, "add", "."], check=True)
        subprocess.run(["git", "-C", tmp, "commit", "-m", "initial commit"], check=True)
        subprocess.run(["git", "-C", tmp, "push"], check=True)

    print("📦 code pushed")

    # --- 4. Enable GitHub Pages ---
    enable_github_pages(repo_name)

    # --- 5. Return URLs ---
    repo_url = f"https://github.com/{GITHUB_OWNER}/{repo_name}"
    pages_url = f"https://{GITHUB_OWNER}.github.io/{repo_name}/"

    return {"status": "success", "repo_url": repo_url, "pages_url": pages_url}


@app.post("/evaluation")
async def evaluation_endpoint(request: Request):
    data = await request.json()
    print("📩 Evaluation received:", data)
    return {"ok": True, "received": data}

