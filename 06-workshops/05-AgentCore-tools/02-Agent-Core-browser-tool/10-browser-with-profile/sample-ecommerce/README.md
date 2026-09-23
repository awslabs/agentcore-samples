# Sample E-Commerce

Simple static e-commerce site for t-shirts with persistent shopping cart using localStorage.

## Run Locally

```bash
cd sample-ecommerce
python3 -m http.server 8000
```

Then open: http://localhost:8000

## Deploy to AWS with CloudFormation

### Quick Deploy

```bash
chmod +x deploy.sh
./deploy.sh
```

The script will automatically:
- Create S3 bucket (or use existing if name conflicts)
- Deploy CloudFormation stack with CloudFront
- Upload website files
- Invalidate CloudFront cache
- Display CloudFront URL

### Update Existing Deployment

```bash
./update.sh
```

Updates files in S3 and invalidates CloudFront cache without touching infrastructure.

### Manual Deploy

```bash
# Create bucket
BUCKET_NAME="sample-ecommerce-static-site-$(date +%s)"
aws s3 mb s3://$BUCKET_NAME --region us-east-1

# Deploy CloudFormation
aws cloudformation deploy \
  --template-file cloudformation.yaml \
  --stack-name sample-ecommerce-stack \
  --parameter-overrides BucketName=$BUCKET_NAME \
  --region us-east-1

# Upload files
aws s3 sync . s3://$BUCKET_NAME/ \
  --exclude "*.yaml" --exclude "*.sh" --exclude "*.md"

# Get CloudFront URL
aws cloudformation describe-stacks \
  --stack-name sample-ecommerce-stack \
  --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' \
  --output text
```

### Delete Stack (Keeps Bucket)

```bash
./delete.sh
```

Or manually:
```bash
aws cloudformation delete-stack --stack-name sample-ecommerce-stack --region us-east-1
```

The S3 bucket is NOT managed by CloudFormation and will remain after stack deletion.

## Deploy to S3 (Simple - No CloudFront)

```bash
# Create bucket
aws s3 mb s3://your-bucket-name

# Upload files
aws s3 sync . s3://your-bucket-name --acl public-read

# Enable static website hosting
aws s3 website s3://your-bucket-name --index-document index.html
```

Your site will be available at: http://your-bucket-name.s3-website-[region].amazonaws.com

## Features

- 6 t-shirt products with images
- Add to cart
- View cart with thumbnails and total
- Remove items
- Cart persists in browser (localStorage)
- Clear URL paths for automation (#home, #cart)

## Playwright Navigation

```python
# Navigate to pages
page.goto("http://localhost:8000/#home")
page.goto("http://localhost:8000/#cart")

# Or use selectors
page.locator('[data-page="home"]')
page.locator('[data-page="cart"]')
```
