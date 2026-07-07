FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8768
CMD ["supersocks-url-scraper", "--serve", "--host", "0.0.0.0", "--port", "8768"]
