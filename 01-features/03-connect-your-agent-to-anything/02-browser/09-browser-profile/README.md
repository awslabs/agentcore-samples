# AgentCore Browser — Browser Profile Persistence

| Information         | Details                                                                       |
|:--------------------|:------------------------------------------------------------------------------|
| Tutorial type       | Feature demonstration                                                         |
| Agent type          | Direct SDK (Playwright) — no LLM agent                                        |
| Agentic Framework   | Playwright (CDP)                                                              |
| LLM model           | None                                                                          |
| Tutorial components | AgentCore Browser, Browser Profiles, S3 Recording, Playwright                |
| Example complexity  | Intermediate                                                                  |

## Overview

A **Browser Profile** stores browser session state — cookies, localStorage, and session storage —
so that data set in one session is available in a subsequent session. This is useful when agents
need to remember login state, shopping carts, preferences, or any other client-side data across
multiple invocations.

This demo:
1. Deploys a sample CloudFront e-commerce site
2. **Session A** — adds two items to the shopping cart and saves the session to a profile
3. **Session B** — starts a brand-new session loaded from the saved profile and verifies the cart still has the selected items

## Key Concepts

- **`create_browser_profile(name=...)`** — creates a profile resource on the control plane
- **`save_browser_session_profile(profileIdentifier, browserIdentifier, sessionId)`** — captures the current browser state into the profile
- **`start_browser_session(profileConfiguration={"profileIdentifier": profile_id})`** — loads saved state into a new session at startup

## Architecture

```
Session A:
  Browser session ──▶ e-commerce site ──▶ add items to cart
                                        ──▶ save_browser_session_profile()

         Profile (cookies + localStorage)
                   │
                   ▼

Session B:
  start_browser_session(profileConfiguration=...)
  ──▶ e-commerce site ──▶ cart already populated (state persisted)
```

## Setup

### Deploy the sample e-commerce site

```bash
cd sample-ecommerce
bash deploy.sh
# Note the CloudFront URL from the output
```

### Run the demo

```bash
pip install -r ../requirements.txt
playwright install chromium

export CFN_URL=https://xxxx.cloudfront.net
python browser_profile.py --cfn-url $CFN_URL --region us-east-1
```

## Sample Interactions

**Session A actions:**
- Navigate to `{CFN_URL}/#home`
- Click "Add to Cart" on items 2 and 4
- Call `save_browser_session_profile()` to snapshot the state

**Session B verification:**
- Start session with `profileConfiguration` pointing to the saved profile
- Navigate to `{CFN_URL}/#home`
- Open cart — should show the 2 previously selected items

## Troubleshooting

### Cart is empty in Session B
**Issue**: The profile was not saved before Session A ended, or the `save_browser_session_profile` call failed.
**Solution**: Check the script output for "Profile saved successfully". Ensure the IAM role has `bedrock-agentcore:SaveBrowserSessionProfile` permission on the profile ARN.

### CloudFront URL not found
**Issue**: The sample e-commerce CloudFormation stack was not deployed, or the URL was not set.
**Solution**: Run `cd sample-ecommerce && bash deploy.sh` and set `CFN_URL` to the CloudFront URL from the output.

### Browser profile API not available
**Issue**: Browser profiles may not be available in all regions.
**Solution**: Check the [AgentCore service availability page](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html) for supported regions.

## Clean Up

```bash
# Delete the browser profile and custom browser
python browser_profile.py --cfn-url $CFN_URL --region us-east-1  # runs cleanup automatically

# Delete the e-commerce sample stack
cd sample-ecommerce && bash delete.sh
```

## Running the Python Script

```bash
pip install -r ../requirements.txt
playwright install chromium

# Deploy e-commerce first, then:
python browser_profile.py --cfn-url https://xxxx.cloudfront.net

# Skip resource cleanup to inspect the browser/profile in the console
python browser_profile.py --cfn-url https://xxxx.cloudfront.net --skip-cleanup
```

## Files

| File | Description |
|:-----|:------------|
| `browser_profile.py` | Main demo script |
| `sample-ecommerce/` | Sample CloudFront-hosted e-commerce site |
| `sample-ecommerce/cloudformation.yaml` | CloudFormation template for the e-commerce site |
| `sample-ecommerce/deploy.sh` | Deploy script |
| `sample-ecommerce/delete.sh` | Cleanup script |
| `img/cfn_outputs.png` | Screenshot of CloudFormation outputs |

## Session Recording and rrweb Replay (Optional)

When the browser is created with `recording={"enabled": True, "s3Location": {...}}`, session recordings are automatically saved to S3 as gzip-compressed rrweb event files.

### Download and decompress

```python
import boto3, gzip, json

s3 = boto3.client("s3")
BUCKET_NAME = "your-bucket"

# List recordings for a session
response = s3.list_objects_v2(
    Bucket=BUCKET_NAME, Prefix=f"browser_recordings/{session_id}"
)
for obj in response.get("Contents", []):
    key = obj["Key"]
    if key.endswith(".gz"):
        filename = key.split("/")[-1]
        s3.download_file(BUCKET_NAME, key, filename)
        print(f"Downloaded: {filename}")

# Decompress — each line is a JSON rrweb event
events = []
with gzip.open(filename, "rt") as f:
    for line in f:
        line = line.strip()
        if line:
            events.append(json.loads(line))

with open("events.json", "w") as f:
    json.dump(events, f)
```

### Replay with rrweb-player

Save the following as an HTML file and open it in a browser:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/rrweb-player@latest/dist/style.css"/>
<div id="player"></div>
<script src="https://cdn.jsdelivr.net/npm/rrweb-player@latest/dist/index.js"></script>
<script>
    fetch("events.json")
        .then(r => r.json())
        .then(events => new rrwebPlayer({
            target: document.getElementById("player"),
            props: { events }
        }));
</script>
```

## Further Reading

- [AgentCore Browser Profiles documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
- [Playwright CDP connection](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp)
