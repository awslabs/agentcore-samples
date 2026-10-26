import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { RefreshCw, Loader2 } from "lucide-react";
import { InspectorConfig } from "@/lib/configurationTypes";
import { getMCPProxyAddress } from "@/utils/configUtils";

interface Gateway {
	gatewayId: string;
	name: string;
	status: string;
	description?: string;
	protocolType?: string;
}

interface GatewaySelectorProps {
	selectedGatewayId: string | null;
	onSelect: (gatewayId: string, gatewayUrl: string) => void;
	config: InspectorConfig;
}

const GatewaySelector = ({
	selectedGatewayId,
	onSelect,
	config,
}: GatewaySelectorProps) => {
	const [gateways, setGateways] = useState<Gateway[]>([]);
	const [region, setRegion] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);
	const [resolving, setResolving] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);

	const fetchGateways = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const proxyAddress = getMCPProxyAddress(config);
			const response = await fetch(`${proxyAddress}/gateways`);
			if (!response.ok) {
				const text = await response.text();
				setError(`HTTP ${response.status}: ${text}`);
				setGateways([]);
				return;
			}
			const data = await response.json();
			if (data.error) {
				setError(data.error);
			}
			setGateways(data.gateways ?? []);
			if (data.region) {
				setRegion(data.region);
			}
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
			setGateways([]);
		} finally {
			setLoading(false);
		}
	}, [config]);

	useEffect(() => {
		fetchGateways();
	}, [fetchGateways]);

	const handleSelect = async (gw: Gateway) => {
		setResolving(gw.gatewayId);
		try {
			const proxyAddress = getMCPProxyAddress(config);
			const response = await fetch(
				`${proxyAddress}/gateways?gatewayId=${encodeURIComponent(gw.gatewayId)}`,
			);
			const data = await response.json();
			if (data.gatewayUrl) {
				const url = data.gatewayUrl.endsWith("/mcp")
					? data.gatewayUrl
					: `${data.gatewayUrl.replace(/\/$/, "")}/mcp`;
				onSelect(gw.gatewayId, url);
			} else {
				setError(data.error || `Could not resolve URL for ${gw.name}`);
			}
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setResolving(null);
		}
	};

	return (
		<div className="space-y-2">
			<div className="flex items-center justify-between">
				<label className="text-sm font-medium">
					AgentCore Gateways
					{region && (
						<span className="ml-1 text-xs text-muted-foreground font-normal">
							({region})
						</span>
					)}
				</label>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={fetchGateways}
					disabled={loading}
					className="h-6 w-6 p-0"
				>
					<RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
				</Button>
			</div>

			{loading && gateways.length === 0 ? (
				<div className="flex items-center justify-center py-4 text-muted-foreground">
					<Loader2 className="w-4 h-4 animate-spin mr-2" />
					<span className="text-xs">Loading gateways...</span>
				</div>
			) : error && gateways.length === 0 ? (
				<div className="text-xs text-muted-foreground py-2">
					Could not list gateways: {error}
				</div>
			) : gateways.length === 0 ? (
				<div className="text-xs text-muted-foreground py-2">
					No gateways found
				</div>
			) : (
				<div className="max-h-[200px] overflow-y-auto border rounded-md">
					{gateways.map((gw) => (
						<label
							key={gw.gatewayId}
							className={`flex items-start gap-2 p-2 cursor-pointer hover:bg-accent/50 border-b last:border-b-0 ${
								selectedGatewayId === gw.gatewayId ? "bg-accent/30" : ""
							}`}
						>
							<input
								type="radio"
								name="gateway-selector"
								checked={selectedGatewayId === gw.gatewayId}
								onChange={() => handleSelect(gw)}
								disabled={resolving === gw.gatewayId}
								className="mt-1 shrink-0"
							/>
							<div className="min-w-0 flex-1">
								<div className="flex items-center gap-2">
									<span className="text-sm font-medium truncate">
										{gw.name}
									</span>
									<span
										className={`text-[10px] px-1.5 py-0.5 rounded-full shrink-0 ${
											gw.status === "READY"
												? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
												: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
										}`}
									>
										{gw.status}
									</span>
									{resolving === gw.gatewayId && (
										<Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
									)}
								</div>
							</div>
						</label>
					))}
				</div>
			)}

			{error && gateways.length > 0 && (
				<div className="text-xs text-destructive">{error}</div>
			)}
		</div>
	);
};

export default GatewaySelector;
