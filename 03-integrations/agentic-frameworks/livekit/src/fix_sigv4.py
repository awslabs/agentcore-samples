file_path = "/usr/local/lib/python3.12/site-packages/livekit/plugins/aws/experimental/realtime/realtime_model.py"

with open(file_path, "r") as f:
    content = f.read()

# Fix Config parameter names for smithy 0.1.0
content = content.replace(
    "http_auth_scheme_resolver=HTTPAuthSchemeResolver(),",
    "auth_scheme_resolver=HTTPAuthSchemeResolver(),",
)
content = content.replace(
    'http_auth_schemes={"aws.auth#sigv4": SigV4AuthScheme()},',
    'auth_schemes={"aws.auth#sigv4": SigV4AuthScheme(service="bedrock")},',
)

with open(file_path, "w") as f:
    f.write(content)

print("Patched Config parameters")
