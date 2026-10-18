FROM python:3.12-slim
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY pyproject.toml README.md /src/
COPY src /src/src
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" /src && rm -rf /src
RUN useradd -u 10001 -M -d /nonexistent app
USER app
ENV PORT=3000
EXPOSE 3000
CMD ["16688-mcp"]
