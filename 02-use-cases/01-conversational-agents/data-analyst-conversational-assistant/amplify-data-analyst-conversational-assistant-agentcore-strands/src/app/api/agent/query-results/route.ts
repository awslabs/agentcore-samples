import { NextResponse } from 'next/server';

// Results are now read through the Runtime, which verifies Cognito identity and
// derives the partition key itself. Do not restore caller-selected table access.
export async function POST() {
  return NextResponse.json({ error: 'Use the authenticated Runtime query_results operation' }, { status: 410 });
}
