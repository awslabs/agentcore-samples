# Fake Okta for the local stack (no dependencies). Built as devbox-fake-okta:dev by compose.yaml.
FROM public.ecr.aws/docker/library/node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1
WORKDIR /app
COPY package.json server.mjs directory.json ./
USER node
ENV FAKE_OKTA_HOST=0.0.0.0 FAKE_OKTA_PORT=9400
EXPOSE 9400
CMD ["node", "server.mjs"]
