FROM node:20-slim

WORKDIR /app

# Build tools for better-sqlite3 native compilation + Chromium for whatsapp-web.js
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 make g++ chromium \
    && rm -rf /var/lib/apt/lists/*

COPY package*.json ./
RUN npm install --omit=dev \
    && npm rebuild better-sqlite3 --build-from-source

COPY index.js history.js ./
RUN mkdir -p /app/history /app/.wwebjs_auth

WORKDIR /app/history
CMD ["node", "/app/index.js"]
