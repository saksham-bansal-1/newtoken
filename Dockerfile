FROM python:3.10-slim

# Install git
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Set git identity for commits (important for round 2)
RUN git config --global user.email "23f2005020@ds.study.iitm.ac.in" \
    && git config --global user.name "saksham-bansal-1"

# Create user
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

COPY --chown=user ./requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY --chown=user . /app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]

