FROM node:22-slim

RUN corepack enable && corepack prepare pnpm@10.9.0 --activate

WORKDIR /app

COPY package.json ./
RUN pnpm install || true

COPY . .

EXPOSE 5173
CMD ["pnpm", "dev", "--host", "0.0.0.0"]
