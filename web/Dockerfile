FROM node:22-slim AS frontend
WORKDIR /frontend
COPY web/package*.json ./
RUN npm ci
COPY web/index.html ./
COPY web/src ./src
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg MASGENT_WEB_DATA=/data OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
ARG INSTALL_CHGNET=0
WORKDIR /app
COPY web/requirements.txt ./web/
RUN pip install --no-cache-dir -r web/requirements.txt \
    && if [ "$INSTALL_CHGNET" = "1" ]; then \
         pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
         && pip install --no-cache-dir chgnet; \
       fi \
    && useradd --create-home --uid 10001 masgent \
    && mkdir /data && chown masgent:masgent /data
COPY src ./src
COPY streamlit_app/core ./streamlit_app/core
COPY streamlit_app/assets/structures ./streamlit_app/assets/structures
COPY streamlit_app/assets/neb ./streamlit_app/assets/neb
COPY web/server.py ./web/
COPY --from=frontend /frontend/dist ./web/dist
USER masgent
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "web.server:create_app()"]
