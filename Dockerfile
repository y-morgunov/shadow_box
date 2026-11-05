FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SHADOW_HOST=0.0.0.0 SHADOW_DATA=/data OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
WORKDIR /app
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements-web.txt && useradd --uid 10001 --create-home shadow && mkdir /data && chown shadow:shadow /data
COPY radial_shadow.py shadow_cube.py printing.py lighting.py ./
COPY web ./web
COPY examples/astronaut/input.png ./examples/astronaut/input.png
USER shadow
EXPOSE 8080
CMD ["python", "-m", "web.server"]
