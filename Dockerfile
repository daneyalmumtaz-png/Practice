FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir pypdf
COPY server.py companion.html manual.pdf ./
ENV HOST=0.0.0.0
ENV PORT=8000
ENV OLLAMA_URL=http://ollama:11434/api/chat
ENV LLM_MODEL=llama3.2
EXPOSE 8000
CMD ["python", "server.py"]
