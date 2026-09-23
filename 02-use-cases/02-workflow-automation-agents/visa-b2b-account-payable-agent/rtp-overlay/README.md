# RTP Overlay - Real-Time Payments Overlay System

A comprehensive accounts payable and procurement management application built with React, TypeScript, and Material-UI.

## Features

- **Delivery Acceptance Module**: Capture goods receipts for purchase orders
- **Quality Control Module**: Perform quality inspections on deliveries
- **Treasury Management Module**: Manage invoices, payments, and vendor performance

## Tech Stack

- React 19+ with TypeScript
- React Router v7 for routing
- Material-UI (MUI) for UI components
- Recharts for data visualization
- Vite for build tooling
- ESLint & Prettier for code quality

## Project Structure

```
src/
├── components/
│   ├── common/       # Shared components (DataTable, Button, Card, etc.)
│   ├── delivery/     # Delivery Acceptance module components
│   ├── qc/           # Quality Control module components
│   └── treasury/     # Treasury Management module components
├── contexts/         # React Context providers (Auth, etc.)
├── services/         # Mock data services and API simulation
├── types/            # TypeScript type definitions
├── utils/            # Utility functions
└── assets/           # Static assets (images, icons, etc.)
```

## Getting Started

### Prerequisites

- Node.js 20.19+ or 22.12+ (recommended)
- npm or yarn

### Installation

1. Clone the repository
2. Navigate to the project directory:
   ```bash
   cd rtp-overlay
   ```

3. Install dependencies:
   ```bash
   npm install
   ```

### Development

Start the development server:

```bash
npm run dev
```

The application will be available at `http://localhost:5173`

### Build

Build for production:

```bash
npm run build
```

### Lint

Run ESLint:

```bash
npm run lint
```

### Preview Production Build

Preview the production build locally:

```bash
npm run preview
```

## User Roles

The application supports three user roles:

1. **Receiving Personnel**: Access to Delivery Acceptance module
2. **QC Resource**: Access to Quality Control module
3. **Treasury Manager**: Access to Treasury Management module (Dashboard, Invoice Review, Payments Hub, Vendor Performance)

## Mock Data

The application uses mock data services to simulate backend API calls. This allows for frontend development without requiring a backend server.

## Development Guidelines

- Follow TypeScript best practices
- Use Material-UI components for consistency
- Implement responsive design (mobile-first approach)
- Ensure accessibility (WCAG 2.1 AA compliance)
- Write clean, documented code

## License

Private - Internal Use Only
