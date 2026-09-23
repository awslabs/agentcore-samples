import { Alert, AlertDescription } from "@/components/ui/alert";
import { TabsContent } from "@/components/ui/tabs";
import { JsonSchemaType } from "@/utils/jsonUtils";
import ElicitationRequest from "./ElicitationRequest";
import UrlElicitationRequest from "./UrlElicitationRequest";

export interface ElicitationRequestData {
  id: number;
  message: string;
  requestedSchema: JsonSchemaType;
}

export interface ElicitationResponse {
  action: "accept" | "decline" | "cancel";
  content?: Record<string, unknown>;
}

export type PendingElicitationRequest = {
  id: number;
  request: ElicitationRequestData;
  originatingTab?: string;
};

export interface UrlElicitationData {
  id: number;
  elicitationId: string;
  url: string;
  message: string;
}

export type PendingUrlElicitationRequest = {
  id: number;
  request: UrlElicitationData;
  originatingTab?: string;
};

export type Props = {
  pendingRequests: PendingElicitationRequest[];
  pendingUrlRequests: PendingUrlElicitationRequest[];
  onResolve: (id: number, response: ElicitationResponse) => void;
  onUrlResolve: (id: number, response: ElicitationResponse) => void;
};

const ElicitationTab = ({
  pendingRequests,
  pendingUrlRequests,
  onResolve,
  onUrlResolve,
}: Props) => {
  return (
    <TabsContent value="elicitations">
      <div className="h-96 overflow-y-auto">
        <Alert>
          <AlertDescription>
            When the server requests information from the user, requests will
            appear here for response. URL elicitation requests require you to
            open a URL in your browser to complete an out-of-band interaction.
          </AlertDescription>
        </Alert>
        <div className="mt-4 space-y-4">
          <h3 className="text-lg font-semibold">Recent Requests</h3>
          {pendingUrlRequests.map((request) => (
            <UrlElicitationRequest
              key={`url-${request.id}`}
              request={request}
              onResolve={onUrlResolve}
            />
          ))}
          {pendingRequests.map((request) => (
            <ElicitationRequest
              key={request.id}
              request={request}
              onResolve={onResolve}
            />
          ))}
          {pendingRequests.length === 0 &&
            pendingUrlRequests.length === 0 && (
              <p className="text-gray-500">No pending requests</p>
            )}
        </div>
      </div>
    </TabsContent>
  );
};

export default ElicitationTab;
