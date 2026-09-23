import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { RefreshCw, Loader2, Key, CheckCircle } from "lucide-react";
import { InspectorConfig } from "@/lib/configurationTypes";
import { getMCPProxyAddress } from "@/utils/configUtils";

interface CredentialProvider {
	name: string;
	arn: string;
	type: string;
}

interface IdentityM2MAuthProps {
	config: InspectorConfig;
	onTokenObtained: (token: string) => void;
}

const IdentityM2MAuth = ({ config, onTokenObtained }: IdentityM2MAuthProps) => {
	const [providers, setProviders] = useState<CredentialProvider[]>([]);
	const [loadingProviders, setLoadingProviders] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [tokenError, setTokenError] = useState<string | null>(null);

	const [workloadName, setWorkloadName] = useState<string>(
		() => localStorage.getItem("lastWorkloadIdentityName") || "mcp-inspector",
	);
	const [workloadReady, setWorkloadReady] = useState(false);
	const [creatingWorkload, setCreatingWorkload] = useState(false);
	const [workloadError, setWorkloadError] = useState<string | null>(null);

	const [selectedProvider, setSelectedProvider] = useState<string>(
		() => localStorage.getItem("lastCredentialProvider") || "",
	);
	const [scopes, setScopes] = useState<string>(
		() => localStorage.getItem("lastM2MScopes") || "api/gateway",
	);
	const [fetchingToken, setFetchingToken] = useState(false);
	const [tokenSuccess, setTokenSuccess] = useState(false);

	useEffect(() => {
		localStorage.setItem("lastWorkloadIdentityName", workloadName);
	}, [workloadName]);

	useEffect(() => {
		if (selectedProvider)
			localStorage.setItem("lastCredentialProvider", selectedProvider);
	}, [selectedProvider]);

	useEffect(() => {
		localStorage.setItem("lastM2MScopes", scopes);
	}, [scopes]);

	const fetchProviders = useCallback(async () => {
		setLoadingProviders(true);
		try {
			const response = await fetch(
				`${getMCPProxyAddress(config)}/identity/credential-providers`,
			);
			const data = await response.json();
			if (data.error) setError(data.error);
			setProviders(data.providers ?? []);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setLoadingProviders(false);
		}
	}, [config]);

	useEffect(() => {
		fetchProviders();
	}, [fetchProviders]);

	const handleEnsureWorkload = async () => {
		if (!workloadName.trim()) return;
		setCreatingWorkload(true);
		setWorkloadError(null);
		setWorkloadReady(false);
		try {
			const response = await fetch(
				`${getMCPProxyAddress(config)}/identity/ensure-workload`,
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ name: workloadName.trim() }),
				},
			);
			const data = await response.json();
			if (data.error) {
				setWorkloadError(data.error);
			} else {
				setWorkloadReady(true);
			}
		} catch (err) {
			setWorkloadError(err instanceof Error ? err.message : String(err));
		} finally {
			setCreatingWorkload(false);
		}
	};

	const handleGetToken = async () => {
		if (!workloadName.trim() || !selectedProvider) return;
		setFetchingToken(true);
		setTokenError(null);
		setTokenSuccess(false);
		try {
			const response = await fetch(
				`${getMCPProxyAddress(config)}/identity/m2m-token`,
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						workloadName: workloadName.trim(),
						credentialProviderName: selectedProvider,
						scopes: scopes.trim() ? scopes.trim().split(/\s+/) : [],
					}),
				},
			);
			const data = await response.json();
			if (data.error) {
				setTokenError(data.error);
			} else if (data.accessToken) {
				onTokenObtained(data.accessToken);
				setTokenSuccess(true);
			} else {
				setTokenError("No access token returned");
			}
		} catch (err) {
			setTokenError(err instanceof Error ? err.message : String(err));
		} finally {
			setFetchingToken(false);
		}
	};

	return (
		<div className="space-y-3">
			<h4 className="text-sm font-semibold">AgentCore Identity (M2M)</h4>

			{error && (
				<div className="text-xs text-muted-foreground p-2 bg-destructive/10 rounded">
					{error}
				</div>
			)}

			<div className="space-y-2">
				<label className="text-sm font-medium">Workload Identity Name</label>
				<div className="flex gap-2">
					<Input
						placeholder="mcp-inspector"
						value={workloadName}
						onChange={(e) => {
							setWorkloadName(e.target.value);
							setWorkloadReady(false);
						}}
						className="font-mono text-xs"
					/>
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={handleEnsureWorkload}
						disabled={!workloadName.trim() || creatingWorkload}
						className="shrink-0"
					>
						{creatingWorkload ? (
							<Loader2 className="w-3 h-3 animate-spin mr-1" />
						) : workloadReady ? (
							<CheckCircle className="w-3 h-3 mr-1 text-green-500" />
						) : null}
						{workloadReady ? "Ready" : "Create"}
					</Button>
				</div>
				{workloadError && (
					<div className="text-xs text-destructive">{workloadError}</div>
				)}
				<p className="text-xs text-muted-foreground">
					Creates or reuses a workload identity for the inspector.
				</p>
			</div>

			<div className="space-y-2">
				<div className="flex items-center justify-between">
					<label className="text-sm font-medium">Credential Provider</label>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={fetchProviders}
						disabled={loadingProviders}
						className="h-5 w-5 p-0"
					>
						<RefreshCw
							className={`w-3 h-3 ${loadingProviders ? "animate-spin" : ""}`}
						/>
					</Button>
				</div>
				{loadingProviders && providers.length === 0 ? (
					<div className="flex items-center text-xs text-muted-foreground py-1">
						<Loader2 className="w-3 h-3 animate-spin mr-1" />
						Loading...
					</div>
				) : providers.length === 0 ? (
					<p className="text-xs text-muted-foreground">
						No credential providers found
					</p>
				) : (
					<Select value={selectedProvider} onValueChange={setSelectedProvider}>
						<SelectTrigger className="font-mono text-xs">
							<SelectValue placeholder="Select credential provider" />
						</SelectTrigger>
						<SelectContent>
							{providers.map((cp) => (
								<SelectItem key={cp.arn} value={cp.name}>
									{cp.name}
									{cp.type && (
										<span className="text-muted-foreground ml-1">
											({cp.type})
										</span>
									)}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				)}
			</div>

			<div className="space-y-2">
				<label className="text-sm font-medium">Scopes</label>
				<Input
					placeholder="api/gateway"
					value={scopes}
					onChange={(e) => setScopes(e.target.value)}
					className="font-mono text-xs"
				/>
				<p className="text-[10px] text-muted-foreground">
					Must match scopes configured on the credential provider's OAuth
					server.
				</p>
			</div>

			<Button
				className="w-full"
				size="sm"
				onClick={handleGetToken}
				disabled={!workloadReady || !selectedProvider || fetchingToken}
			>
				{fetchingToken ? (
					<Loader2 className="w-4 h-4 mr-2 animate-spin" />
				) : (
					<Key className="w-4 h-4 mr-2" />
				)}
				Get Token
			</Button>

			{tokenSuccess && (
				<div className="text-xs text-green-700 dark:text-green-300 p-2 bg-green-50 dark:bg-green-900/30 rounded">
					Token obtained and set as Authorization header.
				</div>
			)}

			{tokenError && (
				<div className="text-xs text-destructive p-2 bg-destructive/10 rounded">
					{tokenError}
				</div>
			)}
		</div>
	);
};

export default IdentityM2MAuth;
