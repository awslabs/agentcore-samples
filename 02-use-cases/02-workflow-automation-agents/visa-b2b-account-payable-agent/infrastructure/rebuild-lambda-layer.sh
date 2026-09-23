#!/bin/bash

# Rebuild Lambda Layer for Linux

set -e

echo "Building Lambda layer..."

# Create temporary directory
rm -rf lambda-layer-temp
mkdir -p lambda-layer-temp/nodejs

# Copy package files
cp ../backend/package.json lambda-layer-temp/nodejs/

if [ -f ../backend/package-lock.json ]; then
  cp ../backend/package-lock.json lambda-layer-temp/nodejs/
  INSTALL_CMD="npm ci --omit=dev"
else
  INSTALL_CMD="npm install --omit=dev --production"
fi

# Install dependencies in Docker (Linux environment)
docker run --rm \
  --platform linux/amd64 \
  -v "$(pwd)/lambda-layer-temp/nodejs:/var/task" \
  -w /var/task \
  --entrypoint /bin/bash \
  public.ecr.aws/lambda/nodejs:20 \
  -c "$INSTALL_CMD"

# Create layer zip
cd lambda-layer-temp
zip -r ../../lambda-layer.zip nodejs/ > /dev/null
cd ..

# Clean up
rm -rf lambda-layer-temp

echo "✓ Lambda layer built: $(ls -lh lambda-layer.zip | awk '{print $5}')"
