# Edge Lambda image: the AWS Lambda Node.js 24 base (arm64) with the static bundle baked in.
#
#   build/build.sh                                   # produces dist/
#   docker buildx build --platform linux/arm64 --load -t devbox-edge:dev .
#
# The base is pinned by the digest of its multi-arch index (the build picks linux/arm64). To update it, change
# the digest here, or pass --build-arg BASE_IMAGE=public.ecr.aws/lambda/nodejs:24@sha256:<digest>.
ARG BASE_IMAGE=public.ecr.aws/lambda/nodejs:24@sha256:b5f6c6f20c76dd64924c4d09b1e3eadd91ea59eb3279d63eb99bc9102c61f58f
FROM ${BASE_IMAGE}

# Six OS packages upgraded to these exact newer versions from the Amazon Linux 2023 repository (dnf checks their
# signatures). Drop this once a newer base digest has them. The handler uses only Node's built-in modules, so the
# runtime's bundled AWS SDK, npm and corepack aren't needed and are removed.
RUN dnf -y upgrade --releasever=latest --setopt=install_weak_deps=0 \
      curl-minimal-8.21.0-5.amzn2023.0.2 libcurl-minimal-8.21.0-5.amzn2023.0.2 \
      pcre2-10.40-1.amzn2023.0.4 pcre2-syntax-10.40-1.amzn2023.0.4 \
      rpm-4.16.1.3-29.amzn2023.0.8 rpm-libs-4.16.1.3-29.amzn2023.0.8 \
 && dnf clean all \
 && rm -rf /var/cache/dnf /var/runtime/node_modules/@aws-sdk \
      /var/lang/lib/node_modules/npm /var/lang/lib/node_modules/corepack \
      /var/lang/bin/npm /var/lang/bin/npx /var/lang/bin/corepack

COPY src/handler.mjs src/limits.mjs ${LAMBDA_TASK_ROOT}/
COPY dist/ ${LAMBDA_TASK_ROOT}/dist/

CMD ["handler.handler"]
