#!/bin/bash

# Build and create a zip file for manual Amplify deployment

set -e

echo "📦 Building frontend for deployment..."
npm run build

echo "🗜️  Creating deployment zip..."
cd dist
zip -r ../rtp-overlay-deployment.zip .
cd ..

echo ""
echo "✅ Deployment package created: rtp-overlay-deployment.zip"
echo ""
echo "📤 To deploy to Amplify:"
echo "1. Go to https://console.aws.amazon.com/amplify/"
echo "2. Select your app (or create new)"
echo "3. Click 'Deploy updates'"
echo "4. Upload rtp-overlay-deployment.zip"
echo ""
