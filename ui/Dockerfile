# Web explorer for auditgym episodes (Railway). Not used by the official check.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY auditgym ./auditgym
COPY configs ./configs
COPY episodes ./episodes
COPY ui ./ui
EXPOSE 8000
CMD ["python", "ui/server.py"]
