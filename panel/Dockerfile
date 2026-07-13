# tiny control panel: docker CLI + compose plugin + python stdlib (no pip deps)
FROM docker:27-cli
RUN apk add --no-cache python3
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 8080
CMD ["python3", "app.py"]
