# Customer Support Assistant Frontend

## Overview

This is a single-page application that provides a chat interface for interacting with Customer Support Assistant. The app handles OAuth authentication via AWS Cognito, streams responses from the agent in real-time, and displays the complete agentic workflow including tool invocations and their results.

## Getting Started

### Prerequisites

- Node.js 18 or higher, use [documentation](https://nodejs.org/en/download).

### Installation

```bash
cd frontend
npm install
```

### Configuration

Create a `.env` file in this directory:

```bash
chmod +x ./setup-env.sh
./setup-env.sh
```

### Running Locally

```bash
npm run dev
```

The app will be available at `http://localhost:5173`
