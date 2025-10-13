# Use lightweight Python image
FROM python:3.10-slim

# Install Git and set user info
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/* \
    && git config --global user.email "23f2005020@ds.study.iitm.ac.in" \
    && git.config --global user.name "saksham-bansal-1"

# Create non-root user
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

# Set work directory
WORKDIR /app

# Install dependencies
COPY --chown=user requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY --chown=user . /app

# Expose Hugging Face default port
EXPOSE 7860

# Run FastAPI app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]

