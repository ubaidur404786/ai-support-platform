# The web app image, built in two stages.
#
# Stage 1 uses Node to turn the React code into plain HTML, CSS and JS files.
# Stage 2 copies only those files into a small nginx image. Node and the
# node_modules folder (hundreds of MB) never reach the final image.
#
# Build from the frontend folder:
#   docker build -t support-web frontend

FROM node:20-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
