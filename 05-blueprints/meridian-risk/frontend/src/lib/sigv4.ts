// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * SigV4 request signing in the browser, using Web Crypto.
 *
 * Needed because this account blocks Lambda Function URLs with
 * `authorization_type = NONE` — unsigned requests are rejected with 403 before
 * reaching the function. The console therefore federates its Cognito ID token
 * into temporary IAM credentials (see credentials.ts) and signs each call.
 *
 * Implemented directly rather than via @aws-sdk/signature-v4 because that adds
 * a substantial dependency tree for one algorithm, and the AWS SDK's fetch
 * handler buffers responses — which would defeat SSE streaming.
 */

export interface AwsCredentials {
  accessKeyId: string
  secretAccessKey: string
  sessionToken: string
}

const encoder = new TextEncoder()

async function sha256Hex(data: string | ArrayBuffer): Promise<string> {
  const buffer =
    typeof data === "string" ? encoder.encode(data) : new Uint8Array(data)
  const digest = await crypto.subtle.digest("SHA-256", buffer)
  return hex(digest)
}

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
}

async function hmac(
  key: ArrayBuffer | Uint8Array,
  message: string
): Promise<ArrayBuffer> {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    key as ArrayBuffer,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  )
  return crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(message))
}

/** Derive the date/region/service scoped signing key. */
async function signingKey(
  secret: string,
  date: string,
  region: string,
  service: string
): Promise<ArrayBuffer> {
  let key: ArrayBuffer | Uint8Array = encoder.encode(`AWS4${secret}`)
  for (const part of [date, region, service, "aws4_request"]) {
    key = await hmac(key, part)
  }
  return key as ArrayBuffer
}

/**
 * Sign a request and return the headers to attach.
 *
 * @param method HTTP method, uppercase.
 * @param url Absolute request URL.
 * @param body Request body, or "" for GET.
 */
export async function signRequest(
  method: string,
  url: string,
  body: string,
  credentials: AwsCredentials,
  region: string,
  service = "lambda"
): Promise<Record<string, string>> {
  const target = new URL(url)

  // ISO8601 basic format: 20260803T171200Z
  const amzDate = new Date().toISOString().replace(/[:-]|\.\d{3}/g, "")
  const dateStamp = amzDate.slice(0, 8)

  const payloadHash = await sha256Hex(body)

  // Sign the minimum set: host, date, and the session token. Every signed
  // header must be reproduced byte-for-byte by the caller, and the browser
  // forbids setting `host` (the fetch stack supplies it), so keeping this set
  // small avoids mismatches. This mirrors what botocore signs.
  //
  // Notably x-amz-content-sha256 is NOT signed: including it here while the
  // browser normalizes or omits the outgoing header yields a signature the
  // service cannot reproduce, and the request is rejected with 403.
  const headers: Record<string, string> = {
    host: target.host,
    "x-amz-date": amzDate,
    "x-amz-security-token": credentials.sessionToken,
  }
  const signedHeaders = Object.keys(headers).sort()

  const canonicalHeaders =
    signedHeaders.map((name) => `${name}:${headers[name]}\n`).join("") // trailing \n per header

  // Query parameters must be sorted by name, and both name and value
  // RFC3986-encoded.
  const canonicalQuery = [...target.searchParams.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(
      ([name, value]) =>
        `${encodeURIComponent(name)}=${encodeURIComponent(value)}`
    )
    .join("&")

  const canonicalRequest = [
    method.toUpperCase(),
    target.pathname || "/",
    canonicalQuery,
    canonicalHeaders,
    signedHeaders.join(";"),
    payloadHash,
  ].join("\n")

  const scope = `${dateStamp}/${region}/${service}/aws4_request`
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    scope,
    await sha256Hex(canonicalRequest),
  ].join("\n")

  const key = await signingKey(
    credentials.secretAccessKey,
    dateStamp,
    region,
    service
  )
  const signature = hex(await hmac(key, stringToSign))

  return {
    "X-Amz-Date": amzDate,
    "X-Amz-Security-Token": credentials.sessionToken,
    Authorization:
      `AWS4-HMAC-SHA256 Credential=${credentials.accessKeyId}/${scope}, ` +
      `SignedHeaders=${signedHeaders.join(";")}, Signature=${signature}`,
  }
}
