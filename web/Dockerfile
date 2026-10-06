FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
# Scans de plusieurs Mo envoyes par lots : la limite par defaut d'adapter-node (512 Ko) est bien trop basse.
ENV BODY_SIZE_LIMIT=1G
COPY --from=build /app/build ./build
COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
EXPOSE 3000
CMD ["node", "build"]
