# Builder stage
FROM node:20-alpine AS builder

# Install system dependencies
RUN apk add --no-cache git

# Set working directory
WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Set build-time environment variable for Next.js
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_TELEMETRY_DISABLED=1

# Build the application
RUN npm run build

# Runtime stage
FROM node:20-alpine AS runner

# Install system dependencies
RUN apk add --no-cache curl

# Set working directory
WORKDIR /app

# Create non-root user
RUN addgroup -S nextjs && adduser -S nextjs -G nextjs

# Set environment variables
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Copy public directory
COPY --from=builder --chown=nextjs:nextjs /app/public ./public

# Copy standalone output and static files
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:3000/ || exit 1

# Switch to non-root user
USER nextjs

# Start the application
CMD ["node", "server.js"]