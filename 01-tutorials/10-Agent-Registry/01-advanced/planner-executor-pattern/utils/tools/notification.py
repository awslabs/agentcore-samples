import uuid

import db

def lambda_handler(event, context):
    tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "___" in tool_name:
        tool_name = tool_name.split("___", 1)[1]

    if tool_name == "send_email":
        to      = event.get("to", "")
        subject = event.get("subject", "")
        body    = event.get("body", "")
        tmpl_id = event.get("template_id", "")
        if tmpl_id:
            tmpl = db.get_item("TEMPLATES_TABLE", {"template_id": tmpl_id})
            if tmpl:
                vars_ = event.get("template_vars", {})
                subject = subject or tmpl["subject"].format_map(vars_)
                body    = body    or tmpl["body"].format_map(vars_)
        return {"message_id": str(uuid.uuid4())[:8], "to": to, "subject": subject,
                "status": "DELIVERED", "note": "[MOCK] Email not actually sent"}

    elif tool_name == "send_bulk_email":
        recipients = event.get("recipients", [])
        subject    = event.get("subject", "(no subject)")
        return {"sent_count": len(recipients),
                "results": [{"to": r, "status": "DELIVERED"} for r in recipients],
                "note": "[MOCK] Emails not actually sent"}

    elif tool_name == "get_template":
        tmpl_id = event.get("template_id", "")
        tmpl    = db.get_item("TEMPLATES_TABLE", {"template_id": tmpl_id})
        return tmpl if tmpl else {"error": f"Template '{tmpl_id}' not found"}

    elif tool_name == "list_templates":
        return {"templates": db.scan_all("TEMPLATES_TABLE")}

    elif tool_name == "create_template":
        tmpl_id = event.get("template_id", str(uuid.uuid4())[:8])
        item    = {"template_id": tmpl_id,
                   "subject": event.get("subject", ""),
                   "body":    event.get("body", "")}
        db.put_item("TEMPLATES_TABLE", item)
        return {"created": True, "template": item}

    elif tool_name == "send_sms":
        return {"message_id": str(uuid.uuid4())[:8], "to": event.get("to", ""),
                "status": "DELIVERED", "note": "[MOCK] SMS not actually sent"}

    elif tool_name == "send_bulk_sms":
        recipients = event.get("recipients", [])
        return {"sent_count": len(recipients),
                "results": [{"to": r, "status": "DELIVERED"} for r in recipients],
                "note": "[MOCK] SMS not actually sent"}

    return {"error": f"Unknown tool: {tool_name}"}
