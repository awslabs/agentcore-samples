import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: 'unit',
          include: ['packages/*/test/**/*.test.ts', 'agents/*/test/**/*.test.ts'],
        },
      },
      {
        test: {
          name: 'integration',
          include: ['tests/**/*.test.ts'],
          // The integration test drives three agent processes plus the mock
          // tool end-to-end through real Bedrock model calls.
          testTimeout: 600_000,
          hookTimeout: 120_000,
        },
      },
    ],
  },
});
