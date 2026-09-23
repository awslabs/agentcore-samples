import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Plus, Trash2, Eye, EyeOff } from "lucide-react";
import {
	CustomHeaders as CustomHeadersType,
	CustomHeader,
	createEmptyHeader,
} from "@/lib/types/customHeaders";

interface CustomHeadersProps {
	headers: CustomHeadersType;
	onChange: (headers: CustomHeadersType) => void;
	className?: string;
	hideAuthorizationHeader?: boolean;
}

const isAuthorizationHeader = (header: CustomHeader): boolean =>
	header.name.toLowerCase() === "authorization" &&
	header.value.startsWith("Bearer ");

const CustomHeaders = ({
	headers,
	onChange,
	className,
	hideAuthorizationHeader = false,
}: CustomHeadersProps) => {
	const [visibleValues, setVisibleValues] = useState<Set<number>>(new Set());

	const updateHeader = (
		index: number,
		field: keyof CustomHeader,
		value: string | boolean,
	) => {
		const header = headers[index];
		if (isAuthorizationHeader(header)) {
			if (field === "name") return;
			if (
				field === "value" &&
				typeof value === "string" &&
				!value.startsWith("Bearer ")
			)
				return;
		}
		const newHeaders = [...headers];
		newHeaders[index] = { ...newHeaders[index], [field]: value };
		onChange(newHeaders);
	};

	const addHeader = () => {
		onChange([...headers, createEmptyHeader()]);
	};

	const removeHeader = (index: number) => {
		if (isAuthorizationHeader(headers[index])) return;
		const newHeaders = headers.filter((_, i) => i !== index);
		onChange(newHeaders);
	};

	const toggleValueVisibility = (index: number) => {
		const newVisible = new Set(visibleValues);
		if (newVisible.has(index)) {
			newVisible.delete(index);
		} else {
			newVisible.add(index);
		}
		setVisibleValues(newVisible);
	};

	return (
		<div className={`space-y-3 ${className}`}>
			<div className="flex justify-between items-center gap-2">
				<h4 className="text-sm font-semibold flex-shrink-0">Custom Headers</h4>
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={addHeader}
					className="text-xs px-2"
					data-testid="add-header-button"
				>
					<Plus className="w-3 h-3 mr-1" />
					Add
				</Button>
			</div>

			{headers.filter(
				(h) => !(hideAuthorizationHeader && isAuthorizationHeader(h)),
			).length === 0 ? (
				<div className="text-center py-4 text-muted-foreground">
					<p className="text-sm">No custom headers configured</p>
					<p className="text-xs mt-1">Click "Add" to get started</p>
				</div>
			) : (
				<div className="space-y-2 max-h-[300px] overflow-y-auto">
					{headers.map((header, index) => {
						if (hideAuthorizationHeader && isAuthorizationHeader(header))
							return null;
						return (
							<div
								key={index}
								className="flex items-start gap-2 p-2 border rounded-md"
							>
								<Switch
									checked={header.enabled}
									onCheckedChange={(enabled) =>
										updateHeader(index, "enabled", enabled)
									}
									className="shrink-0 mt-2"
								/>
								<div className="flex-1 min-w-0 space-y-2">
									<Input
										placeholder="Header Name"
										value={header.name}
										onChange={(e) =>
											updateHeader(index, "name", e.target.value)
										}
										className="font-mono text-xs"
										data-testid={`header-name-input-${index}`}
										disabled={isAuthorizationHeader(header)}
									/>
									<div className="relative">
										{isAuthorizationHeader(header) ? (
											<div className="flex gap-1">
												<Input
													value="Bearer"
													disabled
													className="font-mono text-xs w-[4.5rem] shrink-0"
												/>
												<Input
													placeholder="<your-token>"
													value={header.value.replace("Bearer ", "")}
													onChange={(e) =>
														updateHeader(
															index,
															"value",
															`Bearer ${e.target.value}`,
														)
													}
													type={visibleValues.has(index) ? "text" : "password"}
													className="font-mono text-xs pr-8"
													data-testid={`header-value-input-${index}`}
												/>
											</div>
										) : (
											<Input
												placeholder="Header Value"
												value={header.value}
												onChange={(e) =>
													updateHeader(index, "value", e.target.value)
												}
												type={visibleValues.has(index) ? "text" : "password"}
												className="font-mono text-xs pr-8"
												data-testid={`header-value-input-${index}`}
											/>
										)}
										<Button
											type="button"
											variant="ghost"
											size="sm"
											onClick={() => toggleValueVisibility(index)}
											className="absolute right-1 top-1/2 -translate-y-1/2 h-6 w-6 p-0"
										>
											{visibleValues.has(index) ? (
												<EyeOff className="w-3 h-3" />
											) : (
												<Eye className="w-3 h-3" />
											)}
										</Button>
									</div>
								</div>
								{!isAuthorizationHeader(header) && (
									<Button
										type="button"
										variant="ghost"
										size="sm"
										onClick={() => removeHeader(index)}
										className="shrink-0 text-red-600 hover:text-red-700 hover:bg-red-50 h-6 w-6 p-0 mt-2"
									>
										<Trash2 className="w-3 h-3" />
									</Button>
								)}
							</div>
						);
					})}
				</div>
			)}

			{headers.length > 0 && (
				<p className="text-xs text-muted-foreground">
					Use the toggle to enable/disable headers. Only enabled headers with
					both name and value will be sent.
				</p>
			)}
		</div>
	);
};

export default CustomHeaders;
