import { Button } from "@/components/ui/button";
import {
  PendingUrlElicitationRequest,
  ElicitationResponse,
} from "./ElicitationTab";

export type UrlElicitationRequestProps = {
  request: PendingUrlElicitationRequest;
  onResolve: (id: number, response: ElicitationResponse) => void;
};

const UrlElicitationRequest = ({
  request,
  onResolve,
}: UrlElicitationRequestProps) => {
  const handleAccept = () => {
    // Save current URL so we can restore it after the OAuth callback
    sessionStorage.setItem("mcp_pre_auth_url", window.location.href);
    window.open(request.request.url, "_blank", "noopener,noreferrer");
    onResolve(request.id, { action: "accept" });
  };

  const handleDecline = () => {
    onResolve(request.id, { action: "decline" });
  };

  const handleCancel = () => {
    onResolve(request.id, { action: "cancel" });
  };

  const urlHost = (() => {
    try {
      return new URL(request.request.url).hostname;
    } catch {
      return request.request.url;
    }
  })();

  return (
    <div
      data-testid="url-elicitation-request"
      className="flex flex-col gap-4 p-4 border rounded-lg"
    >
      <div className="flex items-center gap-2">
        <span className="px-2 py-1 text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 rounded">
          URL Mode
        </span>
        <span className="text-xs text-muted-foreground">
          Elicitation ID: {request.request.elicitationId}
        </span>
      </div>

      <p className="text-sm">{request.request.message}</p>

      <div className="bg-gray-50 dark:bg-gray-800 p-3 rounded">
        <p className="text-xs font-medium text-muted-foreground mb-1">
          You will be directed to:
        </p>
        <p className="text-sm font-semibold text-blue-600 dark:text-blue-400">
          {urlHost}
        </p>
        <p className="text-xs text-muted-foreground mt-1 break-all">
          {request.request.url}
        </p>
      </div>

      <div className="flex space-x-2">
        <Button type="button" onClick={handleAccept}>
          Open URL
        </Button>
        <Button type="button" variant="outline" onClick={handleDecline}>
          Decline
        </Button>
        <Button type="button" variant="outline" onClick={handleCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
};

export default UrlElicitationRequest;
