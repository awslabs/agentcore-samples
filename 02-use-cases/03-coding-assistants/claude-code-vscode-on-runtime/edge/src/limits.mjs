// A BUFFERED function URL response is at most 6 MiB of JSON, and binary bodies travel base64 encoded
// (4/3 larger). This leaves room for the envelope and headers.
export const MAX_RAW_BODY_BYTES = 4_400_000;
