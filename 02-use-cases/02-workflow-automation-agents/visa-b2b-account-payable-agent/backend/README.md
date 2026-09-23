# RTP Overlay Backend API

Backend API for the RTP Overlay System - Accounts Payable & Procurement Management

## Tech Stack

- **Runtime**: Node.js 20+ with TypeScript
- **Framework**: Express.js
- **Database**: PostgreSQL with TypeORM
- **Authentication**: JWT tokens
- **File Storage**: AWS S3
- **Logging**: Winston

## Getting Started

### Prerequisites

- Node.js 20+
- PostgreSQL 15+
- AWS Account (for S3)

### Installation

```bash
# Install dependencies
npm install

# Copy environment file
cp .env.example .env

# Edit .env with your configuration
nano .env
```

### Database Setup

```bash
# Create database
createdb rtp_overlay

# Run migrations (when available)
npm run migration:run
```

### Development

```bash
# Start development server with hot reload
npm run dev

# Build for production
npm run build

# Start production server
npm start
```

## Project Structure

```
backend/
├── src/
│   ├── config/          # Configuration files
│   ├── controllers/     # Route controllers
│   ├── entities/        # TypeORM entities
│   ├── middleware/      # Express middleware
│   ├── repositories/    # Data access layer
│   ├── services/        # Business logic
│   ├── types/           # TypeScript types
│   ├── utils/           # Utility functions
│   └── index.ts         # Application entry point
├── package.json
├── tsconfig.json
└── README.md
```

## API Endpoints

### Health Check
- `GET /health` - Server health status

### Authentication (Coming Soon)
- `POST /api/auth/login` - User login
- `POST /api/auth/refresh` - Refresh access token
- `POST /api/auth/logout` - User logout

### Purchase Orders (Coming Soon)
- `GET /api/purchase-orders` - List purchase orders
- `GET /api/purchase-orders/:id` - Get purchase order details

### Goods Receipts (Coming Soon)
- `POST /api/goods-receipts` - Create goods receipt
- `GET /api/goods-receipts` - List goods receipts
- `POST /api/goods-receipts/:id/documents` - Upload receipt documents

## Environment Variables

See `.env.example` for all available configuration options.

## License

MIT
